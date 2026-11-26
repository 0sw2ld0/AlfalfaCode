"""Piloto del manual de pantallas bajo demanda: ¿basta la línea corta con `ui_manual` o hace falta el manual entero?

    python scripts/piloto_manual_ui.py --salida resultados.jsonl [--repeticiones 2] [--tope 1.5]
    python scripts/piloto_manual_ui.py --resumen resultados.jsonl

Dos brazos, con los proveedores de AlfalfaCode: por defecto el plan de ChatGPT (`--proveedor chatgpt`, GPT-6 Luna;
la sesión la conecta `alfalfa --conectar chatgpt`) o el servicio Alfalfa (`--proveedor alfalfa`, también Claude
Haiku 5.5; el código CUY lo lee AlfalfaCode del Llavero). Nada de claves directas de OpenAI ni de Anthropic. Con el
plan de ChatGPT no se paga por petición: el coste es el que tendría con los precios de la API, para comparar.

- `completo`: el prompt de hoy, con `Catalogo.manual(web=True)` entero en el sistema;
- `bajo_demanda`: `ALFALFA_PACAI_UI_MANUAL=1`, el sistema sólo lleva el aviso y el modelo tiene `ui_manual`.

El prompt de sistema y las herramientas son los que manda el motor de verdad: se capturan de un `TerminalManager`
con el perfil `ui` negociado (catálogo base, como la app de Mac), en una carpeta vacía y sin memoria, sustituyendo
sólo la inferencia. Luego cada pregunta se juega como un turno del agente, con el esfuerzo de la sesión por defecto
(medium) y hasta `PASOS` inferencias: `ui_manual` devuelve el manual de verdad (y el aviso corto si se repite),
`web_search` y `read_web` resultados fijos por pregunta, y el resto de herramientas «no disponible en esta medición».

Preguntas: 12 donde conviene una pantalla sin que se pida (listas, comparativas, planes, un formulario, un gráfico y
tres que piden buscar) y 8 de código, donde una pantalla está fuera de lugar. Por turno: si dibuja, si la primera
pantalla pasa el catálogo y el árbol sin un solo error, si llamó a ui_manual, tokens, coste y el tiempo hasta la
respuesta que trae la primera pantalla (el servicio entrega cada respuesta de una vez).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

import httpx  # noqa: E402

from alfalfa.pacai.ui import AVISO_MANUAL, FLAG_MANUAL, Catalogo, Vistas, partir  # noqa: E402

MODELOS = ('claude-haiku-5-5', 'gpt-6-luna')
BRAZOS = ('completo', 'bajo_demanda')
PASOS = 6
MAX_SALIDA = 4000
ESFUERZO = 'medium'

# (id, tipo, pregunta). `conviene`: una pantalla ayuda y nadie la pide. `codigo`: la respuesta es texto y código.
PREGUNTAS = [
    ('c_lenguajes', 'conviene', 'Compara Python, Go y Rust para escribir una herramienta de línea de órdenes: '
                                'rendimiento, curva de aprendizaje y cómo se distribuye.'),
    ('c_mudanza', 'conviene', 'Me mudo de piso el mes que viene. ¿Qué tengo que hacer y en qué orden?'),
    ('c_hosting', 'conviene', '¿Qué opciones tengo para alojar una app web pequeña con base de datos? Precio '
                              'aproximado, pros y contras.'),
    ('c_ingles', 'conviene', 'Quiero estudiar inglés. Ayúdame a elegir entre una academia presencial, clases online '
                             'en directo o una app.'),
    ('c_semana', 'conviene', 'Organízame la semana: el lunes entrego un informe, el miércoles tengo una presentación '
                             'y el viernes una revisión de código, y quiero ir al gimnasio tres días.'),
    ('c_vacaciones', 'conviene', 'Tengo que pedirle vacaciones a mi jefa. Prepárame lo que tengo que rellenar: '
                                 'fechas, motivo y quién me cubre.'),
    ('c_poblacion', 'conviene', '¿Cómo ha crecido la población de Lima desde 1960?'),
    ('c_comidas', 'conviene', 'Hazme un plan de comidas vegetariano y barato para la semana.'),
    ('c_portatil', 'conviene', 'Tengo unos 1000 dólares para un portátil para programar. ¿Qué tipo de equipo me '
                               'recomiendas y qué debería mirar?'),
    ('c_noticias', 'conviene', '¿Qué noticias de tecnología hay hoy?'),
    ('c_papa', 'conviene', '¿A cuánto está hoy el kilo de papa en Lima?'),
    ('c_versiones', 'conviene', '¿Cuáles son las últimas versiones de Python, Node.js y Go y qué trae cada una?'),
    ('k_nonetype', 'codigo', "¿Qué significa este error en Python: TypeError: 'NoneType' object is not "
                             "subscriptable?"),
    ('k_palindromo', 'codigo', 'Escribe una función en Python que diga si una cadena es un palíndromo.'),
    ('k_rebase', 'codigo', '¿Qué diferencia hay entre git merge y git rebase?'),
    ('k_regex', 'codigo', r'Explícame qué hace esta expresión regular: ^[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}$'),
    ('k_invertir', 'codigo', '¿Cómo invierto una lista en JavaScript sin modificar la original?'),
    ('k_refactor', 'codigo', 'Hazlo más legible: def f(l): return [x*2 for x in l if x%2==0 and x>10]'),
    ('k_useeffect', 'codigo', '¿Por qué mi useEffect de React se ejecuta dos veces en desarrollo?'),
    ('k_deadlock', 'codigo', '¿Qué es un deadlock y cómo se evita?'),
]

# Resultados fijos de web_search y read_web: los mismos para los dos brazos, para que sólo cambie el prompt.
WEB = {
    'c_noticias': [
        ('Apple presenta chips M6 con más núcleos de IA', 'https://ejemplo-noticias.pe/tec/apple-m6',
         'Los nuevos chips prometen el doble de rendimiento en tareas de aprendizaje automático.'),
        ('La UE aprueba la ley de interoperabilidad de mensajería', 'https://ejemplo-noticias.pe/tec/ue-mensajeria',
         'WhatsApp, Telegram y Signal tendrán que poder intercambiar mensajes antes de 2028.'),
        ('Una startup peruana levanta 12 millones para pagos móviles', 'https://ejemplo-noticias.pe/tec/startup-pagos',
         'La ronda la lidera un fondo regional; la app ya tiene 800 000 usuarios.'),
    ],
    'c_papa': [
        ('Precios mayoristas: papa blanca a S/ 1,60 el kilo', 'https://ejemplo-agro.pe/precios/papa-blanca',
         'En el Gran Mercado Mayorista de Lima la papa blanca bajó 5 % frente a la semana pasada.'),
        ('Papa amarilla sube por las lluvias en la sierra', 'https://ejemplo-agro.pe/precios/papa-amarilla',
         'La papa amarilla se vende a S/ 3,80 el kilo, un 12 % más que hace un mes.'),
        ('Boletín diario de abastecimiento', 'https://ejemplo-agro.pe/boletin',
         'Ingresaron 4 200 toneladas de papa; la canchán está a S/ 1,90 el kilo.'),
    ],
    'c_versiones': [
        ('Python 3.15 ya está disponible', 'https://ejemplo-dev.pe/python-315',
         'Trae el compilador JIT activado por defecto y mejoras en los mensajes de error.'),
        ('Node.js 26 LTS: novedades', 'https://ejemplo-dev.pe/node-26',
         'Incluye soporte nativo de TypeScript sin transpilar y un nuevo sistema de permisos.'),
        ('Go 1.27 sale con iteradores genéricos mejorados', 'https://ejemplo-dev.pe/go-127',
         'También reduce un 10 % el uso de memoria del recolector de basura.'),
    ],
}


# -- el prompt de verdad -----------------------------------------------------------------------------------------
def capturar(brazo: str) -> dict:
    """El primer payload que manda el motor: sistema, herramientas y opciones, con el perfil `ui` negociado."""
    from alfalfa import terminal
    from alfalfa.proveedores.nativo.runtime import NativeRuntime
    from tests.pacai_comun import Cliente

    antes = os.environ.pop(FLAG_MANUAL, None)
    if brazo == 'bajo_demanda':
        os.environ[FLAG_MANUAL] = '1'
    carpeta, cwd = Path(tempfile.mkdtemp()), os.getcwd()
    (carpeta / 'proyecto').mkdir()
    (carpeta / 'hogar').mkdir()
    os.chdir(carpeta / 'proyecto')
    payloads = []

    async def request(rt, client, endpoint, payload, **kwargs):
        if endpoint == '/api/show':
            return {'capabilities': ['tools'], 'model_info': {'x.context_length': 131072}}
        payloads.append(json.loads(json.dumps(payload)))
        return dict(message={'role': 'assistant', 'content': 'Hecho.'}, done=True, done_reason='stop',
                    prompt_eval_count=1, eval_count=1)

    try:
        cliente = Cliente(perfiles=('work', 'ui'))
        cliente.saludar(render={'ui': {'version': '1'}})
        manager = terminal.TerminalManager('Piloto', home=carpeta / 'hogar', transport=cliente.t)

        async def correr():
            # Con buscador: el manual completo lleva la parte de buscar antes de dibujar.
            with patch.object(NativeRuntime, 'request', request), patch.object(terminal, 'excluidas', lambda: ()):
                await manager.run('hola')
        # En su propio hilo y bucle: el piloto ya corre dentro de asyncio.run.
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(1) as hilo:
            hilo.submit(asyncio.run, correr()).result()
        manager.close()
    finally:
        os.chdir(cwd)
        os.environ.pop(FLAG_MANUAL, None)
        if antes is not None:
            os.environ[FLAG_MANUAL] = antes
    payload = payloads[0]
    sistema = [m for m in payload['messages'] if m['role'] == 'system']
    return {'sistema': sistema, 'tools': payload.get('tools', []), 'options': payload.get('options', {})}


# Con --ui-lang los dos brazos llevan PACAI UI Lang 3 (ALFALFA_PACAI_UI_LANG=1): el manual compacto y su valla.
CAT = Catalogo()
MANUAL = CAT.manual().strip()                   # lo que devuelve ui_manual: buscar antes ya está en el prompt


def usar_ui_lang():
    global CAT, MANUAL
    from alfalfa.pacai import ui_lang

    os.environ[ui_lang.FLAG] = '1'
    CAT = Catalogo.de(None, {ui_lang.FLAG: '1'})
    MANUAL = CAT.manual().strip()


def herramienta(nombre, args, pregunta, mensajes):
    """Lo que el motor devolvería, sin tocar nada de verdad."""
    if nombre == 'ui_manual':
        ya = any(m.get('role') == 'tool' and m.get('tool_name') == 'ui_manual' and '"manual"' in m.get('content', '')
                 for m in mensajes)
        return ({'ya_cargado': True, 'nota': 'El manual de pantallas ya está más arriba en esta conversación: '
                                             'síguelo y dibuja la pantalla.'} if ya else {'manual': MANUAL})
    if nombre == 'web_search':
        resultados = WEB.get(pregunta[0])
        if not resultados:
            return {'error': 'Sin resultados para esa consulta.'}
        return {'query': args.get('query', ''), 'results': [
            {'title': t, 'url': u, 'snippet': s, 'date': '2026-11-26'} for t, u, s in resultados]}
    if nombre == 'read_web':
        for t, u, s in WEB.get(pregunta[0], []):
            if args.get('url') == u:
                return {'url': u, 'title': t, 'text': t + '. ' + s + ' (Texto completo del artículo, resumido.)'}
        return {'error': 'HTTP 403 al leer la página.'}
    if nombre == 'update_plan':
        return {'ok': True}
    if nombre == 'wrap_up':
        return {'mostrado': True, 'nota': 'Ahora escribe tu respuesta final como texto normal.'}
    return {'error': 'Herramienta no disponible en esta medición: responde con lo que sabes.'}


# -- pantallas ---------------------------------------------------------------------------------------------------
def _vivos(mapa):
    vivos, pila = set(), ['root']
    while pila:
        i = pila.pop()
        if i in mapa and i not in vivos:
            vivos.add(i)
            pila += mapa[i].get('children', [])
    return {i: n for i, n in mapa.items() if i in vivos}


def pantallas(textos):
    """Cada bloque ```pacai-ui de las respuestas, en orden: (índice de la respuesta, errores de ese bloque). Se
    aplican sobre las mismas `Vistas`, como el motor: un bloque posterior puede ser un cambio del anterior."""
    vistas, salida = Vistas(CAT), []
    for i, texto in enumerate(textos):
        for trozo in partir(texto or '', lang=CAT.lang):
            if trozo[0] != 'ui':
                continue
            attrs, lineas = trozo[1], trozo[2]
            data, errores = vistas.aplicar(attrs, lineas)
            vista = vistas.vistas.get(attrs.get('view', ''))
            mapa = _vivos(dict(vista.nodos) if vista else {})
            errores = list(errores)
            if data is None and not errores:
                errores.append('El bloque no cambió nada.')
            if 'root' not in mapa:
                errores.append('Falta root.')
            for ident, nodo in mapa.items():
                errores += [f'{h} no existe (hijo de {ident}).' for h in nodo.get('children', []) if h not in mapa]
            salida.append((i, errores))
    return salida


# -- un turno ----------------------------------------------------------------------------------------------------
async def inferir(cliente, modelo, mensajes, tools, options):
    for espera in (10, 20, 40, 60, None):
        t0 = time.monotonic()
        respuesta = await cliente.post('/api/chat', json={
            'model': modelo, 'messages': mensajes, 'tools': tools, 'stream': False,
            'options': dict(options, num_predict=MAX_SALIDA)})
        if respuesta.status_code != 429 or espera is None:
            break
        await asyncio.sleep(espera)
    if respuesta.status_code >= 400:
        raise RuntimeError(f'HTTP {respuesta.status_code}: {respuesta.text[:300]}')
    dato = respuesta.json()
    return (dato.get('message') or {}, dato.get('prompt_eval_count') or 0, dato.get('eval_count') or 0,
            time.monotonic() - t0)


def precio_de(modelo):
    """Los precios de la API (OpenAI o Anthropic) del modelo, aunque se use por un plan."""
    if modelo.startswith('claude-'):
        from alfalfa.proveedores.anthropic import Anthropic
        return Anthropic().precio(modelo)
    from alfalfa.proveedores.openai import OpenAI
    return OpenAI().precio(modelo)


def coste(precio, entrada, salida):
    return (entrada * precio['entrada'] + salida * precio['salida']) / 1e6


async def una(proveedor, prompts, modelo, brazo, pregunta, repeticion):
    precio = precio_de(modelo)
    prompt = prompts[brazo]
    mensajes = list(prompt['sistema']) + [{'role': 'user', 'content': pregunta[2]}]
    fila = {'proveedor': proveedor.id, 'formato': 'lang' if CAT.lang else 'json', 'modelo': modelo, 'brazo': brazo,
            'pregunta': pregunta[0], 'tipo': pregunta[1], 'repeticion': repeticion}
    textos, llamadas, entrada, salida, tiempos = [], [], 0, 0, []
    cliente = proveedor.cliente(httpx.Timeout(240, connect=10), tarea={'effort': ESFUERZO})
    async with cliente:
        for _ in range(PASOS):
            try:
                mensaje, e, s, t = await inferir(cliente, modelo, mensajes, prompt['tools'], prompt['options'])
            except Exception as exc:  # noqa: BLE001 - un fallo del servicio se anota
                fila['error'] = str(exc)[:300]
                break
            entrada, salida = entrada + e, salida + s
            tiempos.append(t)
            textos.append(mensaje.get('content') or '')
            mensajes.append({k: v for k, v in mensaje.items() if k in ('role', 'content', 'tool_calls', 'thinking')})
            calls = mensaje.get('tool_calls') or []
            if not calls:
                break
            for call in calls:
                funcion = call.get('function') or {}
                nombre, args = funcion.get('name'), funcion.get('arguments') or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except ValueError:
                        args = {}
                llamadas.append(nombre)
                resultado = herramienta(nombre, args, pregunta, mensajes)
                mensajes.append({'role': 'tool', 'tool_name': str(nombre),
                                 'content': json.dumps(resultado, ensure_ascii=False),
                                 **({'tool_call_id': call['id']} if isinstance(call.get('id'), str) else {})})
    bloques = pantallas(textos)
    primera = bloques[0] if bloques else None
    fila |= {
        'dibuja': bool(bloques), 'valida': bool(primera) and not primera[1],
        'errores': primera[1][:6] if primera else [], 'pantallas': len(bloques),
        'ui_manual': llamadas.count('ui_manual'), 'llamadas': llamadas, 'pasos': len(tiempos),
        'markdown_tabla': any('|---' in t or '| ---' in t for t in textos),
        'entrada': entrada, 'salida': salida, 'coste': coste(precio, entrada, salida) if precio else 0,
        'tiempo_total': sum(tiempos),
        'tiempo_primera_pantalla': sum(tiempos[:primera[0] + 1]) if primera else None,
        'respuesta': '\n---\n'.join(textos)[-6000:],
    }
    return fila


# -- resumen -----------------------------------------------------------------------------------------------------
def _mediana(xs):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    m = len(xs) // 2
    return xs[m] if len(xs) % 2 else (xs[m - 1] + xs[m]) / 2


def _wilson(k, n, z=1.96):
    if not n:
        return (0, 0)
    p = k / n
    centro = (p + z * z / (2 * n)) / (1 + z * z / n)
    margen = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / (1 + z * z / n)
    return (max(0, centro - margen), min(1, centro + margen))


def resumen(ruta):
    filas = [json.loads(linea) for linea in Path(ruta).read_text(encoding='utf-8').splitlines() if linea.strip()]
    errores = [f for f in filas if 'error' in f]
    filas = [f for f in filas if 'error' not in f]
    print(f'{len(filas)} turnos medidos, {len(errores)} con error del servicio; '
          f'{sum(f["pasos"] for f in filas)} peticiones; gasto {sum(f["coste"] for f in filas):.4f} USD\n')
    print('| Modelo | Brazo | Dibuja sin pedirlo (conviene) | IC95 % | Válida a la primera | Fuera de lugar (código) '
          '| Llama a ui_manual (conviene / código) | Entrada (med.) | Salida (med.) | Coste/turno (med.) '
          '| 1.ª pantalla (med.) | Peticiones (med.) |')
    print('|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|')
    for modelo in MODELOS:
        for brazo in BRAZOS:
            celda = [f for f in filas if f['modelo'] == modelo and f['brazo'] == brazo]
            if not celda:
                continue
            conv = [f for f in celda if f['tipo'] == 'conviene']
            cod = [f for f in celda if f['tipo'] == 'codigo']
            dib = [f for f in conv if f['dibuja']]
            todas = [f for f in celda if f['dibuja']]
            lo, hi = _wilson(len(dib), len(conv))
            print(f'| {modelo} | {brazo} | {len(dib)}/{len(conv)} ({len(dib) / max(1, len(conv)):.0%}) '
                  f'| {lo:.0%}–{hi:.0%} '
                  f'| {sum(f["valida"] for f in todas)}/{len(todas)} '
                  f'| {sum(f["dibuja"] for f in cod)}/{len(cod)} '
                  f'| {sum(f["ui_manual"] > 0 for f in conv)}/{len(conv)} · {sum(f["ui_manual"] > 0 for f in cod)}'
                  f'/{len(cod)} '
                  f'| {_mediana([f["entrada"] for f in celda]):.0f} | {_mediana([f["salida"] for f in celda]):.0f} '
                  f'| {1000 * _mediana([f["coste"] for f in celda]):.2f} $/1000 '
                  f'| {_mediana([f["tiempo_primera_pantalla"] for f in todas]) or 0:.1f} s '
                  f'| {_mediana([f["pasos"] for f in celda]):.0f} |')
    print('\nPor pregunta (dibuja / turnos, completo → bajo_demanda):')
    presentes = [m for m in MODELOS if any(f['modelo'] == m for f in filas)]
    for pregunta in PREGUNTAS:
        partes = []
        for modelo in presentes:
            trozos = []
            for brazo in BRAZOS:
                c = [f for f in filas if f['modelo'] == modelo and f['brazo'] == brazo and f['pregunta'] == pregunta[0]]
                trozos.append(f'{sum(f["dibuja"] for f in c)}/{len(c)}')
            partes.append(modelo + ' ' + ' → '.join(trozos))
        print(f'- {pregunta[0]} ({pregunta[1]}): ' + ' · '.join(partes))
    medias = {}
    for brazo in BRAZOS:
        celda = [f for f in filas if f['brazo'] == brazo]
        medias[brazo] = (sum(f['coste'] for f in celda) / max(1, len(celda)),
                         sum(f['entrada'] for f in celda) / max(1, len(celda)))
    print('\nMedia por turno (todos los modelos del archivo): ' + '; '.join(
        f'{b}: {1000 * c:.3f} $/1000, {e:.0f} tokens de entrada' for b, (c, e) in medias.items()))


async def main() -> None:
    args = argparse.ArgumentParser()
    args.add_argument('--salida')
    args.add_argument('--resumen')
    args.add_argument('--repeticiones', type=int, default=2)
    args.add_argument('--simultaneas', type=int, default=6)
    args.add_argument('--tope', type=float, default=1.5, help='USD: no lanza más turnos al alcanzarlo')
    args.add_argument('--brazos', default=','.join(BRAZOS))
    args.add_argument('--solo', help='preguntas separadas por comas')
    args.add_argument('--modelos', help='modelos separados por comas (por defecto, los dos)')
    args.add_argument('--primera-repeticion', type=int, default=0)
    args.add_argument('--proveedor', choices=('chatgpt', 'alfalfa'), default='chatgpt')
    args.add_argument('--ui-lang', action='store_true', help='los dos brazos con PACAI UI Lang 3')
    opciones = args.parse_args()
    if opciones.resumen:
        resumen(opciones.resumen)
        return
    if not opciones.salida:
        sys.exit('Falta --salida (o --resumen).')
    if opciones.proveedor == 'chatgpt':
        from alfalfa.proveedores.chatgpt import ChatGPT
        proveedor = ChatGPT()
    else:
        from alfalfa.proveedores.alfalfa import Alfalfa
        proveedor = Alfalfa()
    if not proveedor.configurado():
        sys.exit('El proveedor ' + opciones.proveedor + ' no está conectado en este equipo.')
    brazos = opciones.brazos.split(',')
    if opciones.ui_lang:
        usar_ui_lang()
    prompts = {b: capturar(b) for b in brazos}
    if opciones.ui_lang:
        assert all('pacai-ui-lang' in (prompts[b]['sistema'][0]['content'] if b == 'completo' else MANUAL)
                   for b in brazos)
    if 'bajo_demanda' in prompts:
        assert AVISO_MANUAL.strip() in prompts['bajo_demanda']['sistema'][0]['content']
        assert 'web_search' in prompts['bajo_demanda']['sistema'][0]['content'].split(AVISO_MANUAL.strip())[1]
        assert any(t['function']['name'] == 'ui_manual' for t in prompts['bajo_demanda']['tools'])
    preguntas = [p for p in PREGUNTAS if not opciones.solo or p[0] in opciones.solo.split(',')]
    modelos = (opciones.modelos.split(',') if opciones.modelos
               else ['gpt-6-luna'] if opciones.proveedor == 'chatgpt' else MODELOS)
    trabajos = [(m, b, p, r) for r in range(opciones.primera_repeticion,
                                            opciones.primera_repeticion + opciones.repeticiones)
                for p in preguntas for m in modelos for b in brazos]
    semaforo = asyncio.Semaphore(opciones.simultaneas)
    gastado = {'usd': 0.0, 'parado': False}
    salida = Path(opciones.salida).open('a', encoding='utf-8')

    async def correr(trabajo):
        async with semaforo:
            if gastado['usd'] >= opciones.tope:
                gastado['parado'] = True
                return
            fila = await una(proveedor, prompts, *trabajo)
            gastado['usd'] += fila.get('coste', 0)
            salida.write(json.dumps(fila, ensure_ascii=False) + '\n')
            salida.flush()

    await asyncio.gather(*(correr(t) for t in trabajos))
    print(json.dumps({'gastado_usd': round(gastado['usd'], 4), 'parado_por_tope': gastado['parado']}))


if __name__ == '__main__':
    asyncio.run(main())
