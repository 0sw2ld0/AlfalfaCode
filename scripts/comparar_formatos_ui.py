"""PACAI UI Lang 3 frente a OpenUI Lang 1.0 y el JSON de PACAI con ejemplos: mismas tareas, catálogo y modelos.

    # medir (gasta: pide permiso antes)
    python scripts/comparar_formatos_ui.py medir --salida datos.jsonl [--repeticiones 4] [--tope 0.6]
    python scripts/comparar_formatos_ui.py medir --conjunto openui --briefs ruta/a/briefs.ts --salida b.jsonl
    # informe (no gasta): tablas en markdown y un JSON para la página
    python scripts/comparar_formatos_ui.py informe --revalidar docs/pacai/datos/formatos-ui-*.jsonl \
        [--json resumen.json] [--csv docs/pacai/datos/formatos-ui]

Tres brazos, con el servicio Alfalfa (suscripción CUY; nada de claves directas), esfuerzo bajo, temperatura 0, sin
herramientas y una corrección con los errores si la primera salida falla:

- `json_ej`: el prompt de `main` para los modelos sin UI Lang (`Catalogo.manual()`, un ejemplo por componente);
- `lang`: PACAI UI Lang 3, el prompt que ve hoy GPT-6 Luna o Claude Haiku 5.5;
- `openui_ej`: OpenUI Lang 1.0 (`scripts/openui_lang.py`) con el mismo catálogo y los mismos ejemplos traducidos. El
  prompt sigue el que genera el SDK de OpenUI (`packages/lang-core/src/parser/prompt.ts`): reglas de sintaxis,
  firmas posicionales, acciones con `@`, modo edición y verificación final.

Dos conjuntos:

- `pacai` (por defecto): las 52 tareas de `scripts/muestra_ejemplos_ui.py` (32 altas y 20 cambios sobre el tablero de
  CuyMercado), con su comprobación de «exacta».
- `openui`: los 46 briefs del benchmark de OpenUI (https://github.com/thesysdev/generative-ui-bench, `briefs/briefs.ts`,
  sin licencia publicada: el script los lee de un clon y no se copian al repo). Sólo altas. Válida como en ese
  benchmark: sin errores y con al menos tantos componentes alcanzables desde root como requisitos numerados tiene el
  brief (el «suelo de cobertura»). No hay «exacta».

Cada petición va en streaming y guarda cuándo llega cada trozo, para medir el tiempo hasta la primera pantalla. Si el
servicio entrega la respuesta en un solo trozo (como hasta ahora), la primera pantalla y el total coinciden y el
informe da además la estimación de OpenUI (tokens hasta la primera pantalla a 50 tokens/s).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import re
import statistics
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / 'scripts'))

import httpx  # noqa: E402
import muestra_ejemplos_ui as m  # noqa: E402
import openui_lang as o  # noqa: E402

from alfalfa.pacai import ui_lang  # noqa: E402

BRAZOS = ('json_ej', 'lang', 'openui_ej')
NOMBRES = {'json_ej': 'PACAI JSON con ejemplos', 'lang': 'PACAI UI Lang 3', 'openui_ej': 'OpenUI Lang 1.0'}
DECL = {k: v for k, v in m.DECLARACION.items() if k not in ('pieces', 'queries')}
MAX_SALIDA = {'pacai': 1500, 'openui': 6000}
TOKENS_POR_SEGUNDO = 50          # la estimación de streaming del benchmark de OpenUI

# -- prompt de OpenUI Lang 1.0 ----------------------------------------------------------------------------------
# Las partes que no son del formato (cuándo dibujar, texto plano) son las mismas que en PACAI UI Lang; el resto sigue
# el orden del generador del SDK (spec/prompt.md §4.4): sintaxis, firmas, acciones, streaming, modo edición, reglas.
FORMATO_OPENUI = '''
Pantallas en el chat (perfil ui): el cliente dibuja pantallas dentro de la conversación. Responde con una pantalla
cuando la respuesta sea una lista, una tabla, algo que elegir o rellenar, un gráfico o algo que te pidan enseñar aquí;
para una respuesta corta escribe texto normal. No crees archivos para enseñar algo en el chat.
Una pantalla va en un bloque ```openui-lang escrito en OpenUI Lang, un lenguaje declarativo de interfaces.

## Reglas de sintaxis
1. Cada sentencia va en su línea: `identificador = Expresión`
2. `root` es la entrada: todo programa define `root = Stack(...)`
3. Expresiones: textos ("..."), números, booleanos (true/false), null, listas ([...]), objetos ({...}) o llamadas a
   componentes Componente(arg1, arg2, ...)
4. Usa referencias: define `nombre = ...` en una línea y usa `nombre` después
5. TODA variable (salvo root) tiene que estar referenciada por otra. Una variable sin referenciar se descarta y NO se
   dibuja. Pon siempre las variables que defines en la lista de children de su padre.
6. Los argumentos son POSICIONALES (importa el orden, no el nombre). Escribe `Stack([hijos], "horizontal", "large")`, NO
   `Stack([hijos], direction: "horizontal")`: la sintaxis con dos puntos no existe y se rompe sin avisar
7. Los argumentos opcionales del final se pueden omitir; para llegar a uno posterior, pasa los anteriores con null
8. `$clave` lee el estado de la pantalla (lo que el usuario escribe en un Input o un Select con name clave)
9. Los textos van entre comillas dobles, con escapes de barra invertida

## Firmas de los componentes
Los argumentos con ? son opcionales. Los subcomponentes pueden ir en línea o por referencia; mejor por referencia.
{firmas}
- Filters, Stat, PriceBars y PriceLines leen period=$periodo y family=$familia.
- Input y Select guardan solos lo que escribe el usuario en la clave de su name: no les pongas change para eso.

## Acciones (botones y eventos)
Una acción conecta un evento con lo que pasa. Un paso solo va tal cual, por ejemplo Button("Guardar", null, null,
@Send("guardar")). Varios pasos van en Action([...]).
- @Send(nombre: string, with?: string[]) — te devuelve el evento nombre con los valores de esas claves del estado
- @OpenUrl(url: string) — abre un enlace
- @Set($clave, valor) — cambia el estado sin llamarte (es lo único que admite change)
Como mucho un @Send por evento. Nombres de evento y claves: ASCII sin tildes (a-z, 0-9, _).

## Streaming
Escribe primero root = Stack(...), luego los componentes de arriba abajo y al final los datos: la pantalla aparece
mientras llega.

## Modo edición
Si te enseñan la pantalla actual y te piden un cambio, el motor fusiona por nombre: el mismo nombre sustituye, uno
nuevo se añade. Escribe SÓLO las sentencias que cambian o son nuevas; lo demás se conserva.
- Para quitar un componente, vuelve a declarar su padre sin él en children (o escribe `nombre = null`): lo que sólo
  colgaba de él se va también.
- Usa los mismos nombres que ya existen; no los renombres. Nunca reescribas el programa entero.

## Ejemplos (copia la forma; los nombres y los textos son tuyos)
{ejemplos}

## Reglas importantes
- Texto plano, sin HTML ni markdown dentro de los textos. Máximo 300 componentes.
- Antes de terminar, comprueba: 1. root = Stack(...) es la primera línea; 2. todo nombre que usas está definido y todo
  nombre que defines (salvo root) se alcanza desde root.
'''


def sistema(brazo: str) -> str:
    if brazo == 'openui_ej':
        tabla = o.firmas(DECL)
        firmas = '\n'.join(o.firma_texto(c, tabla[c]) for c in tabla)
        return FORMATO_OPENUI.replace('{firmas}', firmas).replace('{ejemplos}', o.a_openui(m.CAT.ejemplos(), DECL)) \
            + m.CIERRE
    return m.sistema(brazo)


def tablero_openui() -> str:
    return o.a_openui(m.TABLERO, DECL)


# -- tareas ------------------------------------------------------------------------------------------------------
def briefs_de(ruta: str) -> list[dict]:
    """Los briefs del benchmark de OpenUI, leídos del `briefs.ts` de un clon (no se ejecuta: sólo se leen campos)."""
    texto = Path(ruta).read_text(encoding='utf-8')
    salida = []
    for bloque in re.finditer(r'\{\s*name:\s*"([^"]+)",\s*axis:\s*"[^"]*",\s*band:\s*"([^"]+)",\s*reqs:\s*(\d+),\s*'
                              r'prompt:\s*("(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\')', texto):
        salida.append({'name': bloque.group(1), 'band': bloque.group(2), 'reqs': int(bloque.group(3)),
                       'prompt': _cadena_ts(bloque.group(4))})
    if len(salida) != 46:
        sys.exit(f'Esperaba 46 briefs en {ruta} y hay {len(salida)}.')
    return salida


def _cadena_ts(literal: str) -> str:
    if literal[0] == "'":
        literal = '"' + literal[1:-1].replace("\\'", "'").replace('"', '\\"') + '"'
    return json.loads(literal)


def tareas(conjunto: str, briefs: str | None):
    """(id, tipo, texto, comprobar, extra) por tarea."""
    if conjunto == 'pacai':
        return [(i, tipo, texto, comprobar, {}) for i, tipo, texto, comprobar in m.TAREAS]
    return [(b['name'], 'alta', b['prompt'], (lambda n: True), {'reqs': b['reqs'], 'band': b['band']})
            for b in briefs_de(briefs)]


def mensaje(brazo, tarea) -> str:
    ident, tipo, texto, _, extra = tarea
    if 'reqs' in extra:
        # Los briefs no nombran la pantalla; los formatos de PACAI la piden (view=…) y OpenUI no tiene vistas.
        return texto if brazo == 'openui_ej' else texto + '\nNombre de la pantalla: view=' + ident.replace('-', '_')
    if brazo == 'openui_ej' and tipo == 'cambio':
        return ('Pantalla actual (modo edición), lo que mandaste:\n```openui-lang\n' + tablero_openui()
                + '\n```\n\nCambio que pide el usuario: ' + texto)
    return m.mensaje_de_tarea(brazo, tarea[:4])


# -- validación --------------------------------------------------------------------------------------------------
def _alcanzables(mapa):
    return m._vivos(mapa)


def validar_openui(tarea, texto):
    """(válida, exacta, errores, avisos, nodos). Como en el benchmark de OpenUI cuenta todo error del parser y de la
    librería, las referencias sin definir y los huérfanos; además, el mismo catálogo y el mismo árbol que PACAI."""
    _, tipo, _, comprobar, _ = tarea
    programa = o.extraer(texto)
    if not programa.strip():
        return False, False, ['No hay bloque de pantalla.'], [], {}
    base = tablero_openui() if tipo == 'cambio' else None
    nodos, fallos, avisos = o.mapa(programa, DECL, base=base)
    errores = [f'{c}: {msg}' for _, c, msg in fallos]
    mapa = _alcanzables(nodos)
    errores += [f'{i}: ' + '; '.join(e[:2]) for i, x in mapa.items() if (e := m.CAT.errores_del_nodo(x))]
    if 'root' not in mapa and not any(e.startswith('no-root') for e in errores):
        errores.append('Falta root.')
    for i, x in mapa.items():
        errores += [f'{h} no existe (hijo de {i}).' for h in x.get('children', []) if h not in mapa
                    and not any(e.startswith('unresolved: ' + h + ' ') for e in errores)]
    try:
        exacta = not errores and bool(comprobar(mapa))
    except (KeyError, TypeError, AttributeError, IndexError):
        exacta = False
    return not errores, exacta, errores, avisos, mapa


def nodos_finales(brazo, tarea, texto):
    """Los nodos que se dibujan (alcanzables desde root) de una respuesta válida, para la cobertura y el streaming."""
    if brazo == 'openui_ej':
        return validar_openui(tarea, texto)[4]
    attrs, lineas = m.bloque(texto)
    if attrs is None and brazo == 'lang':
        # Como `m.validar` y el motor (`Vistas.sin_valla`): sólo líneas de UI Lang valen sin valla.
        sin_valla = (m._vistas_lang() if tarea[1] == 'cambio' else m.Vistas(m.CAT_LANG)).sin_valla(texto)
        if sin_valla:
            attrs = {'view': sin_valla['view'], 'valla': 'pacai-ui-lang'}
            lineas = [x.strip() for x in texto.splitlines() if x.strip()]
    if attrs is None:
        return {}
    if brazo == 'lang':
        vistas = m._vistas_lang() if tarea[1] == 'cambio' else m.Vistas(m.CAT_LANG)
        vistas.aplicar({k: v for k, v in attrs.items() if k != 'valla'} | {'lang': True}, lineas)
    else:
        vistas = m.Vistas(m.CAT)
        if tarea[1] == 'cambio':
            vistas.aplicar({'view': 'tablero'}, [m._compacto(n) for n in m.TABLERO])
        vistas.aplicar({k: v for k, v in attrs.items() if k != 'valla'}, lineas)
    vista = vistas.vistas.get(attrs.get('view', 'tablero' if tarea[1] == 'cambio' else ''))
    return _alcanzables(dict(vista.nodos)) if vista else {}


def validar(brazo, tarea, texto):
    """(válida, exacta, errores, reparaciones). Con el conjunto de OpenUI, además el suelo de cobertura."""
    reparaciones = []
    if brazo == 'openui_ej':
        valida, exacta, errores, avisos, _ = validar_openui(tarea, texto)
        reparaciones = avisos
    else:
        valida, exacta, errores = m.validar(brazo, tarea[:4], texto, reparaciones)
    reqs = tarea[4].get('reqs')
    if valida and reqs:
        n = len(nodos_finales(brazo, tarea, texto))
        if n < reqs:
            valida, exacta = False, False
            errores = [f'Cobertura: {n} componentes < {reqs} requisitos.']
    return valida, exacta, errores, reparaciones


# Clases de error comunes a los tres formatos; los nombres en inglés son los del benchmark de OpenUI.
CLASES = [
    ('sin-bloque', 'no block', r'No hay bloque'),
    ('cobertura', 'coverage-floor', r'^Cobertura'),
    ('cambio', 'edit-conflict', r'se borra y se escribe en el mismo bloque|El bloque no cambió nada'),
    ('raiz', 'root-missing', r'Falta root|no-root|root ya|Ese id ya está definido'),
    ('componente-inventado', 'hallucinated-component', r'unknown-component|no está en el catálogo|no está en la '
     r'librería|Componente desconocido|no es un componente'),
    ('obligatoria', 'required-field', r'missing-required|null-required|is a required property|es obligatorio|faltan'),
    ('enum', 'enum-mismatch', r'is not one of'),
    ('acciones', 'actions', r'change sólo admite set|no ejecuta las acciones|no tiene los eventos|unknown-function|'
     r'unsupported-action|acción|Acción|send'),
    ('firma-o-tipo', 'signature-mismatch', r'excess-args|type-mismatch|is not of type|Additional properties|is not '
     r'valid under|does not match|is too long|is too short|less than|greater than|non-empty|should be non-empty'),
    ('referencias', 'reference-graph', r'no existe \(hijo|unresolved|orphan|no cuelga|Falta la línea|no está en la '
     r'pantalla|no está definido'),
    ('sintaxis', 'malformed-syntax', r'malformed-syntax|parse-failed|unsupported-expression|No se puede leer|JSON mal|'
     r'Esperaba|Falta el componente|Una línea empieza|Los hijos van|Sobra|Línea \d+: |línea \d+: [^(]|'
     r'Expecting|Unterminated|Extra data|Invalid'),
]


def clase(error: str) -> str:
    for nombre, _, patron in CLASES:
        if re.search(patron, error):
            return nombre
    return 'otros'


# -- streaming y cortes ------------------------------------------------------------------------------------------
def visibles(brazo, tarea, prefijo: str) -> int:
    """Cuántos nodos de un alta se verían con sólo `prefijo` del texto, con la regla de streaming de cada formato:
    PACAI (JSON y UI Lang) publica cada línea completa que vale (UI Lang con su `Flujo`); OpenUI cierra la cola
    pendiente (§4). En los tres, un nodo se ve si cuelga de root por nodos ya llegados; un hijo que falta se omite."""
    if brazo == 'openui_ej':
        programa = o.extraer(prefijo)
        completas, cola = o.sentencias(programa, cerrar_cola=False)
        texto = '\n'.join(completas)
        if cola:
            nombre_cola = (o._SENTENCIA.match(cola) or [None, None])[1]
            nombres = {(o._SENTENCIA.match(s) or [None, None])[1] for s in completas}
            if nombre_cola not in nombres:          # §4 regla 3: la cola cerrada no pisa una sentencia completa
                texto += '\n' + o.cerrar(cola)
        nodos, _, _ = o.mapa(texto, DECL) if texto.strip() else ({}, [], [])
        return len(_alcanzables(nodos))
    attrs, lineas = m.bloque(prefijo)
    if attrs is None:
        return 0
    if not prefijo.endswith('\n') and lineas and not prefijo.rstrip().endswith('```'):
        lineas = lineas[:-1]                     # la última línea aún no ha llegado entera
    if brazo == 'lang':
        flujo = ui_lang.Flujo(m.CAT_LANG)
        flujo.alimentar('\n'.join(lineas) + '\n')
        return len(_alcanzables(dict(flujo.nodos)))
    nodos = {}
    for linea in lineas:
        try:
            n = json.loads(linea)
        except json.JSONDecodeError:
            continue
        if isinstance(n, dict) and isinstance(n.get('id'), str) and not m.CAT.errores_del_nodo(n):
            nodos[n['id']] = n
    return len(_alcanzables(nodos))


def corte(brazo, tarea, texto, fraccion):
    """Fracción de los nodos finales que se ven con la `fraccion` inicial del texto."""
    total = len(nodos_finales(brazo, tarea, texto))
    if not total:
        return None
    return min(1.0, visibles(brazo, tarea, texto[:int(len(texto) * fraccion)]) / total)


def primera_pantalla(brazo, tarea, texto):
    """La fracción del texto que hace falta para ver root con al menos un hijo (en pasos del 5 %)."""
    for paso in range(1, 21):
        if visibles(brazo, tarea, texto[:int(len(texto) * paso / 20)]) >= 2:
            return paso / 20
    return None


# -- peticiones --------------------------------------------------------------------------------------------------
async def pedir_http(cliente, modelo, mensajes, max_salida):
    """(texto, entrada, salida, segundos, trozos[(segundos, caracteres acumulados)])."""
    for espera in (10, 20, 40, 60, None):
        t0 = time.monotonic()
        trozos, partes, final = [], [], {}
        async with cliente.stream('POST', '/api/chat', json={
                'model': modelo, 'messages': mensajes, 'stream': True, 'think': False,
                'options': {'temperature': 0, 'num_predict': max_salida}}) as respuesta:
            if respuesta.status_code == 429 and espera is not None:
                await respuesta.aread()
                await asyncio.sleep(espera)
                continue
            if respuesta.status_code >= 400:
                cuerpo = (await respuesta.aread()).decode(errors='replace')
                raise RuntimeError(f'HTTP {respuesta.status_code}: {cuerpo[:300]}')
            async for linea in respuesta.aiter_lines():
                if not linea.strip():
                    continue
                dato = json.loads(linea)
                trozo = (dato.get('message') or {}).get('content') or ''
                if trozo:
                    partes.append(trozo)
                    trozos.append((round(time.monotonic() - t0, 3), sum(len(p) for p in partes)))
                if dato.get('done'):
                    final = dato
        texto = ''.join(partes)
        return (texto, final.get('prompt_eval_count') or 0, final.get('eval_count') or 0,
                time.monotonic() - t0, trozos)
    raise RuntimeError('HTTP 429 tras cinco intentos')


# -- otros proveedores: Ollama (local o su nube) y Gemini por el CLI de Antigravity --------------------------------
# USD por millón de tokens (entrada, salida), sólo para situar en la clasificación a los modelos que se pagan con un
# plan y no por token. Salen del catálogo (`alfalfa/catalogo/minimo.json`, entrada de opencode); Qwen 3.5 usa el de
# qwen3.5-plus. Los modelos locales de Ollama cuestan 0.
PRECIOS_REFERENCIA = {'gemini-4-argon': (2.0, 10.0), 'gemini-3.8-flash': (1.5, 7.5), 'qwen3.5': (0.2, 1.2)}


def precio_de(proveedor, modelo):
    """{'entrada', 'salida', 'referencia'} o None. Primero el del proveedor; luego el de Ollama Cloud del catálogo;
    luego PRECIOS_REFERENCIA. Un modelo local de Ollama (sin «cloud» en el nombre) cuesta 0."""
    precio = proveedor.precio(modelo) if hasattr(proveedor, 'precio') else None
    if precio:
        return precio
    from alfalfa import catalogo
    if getattr(proveedor, 'id', '') == 'ollama':
        if 'cloud' not in modelo:
            return {'entrada': 0, 'salida': 0, 'referencia': 'local'}
        ficha = catalogo.ficha('ollama-cloud', modelo) or {}
        if ficha.get('precio'):
            return ficha['precio'] | {'referencia': 'catálogo de Ollama Cloud'}
    base = re.sub(r'-(low|medium|high|xhigh)$', '', modelo.split(':')[0])
    if base in PRECIOS_REFERENCIA:
        entrada, salida = PRECIOS_REFERENCIA[base]
        return {'entrada': entrada, 'salida': salida, 'referencia': 'catálogo (opencode)'}
    return None


class Agy:
    """Gemini con el plan del dueño, por el CLI de Antigravity (`agy -p`): sin claves en el código, con la sesión que
    ya tiene iniciada el CLI. Es un agente con su propio prompt de sistema: el nuestro va al principio del mensaje y
    se le pide que no use herramientas. Corre en una carpeta vacía y con `--sandbox`."""
    id = 'agy'

    def __init__(self, ejecutable=None):
        import shutil
        self.ejecutable = ejecutable or shutil.which('agy') or str(Path.home() / '.local' / 'bin' / 'agy')

    def configurado(self):
        return Path(self.ejecutable).exists()

    def precio(self, modelo):
        return None

    def cliente(self, timeout, tarea=None):
        return _ClienteAgy(self.ejecutable, timeout)


class _ClienteAgy:
    def __init__(self, ejecutable, timeout):
        self.ejecutable, self.segundos = ejecutable, int(getattr(timeout, 'read', None) or 240)

    async def __aenter__(self):
        import tempfile
        self._carpeta = tempfile.TemporaryDirectory(prefix='agy-ui-')
        return self

    async def __aexit__(self, *exc):
        self._carpeta.cleanup()


def prompt_agy(mensajes) -> str:
    partes = ['Eres el modelo de un banco de pruebas. NO uses herramientas, archivos ni la terminal: contesta sólo con '
              'texto, siguiendo estas instrucciones como si fueran tu prompt de sistema.\n\n# Instrucciones\n'
              + mensajes[0]['content']]
    for x in mensajes[1:]:
        titulo = '# Mensaje del usuario\n' if x['role'] == 'user' else '# Tu respuesta anterior\n'
        partes.append(titulo + x['content'])
    return '\n\n'.join(partes)


def _texto_agy(salida: str):
    """(texto, entrada, salida) de `agy -p --output-format json`. Si no trae tokens, se estiman con o200k_base."""
    try:
        dato = json.loads(salida)
    except ValueError:
        return salida.strip(), 0, 0
    texto = next((dato[k] for k in ('result', 'response', 'text', 'content', 'output') if isinstance(dato.get(k), str)),
                 salida)
    uso = dato.get('usage') or {}
    entrada = uso.get('input_tokens') or uso.get('prompt_tokens') or 0
    salida_t = uso.get('output_tokens') or uso.get('completion_tokens') or 0
    return texto, entrada, salida_t


async def pedir_agy(cliente, modelo, mensajes, max_salida):
    t0 = time.monotonic()
    prompt = prompt_agy(mensajes)
    proc = await asyncio.create_subprocess_exec(
        cliente.ejecutable, '-p', prompt, '--model', modelo, '--output-format', 'json', '--sandbox',
        '--disable-slash-commands', '--print-timeout', f'{cliente.segundos}s', cwd=cliente._carpeta.name,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    salida, error = await proc.communicate()
    if proc.returncode:
        raise RuntimeError(f'agy salió con {proc.returncode}: {error.decode(errors="replace")[:300]}')
    texto, _, salida_t = _texto_agy(salida.decode(errors='replace'))
    total = time.monotonic() - t0
    # La entrada que informa agy lleva su prompt de agente (~19 000 tokens): para el coste cuenta sólo el nuestro.
    return (texto, tokens(prompt), salida_t or tokens(texto), total, [(round(total, 3), len(texto))])


def _coste(precio, entrada, salida):
    return m.coste(precio, entrada, salida) if precio else 0


async def una(proveedor, modelo, brazo, tarea, repeticion, max_salida):
    precio = precio_de(proveedor, modelo)
    pedir = pedir_agy if getattr(proveedor, 'id', '') == 'agy' else pedir_http
    mensajes = [{'role': 'system', 'content': sistema(brazo)}, {'role': 'user', 'content': mensaje(brazo, tarea)}]
    fila = {'modelo': modelo, 'brazo': brazo, 'tarea': tarea[0], 'tipo': tarea[1], 'repeticion': repeticion,
            'proveedor': getattr(proveedor, 'id', ''), **{k: v for k, v in tarea[4].items()}}
    if precio and precio.get('referencia'):
        fila['precio_referencia'] = precio['referencia']
    if not precio:
        fila['sin_precio'] = True              # cuesta 0 en la tabla, pero no entra en la frontera de Pareto
    async with proveedor.cliente(httpx.Timeout(240, connect=10), tarea={'effort': 'low'}) as cliente:
        try:
            texto, entrada, salida, total, trozos = await pedir(cliente, modelo, mensajes, max_salida)
        except Exception as exc:  # noqa: BLE001 - un fallo del servicio se anota y no cuenta como fallo del modelo
            return fila | {'error': str(exc)[:300], 'coste': 0}
        valida, exacta, errores, reparaciones = validar(brazo, tarea, texto)
        fila |= {'valida': valida, 'exacta': exacta, 'errores': errores[:8], 'reparaciones': reparaciones,
                 'entrada': entrada, 'salida': salida, 'total': round(total, 3), 'trozos': trozos[:400],
                 'n_trozos': len(trozos), 'coste': _coste(precio, entrada, salida), 'respuesta': texto[:20000],
                 'truncada': salida >= max_salida}
        if valida:
            return fila | {'valida_tras': True, 'exacta_tras': exacta}
        peticion = ('El motor rechazó el bloque: ' + '; '.join(errores[:8])
                    + '\nVuelve a mandar el bloque entero, corregido.')
        try:
            texto2, entrada2, salida2, total2, _ = await pedir(cliente, modelo, mensajes + [
                {'role': 'assistant', 'content': texto}, {'role': 'user', 'content': peticion}], max_salida)
        except Exception as exc:  # noqa: BLE001
            return fila | {'error_correccion': str(exc)[:300], 'valida_tras': False, 'exacta_tras': False}
        valida2, exacta2, errores2, _ = validar(brazo, tarea, texto2)
        return fila | {'valida_tras': valida2, 'exacta_tras': exacta2, 'errores_tras': errores2[:8],
                       'entrada2': entrada2, 'salida2': salida2, 'total2': round(total2, 3),
                       'coste2': _coste(precio, entrada2, salida2), 'respuesta2': texto2[:20000]}


async def medir(opciones) -> None:
    if opciones.proveedor == 'agy':
        proveedor = Agy()
    elif opciones.proveedor == 'ollama':
        from alfalfa.proveedores.ollama import Ollama
        proveedor = Ollama()
    else:
        from alfalfa.proveedores.alfalfa import Alfalfa
        proveedor = Alfalfa()
    if not proveedor.configurado():
        sys.exit(f'El proveedor {opciones.proveedor} no está configurado en este equipo.')
    lista = [t for t in tareas(opciones.conjunto, opciones.briefs) if not opciones.solo
             or t[0] in opciones.solo.split(',')]
    modelos = opciones.modelos.split(',')
    hechas = set()
    salida_path = Path(opciones.salida)
    if salida_path.exists():                      # reanudar: no repite lo que ya está en el archivo
        for linea in salida_path.read_text(encoding='utf-8').splitlines():
            r = json.loads(linea)
            if 'error' not in r:
                hechas.add((r['modelo'], r['brazo'], r['tarea'], r['repeticion']))
    trabajos = [(mo, b, t, r) for r in range(opciones.primera_repeticion,
                                             opciones.primera_repeticion + opciones.repeticiones)
                for t in lista for mo in modelos for b in opciones.brazos.split(',')
                if (mo, b, t[0], r) not in hechas]
    semaforo = asyncio.Semaphore(opciones.simultaneas)
    gastado = {'usd': 0.0, 'parado': False, 'hechas': 0}
    salida = salida_path.open('a', encoding='utf-8')
    max_salida = MAX_SALIDA[opciones.conjunto]

    async def correr(trabajo):
        async with semaforo:
            if gastado['usd'] >= opciones.tope:
                gastado['parado'] = True
                return
            fila = await una(proveedor, *trabajo, max_salida)
            gastado['usd'] += fila.get('coste', 0) + fila.get('coste2', 0)
            gastado['hechas'] += 1
            salida.write(json.dumps(fila, ensure_ascii=False) + '\n')
            salida.flush()

    await asyncio.gather(*(correr(t) for t in trabajos))
    print(json.dumps({'peticiones': gastado['hechas'], 'gastado_usd': round(gastado['usd'], 4),
                      'parado_por_tope': gastado['parado']}))


# -- informe -----------------------------------------------------------------------------------------------------
def wilson(k, n, z=1.96):
    if not n:
        return (0.0, 0.0)
    p = k / n
    centro = (p + z * z / (2 * n)) / (1 + z * z / n)
    margen = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return (centro - margen, centro + margen)


def tokens(texto: str) -> int:
    import tiktoken

    return len(tiktoken.get_encoding('o200k_base').encode(texto))


def _med(xs):
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


def _tarea_de(r, mapa_tareas):
    return mapa_tareas.get(r['tarea'])


def resumen(filas, mapa_tareas):
    """Por (conjunto, modelo, brazo): todas las cifras del informe."""
    grupos = defaultdict(list)
    for r in filas:
        if 'error' in r:
            continue
        grupos[(r['conjunto'], r['modelo'], r['brazo'])].append(r)
    salida = {}
    for clave, rs in sorted(grupos.items()):
        n = len(rs)
        validas = sum(r['valida'] for r in rs)
        exactas = sum(r['exacta'] for r in rs)
        tras = sum(r.get('valida_tras', False) for r in rs)
        exactas_tras = sum(r.get('exacta_tras', False) for r in rs)
        coste = sum(r['coste'] + r.get('coste2', 0) for r in rs)
        coste1 = sum(r['coste'] for r in rs)
        altas = [r for r in rs if r['tipo'] == 'alta']
        cambios = [r for r in rs if r['tipo'] == 'cambio']
        errores = Counter(clase(r['errores'][0]) for r in rs if not r['valida'] and r['errores'])
        errores_todos = Counter(c for r in rs if not r['valida'] for c in {clase(e) for e in r['errores']})
        cortes = {f: [] for f in (0.25, 0.5, 0.75)}
        primeras, primeras_s, primeras_est = [], [], []
        for r in altas:
            if not r['valida']:
                continue
            tarea = _tarea_de(r, mapa_tareas)
            for f in cortes:
                v = corte(r['brazo'], tarea, r['respuesta'], f)
                if v is not None:
                    cortes[f].append(v)
            p = primera_pantalla(r['brazo'], tarea, r['respuesta'])
            if p is not None:
                primeras.append(p)
                primeras_est.append(r['salida'] * p / TOKENS_POR_SEGUNDO)
                hasta = int(len(r['respuesta']) * p)
                llegado = next((t for t, c in r.get('trozos') or [] if c >= hasta), r['total'])
                primeras_s.append(llegado)
        salida['|'.join(clave)] = {
            'conjunto': clave[0], 'modelo': clave[1], 'brazo': clave[2], 'n': n,
            'validas': validas, 'pct_validas': validas / n, 'wilson': wilson(validas, n),
            'exactas': exactas, 'validas_tras': tras, 'exactas_tras': exactas_tras,
            'validas_altas': [sum(r['valida'] for r in altas), len(altas)],
            'validas_cambios': [sum(r['valida'] for r in cambios), len(cambios)],
            'exactas_altas': sum(r['exacta'] for r in altas), 'exactas_cambios': sum(r['exacta'] for r in cambios),
            'entrada_med': _med([r['entrada'] for r in rs]), 'salida_med': _med([r['salida'] for r in rs]),
            'salida_altas_med': _med([r['salida'] for r in altas]),
            'salida_cambios_med': _med([r['salida'] for r in cambios]),
            'entrada_total': sum(r['entrada'] + r.get('entrada2', 0) for r in rs),
            'salida_total': sum(r['salida'] + r.get('salida2', 0) for r in rs),
            'total_med': _med([r['total'] for r in rs]), 'total_p90': _p(rs, 'total', 0.9),
            'trozos_med': _med([r.get('n_trozos') for r in rs]),
            'primera_s_med': _med(primeras_s), 'primera_frac_med': _med(primeras),
            'primera_est_med': _med(primeras_est),
            'coste_total': coste, 'coste_por_peticion': coste1 / n,
            'coste_por_valida_1': coste1 / validas if validas else None,
            'coste_por_exacta_1': coste1 / exactas if exactas else None,
            'coste_por_valida_tras': coste / tras if tras else None,
            'coste_por_exacta_tras': coste / exactas_tras if exactas_tras else None,
            'errores': dict(errores), 'errores_todos': dict(errores_todos),
            'cortes': {str(f): (_med(v), sum(x >= 0.999 for x in v) / len(v) if v else None)
                       for f, v in cortes.items()},
            'truncadas': sum(r.get('truncada', False) for r in rs),
            'referencia': next((r['precio_referencia'] for r in rs if r.get('precio_referencia')), None),
            'sin_precio': any(r.get('sin_precio') for r in rs),
            'reparaciones': sum(bool(r.get('reparaciones')) for r in rs),
        }
        if clave[0] == 'openui':
            bandas = defaultdict(lambda: [0, 0])
            for r in rs:
                bandas[r['band']][0] += r['valida']
                bandas[r['band']][1] += 1
            salida['|'.join(clave)]['bandas'] = dict(sorted(bandas.items()))
    return salida


def clasificacion(res, brazo='lang'):
    """Una fila por modelo con el formato `brazo`: validez a la primera en las 52 tareas y en los 46 briefs (con su
    suelo de cobertura), coste medio por tarea del primer intento y si está en la frontera de Pareto. La validez y el
    coste son la media de los dos conjuntos (pesan igual); sólo entran en la frontera los modelos medidos en los dos."""
    modelos = defaultdict(dict)
    for r in res.values():
        if r['brazo'] == brazo:
            modelos[r['modelo']][r['conjunto']] = r
    filas = []
    for modelo, por in modelos.items():
        t, b = por.get('pacai'), por.get('openui')
        coste = sum(x['coste_por_peticion'] for x in por.values()) / len(por)    # los dos conjuntos pesan igual
        validez = (t['pct_validas'] + b['pct_validas']) / 2 if t and b else None
        filas.append({'modelo': modelo, 'validez': validez,
                      'tareas': (t['validas'], t['n']) if t else None, 'briefs': (b['validas'], b['n']) if b else None,
                      'wilson_briefs': b['wilson'] if b else None, 'bandas': b.get('bandas') if b else None,
                      'coste_tarea': coste, 'salida_med': _med([x['salida_med'] for x in por.values()]),
                      'total_med': _med([x['total_med'] for x in por.values()]),
                      'referencia': any(x.get('referencia') for x in por.values()),
                      'sin_precio': any(x.get('sin_precio') for x in por.values())})
    completos = [f for f in filas if f['validez'] is not None and not f['sin_precio']]
    for f in filas:
        f['pareto'] = f in completos and not any(
            g is not f and g['validez'] >= f['validez'] and g['coste_tarea'] <= f['coste_tarea']
            and (g['validez'] > f['validez'] or g['coste_tarea'] < f['coste_tarea']) for g in completos)
    return sorted(filas, key=lambda f: (f['validez'] is None, -(f['validez'] or 0), f['coste_tarea']))


def _p(rs, campo, q):
    xs = sorted(r[campo] for r in rs if r.get(campo) is not None)
    return xs[min(len(xs) - 1, int(q * len(xs)))] if xs else None


def muestras_de_tokens():
    """Las mismas pantallas escritas en los tres formatos (las de `tests/conformidad/muestras-pacai-ui-lang.json` que
    no usan piezas, más el tablero y su parche): caracteres y tokens `o200k_base`."""
    salida = []
    tablero_json = '\n'.join(m._compacto(n) for n in m.TABLERO)
    tablero_lang = ui_lang.a_lang(m.TABLERO, m.CAT_LANG)
    salida.append(('Tablero de CuyMercado', tablero_json, tablero_lang, tablero_openui()))
    for nombre, nodos in EJEMPLOS_TOKENS.items():
        salida.append((nombre, '\n'.join(m._compacto(n) for n in nodos), ui_lang.a_lang(nodos, m.CAT_LANG),
                       o.a_openui(nodos, DECL)))
    # Sin borrar c_bajaron a mano: en los tres formatos, un nodo que su padre deja fuera ya no se dibuja.
    patch_json = '\n'.join(m._compacto(n) for n in PARCHE_NODOS)
    patch_lang = ui_lang.a_lang(PARCHE_NODOS, m.CAT_LANG)
    patch_openui = o.a_openui(PARCHE_NODOS, DECL)
    salida.append(('Cambio: «Iguales» en lugar de «Bajaron»', patch_json, patch_lang, patch_openui))
    return [{'muestra': n, 'json': j, 'lang': lg, 'openui': op,
             'tokens': {'json_ej': tokens(j), 'lang': tokens(lg), 'openui_ej': tokens(op)},
             'caracteres': {'json_ej': len(j), 'lang': len(lg), 'openui_ej': len(op)}} for n, j, lg, op in salida]


PARCHE_NODOS = [
    {'id': 'cifras', 'component': 'Grid', 'props': {'columns': 4}, 'children': ['c_subieron', 'c_iguales']},
    {'id': 'c_iguales', 'component': 'Stat', 'props': {'label': 'Iguales', 'icon': 'igual', **m.B,
                                                      'query': {'tipo': 'totales', 'campo': 'iguales'}}},
]
EJEMPLOS_TOKENS = {
    'Formulario de contacto': [
        {'id': 'root', 'component': 'Form', 'props': {'submit_label': 'Mandar'},
         'children': ['nombre', 'correo', 'mensaje'],
         'events': {'submit': {'send': 'contacto', 'with': ['nombre', 'correo', 'mensaje']}}},
        {'id': 'nombre', 'component': 'Input', 'props': {'name': 'nombre', 'label': 'Nombre', 'required': True}},
        {'id': 'correo', 'component': 'Input', 'props': {'name': 'correo', 'label': 'Correo', 'kind': 'email',
                                                         'required': True}},
        {'id': 'mensaje', 'component': 'Input', 'props': {'name': 'mensaje', 'label': 'Mensaje',
                                                          'kind': 'multiline'}},
    ],
    'Tabla de tickets': [
        {'id': 'root', 'component': 'Table', 'props': {
            'columns': [{'key': 'id', 'label': 'Id'}, {'key': 'titulo', 'label': 'Título'},
                        {'key': 'prioridad', 'label': 'Prioridad'}],
            'rows': [{'id': 'T-1', 'titulo': 'Login falla', 'prioridad': 'Alta'},
                     {'id': 'T-2', 'titulo': 'Botón roto', 'prioridad': 'Media'},
                     {'id': 'T-3', 'titulo': 'Texto mal', 'prioridad': 'Baja'}]},
         'events': {'row_press': {'send': 'abrir_ticket'}}},
    ],
    'Tres tarjetas con enlace': [
        {'id': 'root', 'component': 'Stack', 'props': {'direction': 'grid'}, 'children': ['palta', 'limon', 'papa']},
        *[x for nombre, titulo in (('palta', 'Palta'), ('limon', 'Limón'), ('papa', 'Papa')) for x in (
            {'id': nombre, 'component': 'Card', 'props': {'title': titulo, 'tags': ['fruta']},
             'children': ['b_' + nombre]},
            {'id': 'b_' + nombre, 'component': 'Button', 'props': {'label': 'Abrir fuente'},
             'events': {'press': {'open_url': 'https://ejemplo.pe/' + nombre}}})],
    ],
}


def informe(opciones) -> None:
    filas = []
    mapa_tareas = {}
    for ruta in opciones.datos:
        for linea in Path(ruta).read_text(encoding='utf-8').splitlines():
            r = json.loads(linea)
            r['conjunto'] = 'openui' if 'reqs' in r else 'pacai'
            filas.append(r)
    for t in tareas('pacai', None):
        mapa_tareas[t[0]] = t
    for r in filas:
        if r['conjunto'] == 'openui' and r['tarea'] not in mapa_tareas:
            mapa_tareas[r['tarea']] = (r['tarea'], 'alta', '', (lambda n: True), {'reqs': r['reqs'],
                                                                                 'band': r['band']})
    if opciones.revalidar:
        for r in filas:
            revalidar(r, mapa_tareas[r['tarea']])
    datos = {'resumen': resumen(filas, mapa_tareas), 'muestras': muestras_de_tokens(),
             'prompt_tokens': {b: tokens(sistema(b)) for b in BRAZOS},
             'errores_servicio': sum('error' in r for r in filas)}
    datos['clasificacion'] = clasificacion(datos['resumen'])
    if opciones.json:
        Path(opciones.json).write_text(json.dumps(datos, ensure_ascii=False, indent=1), encoding='utf-8')
    if opciones.csv:
        escribir_csv(datos, Path(opciones.csv))
    imprimir(datos)


def escribir_csv(datos, carpeta: Path) -> None:
    """Las tablas de la página: resumen, errores, cortes y bandas en CSV (coma, punto decimal) y muestras en JSON."""
    import csv

    carpeta.mkdir(parents=True, exist_ok=True)

    def volcar(nombre, filas):
        if not filas:
            return
        with (carpeta / nombre).open('w', encoding='utf-8', newline='') as f:
            w = csv.DictWriter(f, fieldnames=list(filas[0]))
            w.writeheader()
            w.writerows(filas)

    res = list(datos['resumen'].values())
    volcar('resumen.csv', [{
        'conjunto': r['conjunto'], 'modelo': r['modelo'], 'formato': NOMBRES[r['brazo']], 'n': r['n'],
        'validas': r['validas'], 'pct_validas': round(100 * r['pct_validas'], 1),
        'wilson_bajo': round(100 * r['wilson'][0], 1), 'wilson_alto': round(100 * r['wilson'][1], 1),
        'exactas': r['exactas'], 'validas_tras': r['validas_tras'],
        'pct_validas_tras': round(100 * r['validas_tras'] / r['n'], 1),
        'validas_altas': r['validas_altas'][0], 'n_altas': r['validas_altas'][1],
        'validas_cambios': r['validas_cambios'][0], 'n_cambios': r['validas_cambios'][1],
        'entrada_med': r['entrada_med'], 'salida_med': r['salida_med'],
        'salida_altas_med': r['salida_altas_med'], 'salida_cambios_med': r['salida_cambios_med'],
        'total_med_s': round(r['total_med'], 2) if r['total_med'] else None,
        'total_p90_s': round(r['total_p90'], 2) if r['total_p90'] else None,
        'primera_s_med': round(r['primera_s_med'], 2) if r['primera_s_med'] else None,
        'primera_est_s': round(r['primera_est_med'], 2) if r['primera_est_med'] else None,
        'primera_frac': r['primera_frac_med'],
        'coste_por_valida_milesimas': round(1000 * r['coste_por_valida_1'], 3) if r['coste_por_valida_1'] else None,
        'coste_por_exacta_milesimas': round(1000 * r['coste_por_exacta_1'], 3) if r['coste_por_exacta_1'] else None,
        'coste_por_valida_tras_milesimas': round(1000 * r['coste_por_valida_tras'], 3)
        if r['coste_por_valida_tras'] else None,
        'coste_total_usd': round(r['coste_total'], 4),
        'coste_primer_intento_usd': round(r['coste_por_peticion'] * r['n'], 5),
        'entrada_total': r['entrada_total'], 'salida_total': r['salida_total'],
    } for r in res])
    volcar('errores.csv', [{'conjunto': r['conjunto'], 'modelo': r['modelo'], 'formato': NOMBRES[r['brazo']],
                            'clase': k, 'pantallas': v}
                           for r in res for k, v in sorted(r['errores'].items(), key=lambda x: -x[1])])
    volcar('cortes.csv', [{'conjunto': r['conjunto'], 'modelo': r['modelo'], 'formato': NOMBRES[r['brazo']],
                           'texto_llegado_pct': int(100 * float(f)), 'nodos_visibles_pct':
                           round(100 * r['cortes'][f][0], 1) if r['cortes'][f][0] is not None else None}
                          for r in res for f in ('0.25', '0.5', '0.75')])
    bandas = [{'modelo': r['modelo'], 'formato': NOMBRES[r['brazo']], 'banda': b, 'validas': v[0], 'n': v[1],
               'pct_validas': round(100 * v[0] / v[1], 1)} for r in res for b, v in r.get('bandas', {}).items()]
    if bandas:
        volcar('bandas.csv', bandas)
    volcar('clasificacion.csv', [{
        'modelo': f['modelo'], 'validez_pct': round(100 * f['validez'], 1) if f['validez'] is not None else None,
        'tareas_validas': f['tareas'][0] if f['tareas'] else None, 'tareas_n': f['tareas'][1] if f['tareas'] else None,
        'briefs_validas': f['briefs'][0] if f['briefs'] else None, 'briefs_n': f['briefs'][1] if f['briefs'] else None,
        'coste_tarea_milesimas': round(1000 * f['coste_tarea'], 4), 'salida_med': f['salida_med'],
        'total_med_s': round(f['total_med'], 2) if f['total_med'] else None, 'pareto': f['pareto'],
        'precio_de_referencia': f['referencia'], 'sin_precio': f['sin_precio']} for f in datos['clasificacion']])
    # Las muestras llevan saltos de línea: van en JSON, una lista de filas.
    clave = {'json_ej': 'json', 'lang': 'lang', 'openui_ej': 'openui'}
    muestras = [{'muestra': x['muestra'], 'formato': NOMBRES[b], 'tokens': x['tokens'][b],
                 'caracteres': x['caracteres'][b], 'texto': x[clave[b]]} for x in datos['muestras'] for b in BRAZOS]
    (carpeta / 'muestras.json').write_text(json.dumps(muestras, ensure_ascii=False), encoding='utf-8')


def revalidar(r, tarea) -> None:
    """Vuelve a validar las respuestas guardadas con el lector de hoy, sin pedir nada. Si la primera pasa a valer, la
    corrección que se pidió entonces deja de contar (su coste y sus tokens se quitan de la fila)."""
    if 'error' in r or 'respuesta' not in r:
        return
    valida, exacta, errores, reparaciones = validar(r['brazo'], tarea, r['respuesta'])
    r.update(valida=valida, exacta=exacta, errores=errores[:8], reparaciones=reparaciones)
    if valida:
        for k in ('entrada2', 'salida2', 'coste2', 'total2', 'respuesta2', 'errores_tras'):
            r.pop(k, None)
        r.update(valida_tras=True, exacta_tras=exacta)
    elif 'respuesta2' in r:
        valida2, exacta2, errores2, _ = validar(r['brazo'], tarea, r['respuesta2'])
        r.update(valida_tras=valida2, exacta_tras=exacta2, errores_tras=errores2[:8])


def _pct(x):
    return '—' if x is None else f'{100 * x:.1f} %'.replace('.', ',')


def _num(x, d=0):
    return '—' if x is None else (f'{x:.{d}f}' if d else f'{round(x)}').replace('.', ',')


def _usd(x):
    return '—' if x is None else f'{1000 * x:.2f} $/1000'.replace('.', ',')


def imprimir(datos):
    res = datos['resumen']
    print('| Conjunto | Modelo | Formato | n | Válidas 1.er intento | IC95 % Wilson | Exactas 1.º |'
          ' Válidas tras corr. | Entrada (med.) | Salida (med.) | Total (med.) | Coste por válida | Coste por exacta |')
    print('|---|---|---|---:|---|---|---|---|---:|---:|---:|---:|---:|')
    for r in res.values():
        lo, hi = r['wilson']
        print(f"| {r['conjunto']} | {r['modelo']} | {NOMBRES[r['brazo']]} | {r['n']} | {r['validas']} "
              f"({_pct(r['pct_validas'])}) | {_pct(lo)}–{_pct(hi)} | {r['exactas']} | {r['validas_tras']} | "
              f"{_num(r['entrada_med'])} | {_num(r['salida_med'])} | {_num(r['total_med'], 2)} s | "
              f"{_usd(r['coste_por_valida_1'])} | {_usd(r['coste_por_exacta_1'])} |")
    print('\nErrores (clase del primer error de cada pantalla no válida):')
    for r in res.values():
        print(f"- {r['conjunto']} · {r['modelo']} · {NOMBRES[r['brazo']]}: "
              + ', '.join(f'{k} {v}' for k, v in sorted(r['errores'].items(), key=lambda x: -x[1])))
    print('\nStreaming (altas válidas): fracción de nodos visibles con el 25/50/75 % del texto, primera pantalla:')
    for r in res.values():
        c = r['cortes']
        print(f"- {r['conjunto']} · {r['modelo']} · {NOMBRES[r['brazo']]}: "
              + ' / '.join(_pct(c[f][0]) for f in ('0.25', '0.5', '0.75'))
              + f" · primera pantalla al {_pct(r['primera_frac_med'])} del texto, {_num(r['primera_s_med'], 2)} s "
              f"medidos, {_num(r['primera_est_med'], 2)} s a 50 tok/s · trozos {_num(r['trozos_med'])}")
    print('\nTokens o200k_base de la misma pantalla:')
    for x in datos['muestras']:
        print(f"- {x['muestra']}: " + ', '.join(f"{NOMBRES[b]} {x['tokens'][b]}" for b in BRAZOS))
    print('\nClasificación con PACAI UI Lang 3 (validez = media de las 52 tareas y los 46 briefs; * = frontera de '
          'Pareto validez/coste; ~ = precio de referencia, se paga con un plan):')
    print('| Modelo | Validez | 52 tareas | 46 briefs | Coste por tarea | Salida (med.) | Total (med.) |')
    print('|---|---|---|---|---:|---:|---:|')
    for f in datos['clasificacion']:
        fr = lambda x: f'{x[0]}/{x[1]}' if x else '—'  # noqa: E731
        coste = 'sin precio' if f['sin_precio'] else ('~' if f['referencia'] else '') + _usd(f['coste_tarea'])
        print(f"| {f['modelo']}{' *' if f['pareto'] else ''} | {_pct(f['validez'])} | {fr(f['tareas'])} |"
              f" {fr(f['briefs'])} | {coste} |"
              f" {_num(f['salida_med'])} | {_num(f['total_med'], 2)} s |")
    print('\nTokens del prompt de sistema (o200k_base):', datos['prompt_tokens'])
    print('Errores del servicio:', datos['errores_servicio'])


def main() -> None:
    args = argparse.ArgumentParser()
    sub = args.add_subparsers(dest='orden', required=True)
    a = sub.add_parser('medir')
    a.add_argument('--salida', required=True)
    a.add_argument('--conjunto', choices=('pacai', 'openui'), default='pacai')
    a.add_argument('--briefs', help='ruta al briefs/briefs.ts de un clon de generative-ui-bench')
    a.add_argument('--repeticiones', type=int, default=4)
    a.add_argument('--primera-repeticion', type=int, default=0)
    a.add_argument('--simultaneas', type=int, default=6)
    a.add_argument('--tope', type=float, default=0.6, help='USD: no lanza más peticiones al alcanzarlo')
    a.add_argument('--brazos', default=','.join(BRAZOS))
    a.add_argument('--modelos', default=','.join(m.MODELOS))
    a.add_argument('--solo', help='tareas separadas por comas')
    a.add_argument('--proveedor', choices=('alfalfa', 'ollama', 'agy'), default='alfalfa',
                   help='alfalfa (suscripción CUY), ollama (local o su nube) o agy (Gemini con el CLI de Antigravity)')
    b = sub.add_parser('informe')
    b.add_argument('datos', nargs='+')
    b.add_argument('--json')
    b.add_argument('--revalidar', action='store_true', help='vuelve a validar las respuestas guardadas')
    b.add_argument('--csv', help='carpeta donde escribir las tablas en CSV')
    opciones = args.parse_args()
    if opciones.orden == 'medir':
        if opciones.conjunto == 'openui' and not opciones.briefs:
            sys.exit('El conjunto openui necesita --briefs.')
        asyncio.run(medir(opciones))
    else:
        informe(opciones)


if __name__ == '__main__':
    main()
