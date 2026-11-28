"""Tasa de renderizado real: dibuja cada respuesta guardada con el renderizador web de PACAI en Chrome sin ventana.

    python scripts/renderizar_ui.py docs/pacai/datos/formatos-ui-tareas.jsonl \
        docs/pacai/datos/formatos-ui-briefs-openui.jsonl docs/pacai/datos/formatos-ui-otros-modelos.jsonl \
        [--csv docs/pacai/datos/formatos-ui] [--chrome RUTA]

No gasta: sólo lee las respuestas que ya están en los `.jsonl` de `scripts/comparar_formatos_ui.py`.

Es la métrica «pantallas que se dibujan» del benchmark de OpenUI (su renderizador, sin errores y con algo en pantalla).
Para cada respuesta:

1. Se calcula la pantalla que llegaría a la app, con el motor de hoy:
   - **UI Lang y el JSON de PACAI:** si el motor acepta el bloque, sus nodos `pacai.ui/1`; si lo rechaza, no hay
     pantalla (el motor pide una corrección al modelo y la app no recibe nada).
   - **OpenUI Lang 1.0:** lo que lea su lector aunque haya errores, como hace el renderizador de OpenUI.
2. Se dibuja con `docs/demos/pacai-ui/ui.js` (el mismo que copia la extensión de VS Code) y `estilo.css`, en una página
   servida en 127.0.0.1 y abierta con Chrome `--headless`.
3. Cuenta como dibujada si no lanza ninguna excepción y lo dibujado tiene texto o controles visibles (alto mayor que 0).
   Se anota además qué parte de los nodos llegó al DOM.

Los 6 componentes de CuyMercado (Filters, Grid, Panel, Stat, PriceBars y PriceLines) son de la app, no del
renderizador base: aquí se registran con un dibujo mínimo (título, cifra o un gráfico vacío) para que no cuenten
como pantallas en blanco. Lo que se mide es el árbol, no el aspecto de esos componentes.
"""
from __future__ import annotations

import argparse
import csv
import functools
import html
import http.server
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from collections import defaultdict
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / 'scripts'))

import comparar_formatos_ui as c  # noqa: E402

DEMO = RAIZ / 'docs' / 'demos' / 'pacai-ui'
CHROME_MAC = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'

PAGINA = '''<!doctype html>
<html lang="es"><head><meta charset="utf-8"><link rel="stylesheet" href="estilo.css"></head>
<body><main id="lienzo" style="width:720px"></main>
<script type="application/json" id="pantallas">__DATOS__</script>
<script type="module">
import { Vista, registrarComponente } from './ui.js';

// Los componentes propios de CuyMercado, con un dibujo mínimo.
const rotulo = (u, clase, texto) => u.crear('div', clase, texto);
registrarComponente('Panel', (n, p, u) => { const el = u.crear('section', 'ui-card');
    el.append(u.crear('h3', 'ui-card-titulo', p.title ?? ''), u.crear('div', 'ui-hijos')); return el; },
    { hijos: true });
registrarComponente('Grid', (n, p, u) => u.crear('div', 'ui-stack ui-rejilla ui-gap-medium'), { hijos: true });
registrarComponente('Stat', (n, p, u) => { const el = u.crear('div', 'ui-card');
    el.append(u.crear('p', 'ui-card-meta', p.label ?? ''), u.crear('p', 'ui-card-titulo', '12')); return el; });
registrarComponente('Filters', (n, p, u) => { const el = u.crear('div', 'ui-stack ui-fila ui-gap-small');
    el.append(u.crear('select', 'ui-control'), u.crear('select', 'ui-control')); return el; });
for (const nombre of ['PriceBars', 'PriceLines']) registrarComponente(nombre, (n, p, u) => {
    const el = u.crear('figure', 'ui-grafico'); el.append(rotulo(u, 'ui-text', nombre)); el.style.minHeight = '80px';
    return el; });

const lienzo = document.getElementById('lienzo');
const salida = [];
for (const [clave, nodos] of JSON.parse(document.getElementById('pantallas').textContent)) {
    const fila = { clave, dibujada: false, nodos: nodos.length, en_dom: 0, alto: 0, error: null };
    try {
        const vista = new Vista('v', { alEnviar() {}, alAbrir() {}, imagenesRemotas: false });
        lienzo.replaceChildren(vista.elemento);
        vista.aplicar({ rev: 1, op: 'replace', nodes: nodos, state: {} });
        const cuerpo = [...vista.elemento.children].filter(e => !e.classList.contains('ui-barra'));
        const ids = new Set([...vista.elemento.querySelectorAll('[data-nodo]')].map(e => e.dataset.nodo));
        fila.en_dom = ids.size;
        fila.alto = Math.round(cuerpo.reduce((s, e) => s + e.getBoundingClientRect().height, 0));
        const texto = cuerpo.map(e => e.textContent).join('').trim();
        const controles = cuerpo.some(e => e.querySelector('input, select, textarea, button, svg, img, table, figure'));
        fila.dibujada = fila.alto > 0 && (texto.length > 0 || controles);
    } catch (e) {
        fila.error = String(e && e.message || e).slice(0, 200);
    }
    salida.push(fila);
}
lienzo.replaceChildren();
const pre = document.createElement('pre');
pre.id = 'resultado';
pre.textContent = JSON.stringify(salida);
document.body.replaceChildren(pre);
</script></body></html>
'''


def pantalla(r, tarea, texto):
    """Los nodos que recibiría la app, o None si el motor rechaza la respuesta."""
    if r['brazo'] == 'openui_ej':
        return list(c.validar_openui(tarea, texto)[4].values()) or None
    valida, *_ = c.validar(r['brazo'], tarea, texto)
    return list(c.nodos_finales(r['brazo'], tarea, texto).values()) if valida else None


def leer(rutas):
    filas = []
    for ruta in rutas:
        for linea in Path(ruta).read_text(encoding='utf-8').splitlines():
            r = json.loads(linea)
            if 'error' in r or 'respuesta' not in r:
                continue
            r['conjunto'] = 'openui' if 'reqs' in r else 'pacai'
            filas.append(r)
    mapa = {t[0]: t for t in c.tareas('pacai', None)}
    for r in filas:
        if r['conjunto'] == 'openui' and r['tarea'] not in mapa:
            mapa[r['tarea']] = (r['tarea'], 'alta', '', (lambda n: True), {'reqs': r['reqs'], 'band': r['band']})
    return filas, mapa


def dibujar_en_chrome(pantallas, chrome):
    """[(clave, nodos)] → {clave: resultado}. Sirve la página en 127.0.0.1 y la abre con Chrome sin ventana."""
    with tempfile.TemporaryDirectory() as carpeta:
        for nombre in ('ui.js', 'estilo.css'):
            shutil.copy(DEMO / nombre, Path(carpeta) / nombre)
        datos = json.dumps(pantallas, ensure_ascii=False).replace('</', '<\\/')
        (Path(carpeta) / 'medir.html').write_text(PAGINA.replace('__DATOS__', datos), encoding='utf-8')
        manejador = functools.partial(_Silencioso, directory=carpeta)
        servidor = http.server.ThreadingHTTPServer(('127.0.0.1', 0), manejador)
        threading.Thread(target=servidor.serve_forever, daemon=True).start()
        perfil = Path(carpeta) / 'perfil'
        try:
            salida = subprocess.run(
                [chrome, '--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
                 f'--user-data-dir={perfil}', '--virtual-time-budget=120000', '--window-size=900,1200',
                 '--dump-dom', f'http://127.0.0.1:{servidor.server_address[1]}/medir.html'],
                capture_output=True, text=True, timeout=600, check=False).stdout
        finally:
            servidor.shutdown()
    hallado = re.search(r'<pre id="resultado">(.*?)</pre>', salida, re.S)
    if not hallado:
        sys.exit('Chrome no devolvió el resultado. Salida:\n' + salida[:2000])
    return {f['clave']: f for f in json.loads(html.unescape(hallado.group(1)))}


class _Silencioso(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def medir(filas, mapa, chrome):
    """Añade a cada fila `pantalla` (hay pantalla), `dibujada` y, tras la corrección, `dibujada_tras`."""
    pantallas = []
    for i, r in enumerate(filas):
        tarea = mapa[r['tarea']]
        nodos = pantalla(r, tarea, r['respuesta'])
        r['_clave'] = f'{i}'
        r['pantalla'] = nodos is not None
        if nodos:
            pantallas.append((r['_clave'], nodos))
        if not r['pantalla'] and r.get('respuesta2'):
            nodos2 = pantalla(r, tarea, r['respuesta2'])
            if nodos2:
                pantallas.append((r['_clave'] + 'b', nodos2))
    resultados = dibujar_en_chrome(pantallas, chrome)
    for r in filas:
        f = resultados.get(r['_clave'])
        r['dibujada'] = bool(f and f['dibujada'])
        r['error_dibujo'] = f and f['error']
        r['fraccion_dom'] = f['en_dom'] / f['nodos'] if f and f['nodos'] else None
        b = resultados.get(r['_clave'] + 'b')
        r['dibujada_tras'] = r['dibujada'] or bool(b and b['dibujada'])
    return resultados


def agrupar(filas):
    grupos = defaultdict(list)
    for r in filas:
        grupos[(r['conjunto'], r['modelo'], r['brazo'])].append(r)
    salida = []
    for (conjunto, modelo, brazo), rs in sorted(grupos.items()):
        n = len(rs)
        con = [r for r in rs if r['pantalla']]
        dib = sum(r['dibujada'] for r in rs)
        fr = [r['fraccion_dom'] for r in rs if r['fraccion_dom'] is not None]
        salida.append({
            'conjunto': conjunto, 'modelo': modelo, 'formato': c.NOMBRES[brazo], 'n': n,
            'validas': sum(r['valida'] for r in rs), 'con_pantalla': len(con), 'dibujadas': dib,
            'pct_dibujadas': round(100 * dib / n, 1),
            'dibujadas_de_las_validas': sum(r['dibujada'] for r in rs if r['valida']),
            'en_blanco': sum(r['pantalla'] and not r['dibujada'] and not r['error_dibujo'] for r in rs),
            'excepciones': sum(bool(r['error_dibujo']) for r in rs),
            'dibujadas_tras': sum(r['dibujada_tras'] for r in rs),
            'pct_dibujadas_tras': round(100 * sum(r['dibujada_tras'] for r in rs) / n, 1),
            'nodos_en_dom_pct': round(100 * sum(fr) / len(fr), 1) if fr else None,
        })
    return salida


def main() -> None:
    args = argparse.ArgumentParser()
    args.add_argument('datos', nargs='+')
    args.add_argument('--csv', help='carpeta donde escribir renderizado.csv')
    args.add_argument('--chrome', default=os.environ.get('CHROME', CHROME_MAC))
    opciones = args.parse_args()
    if not Path(opciones.chrome).exists():
        sys.exit(f'No encuentro Chrome en {opciones.chrome}; pásalo con --chrome o CHROME.')
    filas, mapa = leer(opciones.datos)
    for r in filas:                       # la validez de hoy, como `informe --revalidar`
        c.revalidar(r, mapa[r['tarea']])
    medir(filas, mapa, opciones.chrome)
    tabla = agrupar(filas)
    if opciones.csv:
        ruta = Path(opciones.csv) / 'renderizado.csv'
        with ruta.open('w', encoding='utf-8', newline='') as f:
            w = csv.DictWriter(f, fieldnames=list(tabla[0]))
            w.writeheader()
            w.writerows(tabla)
    print('| Conjunto | Modelo | Formato | n | Válidas | Con pantalla | Dibujadas | En blanco | Excepciones |'
          ' Dibujadas tras corr. | Nodos en el DOM |')
    print('|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|')
    for t in tabla:
        print(f"| {t['conjunto']} | {t['modelo']} | {t['formato']} | {t['n']} | {t['validas']} | {t['con_pantalla']} |"
              f" {t['dibujadas']} ({t['pct_dibujadas']} %) | {t['en_blanco']} | {t['excepciones']} |"
              f" {t['dibujadas_tras']} ({t['pct_dibujadas_tras']} %) | {t['nodos_en_dom_pct']} % |")
    errores = sorted({r['error_dibujo'] for r in filas if r['error_dibujo']})
    if errores:
        print('\nExcepciones del renderizador:', *errores[:10], sep='\n- ')


if __name__ == '__main__':
    main()
