"""Muestra grande: ¿un ejemplo por componente en el prompt del perfil `ui` sube las pantallas válidas a la primera?

    python scripts/muestra_ejemplos_ui.py --salida resultados.jsonl [--repeticiones 2] [--tope 1.0]

Tres brazos, con Claude Haiku 5.5 y GPT-6 Luna del servicio Alfalfa (la suscripción CUY: el código lo lee AlfalfaCode
del Llavero o de `ALFALFA_CODIGO`; nada de claves directas de OpenAI ni de Anthropic), esfuerzo bajo, temperatura 0 y
sin herramientas:

- `json`: el prompt de hoy sin ejemplos (`Catalogo.manual(ejemplos=False)`);
- `json_ej`: el prompt de hoy con un ejemplo por componente (`Catalogo.manual()`);
- `a2ui_ej`: A2UI v1.0 con los mismos ejemplos traducidos (los eventos llevan las acciones de PACAI: una adaptación).

50 tareas (altas y cambios sobre el tablero, componentes base y de CuyMercado). Por petición: válida (pasa el catálogo
y el árbol sin un solo error), exacta (además hace lo pedido), una corrección con los errores si falla, tokens y
coste. Con `--tope` deja de lanzar peticiones cuando el gasto estimado lo alcanza.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

import httpx  # noqa: E402

from alfalfa.pacai import ui_lang  # noqa: E402
from alfalfa.pacai.ui import Catalogo, Vistas, ascii_de  # noqa: E402

DECLARACION = json.loads((RAIZ / 'tests' / 'conformidad' / 'catalogo-cuymercado.json').read_text(encoding='utf-8'))
CAT = Catalogo.de(DECLARACION, {})
# PACAI UI Lang 2 (rama claude/pacai-ui-lang): el mismo catálogo sin las piezas declarativas, para enseñarle al modelo
# lo mismo que al JSON. `positional` e `inherit` sí, que son parte del formato.
CAT_LANG = Catalogo.de({k: v for k, v in DECLARACION.items() if k not in ('pieces', 'queries')},
                       {'ALFALFA_PACAI_UI_LANG': '1'})
MODELOS = ('claude-haiku-5-5', 'gpt-6-luna')
BRAZOS = ('json', 'json_ej', 'a2ui_ej', 'lang2')
VALLAS = {'json': 'pacai-ui', 'json_ej': 'pacai-ui', 'a2ui_ej': 'a2ui', 'lang2': 'pacai-ui-lang'}
MAX_SALIDA = 1500
CIERRE = '\nResponde sólo con el bloque de la pantalla, sin texto antes ni después, sin herramientas ni comentarios.'
B = {'period': {'$': 'periodo'}, 'family': {'$': 'familia'}}
TABLERO = [
    {'id': 'root', 'component': 'Stack', 'props': {'direction': 'vertical', 'gap': 'medium'},
     'children': ['filtros', 'cifras', 'p_bajo', 'fuente']},
    {'id': 'filtros', 'component': 'Filters', 'props': dict(B)},
    {'id': 'cifras', 'component': 'Grid', 'props': {'columns': 4}, 'children': ['c_subieron', 'c_bajaron']},
    {'id': 'c_subieron', 'component': 'Stat', 'props': {'label': 'Subieron', 'icon': 'subio', **B,
                                                        'query': {'tipo': 'totales', 'campo': 'subieron'}}},
    {'id': 'c_bajaron', 'component': 'Stat', 'props': {'label': 'Bajaron', 'icon': 'bajo', **B,
                                                       'query': {'tipo': 'totales', 'campo': 'bajaron'}}},
    {'id': 'p_bajo', 'component': 'Panel', 'props': {'title': 'Lo que más bajó'}, 'children': ['bajo_top']},
    {'id': 'bajo_top', 'component': 'PriceBars', 'props': {**B, 'query': {'tipo': 'productos', 'orden': 'baja',
                                                                         'limite': 6}}},
    {'id': 'fuente', 'component': 'Text', 'props': {'style': 'caption',
                                                   'text': 'Fuente: MIDAGRI · Gran Mercado Mayorista de Lima.'}},
]


def _compacto(o) -> str:
    return json.dumps(o, ensure_ascii=False, separators=(',', ':'))


# -- A2UI ------------------------------------------------------------------------------------------------------
FORMATO_A2UI = '''Pantallas en el chat: escribe cada pantalla en un bloque ```a2ui con mensajes A2UI v1.0, uno por línea:
{"version":"1.0","createSurface":{"surfaceId":"<view>","catalogId":"cuymercado"}}
{"version":"1.0","updateComponents":{"surfaceId":"<view>","components":[{"id":"root","component":"Stack","direction":"vertical","children":["titulo"]},{"id":"titulo","component":"Text","text":"Hola","style":"title"}]}}
- Cada componente lleva id, component, sus props al mismo nivel, children (ids) y, si tiene eventos,
  events ({"press":{"send":"nombre","with":["clave"]}}, o {"open_url":"https://…"}). La raíz se llama root.
- Leer el estado: {"@path":"/clave"} (también dentro de query).
- Cambio de una pantalla que ya existe: sólo updateComponents con los componentes nuevos o cambiados, enteros
  (un id repetido sustituye al componente). Para quitar uno, sácalo de los children de su padre.
- Input y Select guardan solos lo que escribe el usuario en la clave de su name. change sólo admite set.'''  # noqa: E501


def _a_enlace(valor, clave='@path'):
    if isinstance(valor, dict):
        if set(valor) == {'$'}:
            return {clave: '/' + valor['$']}
        return {k: _a_enlace(v, clave) for k, v in valor.items()}
    if isinstance(valor, list):
        return [_a_enlace(v, clave) for v in valor]
    return valor


def _de_enlace(valor, clave='@path'):
    if isinstance(valor, dict):
        if set(valor) == {clave} and isinstance(valor[clave], str):
            return {'$': valor[clave].strip('/').replace('/', '.')}
        return {k: _de_enlace(v, clave) for k, v in valor.items()}
    if isinstance(valor, list):
        return [_de_enlace(v, clave) for v in valor]
    return valor


def a_a2ui(nodo):
    c = {'id': nodo['id'], 'component': nodo['component'], **_a_enlace(nodo.get('props') or {})}
    for k in ('children', 'events'):
        if k in nodo:
            c[k] = nodo[k]
    return c


def tablero_a2ui() -> str:
    crear = {'version': '1.0', 'createSurface': {'surfaceId': 'tablero', 'catalogId': 'cuymercado'}}
    poner = {'version': '1.0', 'updateComponents': {'surfaceId': 'tablero', 'components': [a_a2ui(n) for n in TABLERO]}}
    return _compacto(crear) + '\n' + _compacto(poner)


def mapa_de_a2ui(lineas, base=None):
    mapa = dict(base or {})
    for linea in lineas:
        m = json.loads(linea)
        if not isinstance(m, dict) or m.get('version') != '1.0':
            raise ValueError('Cada mensaje A2UI lleva "version":"1.0".')
        cuerpo = m.get('createSurface') or m.get('updateComponents')
        if not isinstance(cuerpo, dict):
            raise ValueError('Mensaje A2UI desconocido.')
        for c in cuerpo.get('components') or []:
            if not isinstance(c, dict) or not isinstance(c.get('id'), str) or not isinstance(c.get('component'), str):
                raise ValueError('Un componente A2UI sin id o component.')
            nodo = {'id': c['id'], 'component': c['component']}
            props = _de_enlace({k: v for k, v in c.items() if k not in ('id', 'component', 'children', 'events')})
            if props:
                nodo['props'] = props
            if c.get('children'):
                nodo['children'] = c['children']
            if c.get('events'):
                nodo['events'] = c['events']
            mapa[c['id']] = nodo
    return mapa


def _vistas_lang():
    vistas = Vistas(CAT_LANG)
    vistas.aplicar({'view': 'tablero', 'lang': True}, ui_lang.a_lang(TABLERO, CAT_LANG).splitlines())
    return vistas


def sistema(brazo: str) -> str:
    if brazo == 'lang2':
        return CAT_LANG.manual() + CIERRE
    if brazo == 'json':
        return CAT.manual(ejemplos=False) + CIERRE
    if brazo == 'json_ej':
        return CAT.manual() + CIERRE
    componentes = 'Componentes:' + CAT.manual(ejemplos=False).split('Componentes:', 1)[1]
    ejemplos = ('\nEjemplos, un componente A2UI por línea (van dentro de components; copia la forma, los ids y los '
                'textos son tuyos):\n' + '\n'.join(_compacto(a_a2ui(n)) for n in CAT.ejemplos()) + '\n')
    return FORMATO_A2UI + '\n\n' + componentes + ejemplos + CIERRE


# -- comprobaciones de las tareas ---------------------------------------------------------------------------------
def _todos(n, c):
    return [x for x in n.values() if x['component'] == c]


def _p(x):
    return x.get('props') or {}


def _q(x):
    return _p(x).get('query') or {}


def _envio(x, evento):
    accion = (x.get('events') or {}).get(evento)
    accion = accion[0] if isinstance(accion, list) and accion else accion
    return accion if isinstance(accion, dict) else {}


def _texto(n, trozo):
    return [t for t in _todos(n, 'Text') if isinstance(_p(t).get('text'), str)
            and trozo.lower() in _p(t)['text'].lower()]


def _estilo(n, trozo, estilo):
    return any(_p(t).get('style') == estilo for t in _texto(n, trozo))


def _productos(x):
    return sorted(ascii_de(p, minusculas=True) for p in _q(x).get('productos') or [] if isinstance(p, str))


def _enlazado(x):
    return _p(x).get('period') == {'$': 'periodo'} and _p(x).get('family') == {'$': 'familia'}


def _opciones(s):
    return sorted(ascii_de(str(o.get('value')), minusculas=True) for o in _p(s).get('options') or []
                  if isinstance(o, dict))


def _hijos(n, ident):
    return n.get(ident, {}).get('children', [])


def _ultimo(n):
    hijos = _hijos(n, 'root')
    return n.get(hijos[-1], {}) if hijos else {}


def _form(n, envio, campos):
    forms = [f for f in _todos(n, 'Form') if _envio(f, 'submit').get('send') == envio]
    return len(forms) == 1 and len(_envio(forms[0], 'submit').get('with') or []) == campos


def _botones(n, envio):
    return [b for b in _todos(n, 'Button') if _envio(b, 'press').get('send') == envio]


def _variante(n, envio, variante):
    botones = _botones(n, envio)
    return len(botones) == 1 and _p(botones[0]).get('variant') == variante


def _stats(n):
    return sorted(_q(s).get('campo') for s in _todos(n, 'Stat'))


def _barras(n, orden, limite):
    return [b for b in _todos(n, 'PriceBars') if _q(b).get('orden') == orden and _q(b).get('limite') == limite]


def _lineas(n, productos):
    return [x for x in _todos(n, 'PriceLines') if _productos(x) == sorted(productos)]


def _kind(n, kind, **extra):
    return any(_p(i).get('kind') == kind and all(_p(i).get(k) == v for k, v in extra.items())
               for i in _todos(n, 'Input'))


def _grafico(n, kind, **extra):
    return [c for c in _todos(n, 'Chart') if _p(c).get('kind') == kind
            and all(f(_p(c)) for f in extra.values())]


def _columnas(n, numero):
    return any(_p(g).get('columns') == numero for g in _todos(n, 'Grid'))


def _direccion(n, direccion):
    return any(_p(s).get('direction') == direccion for s in _todos(n, 'Stack'))


def _titulo_panel(n, ident, titulo):
    return _p(n.get(ident, {})).get('title') == titulo


A = [  # altas
    ('a_tickets', 'Una tabla de tickets (view=tickets) con columnas id, título y prioridad y tres filas: T-1 «Login '
     'falla» Alta, T-2 «Botón roto» Media, T-3 «Texto mal» Baja. Al pulsar una fila manda abrir_ticket.',
     lambda n: any(len(_p(t).get('columns') or []) == 3 and len(_p(t).get('rows') or []) == 3
                   and _envio(t, 'row_press').get('send') == 'abrir_ticket' for t in _todos(n, 'Table'))),
    ('a_contacto', 'Un formulario de contacto (view=contacto): nombre (texto, obligatorio), correo válido '
     '(obligatorio) y mensaje (multilínea). «Mandar» envía contacto con los tres.',
     lambda n: _form(n, 'contacto', 3) and _kind(n, 'email', required=True) and _kind(n, 'multiline')),
    ('a_suscripcion', 'Un formulario de suscripción (view=suscripcion): correo (obligatorio) y frecuencia (Select '
     'con diaria, semanal y mensual). «Suscribirme» envía suscribir con los dos.',
     lambda n: _form(n, 'suscribir', 2)
     and any(_opciones(s) == ['diaria', 'mensual', 'semanal'] for s in _todos(n, 'Select'))),
    ('a_tareas', 'Una lista de 4 tareas pendientes (view=tareas), inventadas, con título y subtítulo. Al pulsar una '
     'manda ver_tarea.',
     lambda n: any(len(_p(x).get('items') or []) == 4 and _envio(x, 'item_press').get('send') == 'ver_tarea'
                   for x in _todos(n, 'List'))),
    ('a_tarjetas', 'Tres tarjetas en rejilla (view=frutas): Palta, Limón y Papa, cada una con una etiqueta y un botón '
     '«Abrir fuente» que abre https://ejemplo.pe/<producto>.',
     lambda n: len(_todos(n, 'Card')) == 3 and _direccion(n, 'grid')
     and sum('open_url' in _envio(b, 'press') for b in _todos(n, 'Button')) == 3),
    ('a_barras', 'Un gráfico de barras (view=ventas) con las ventas de enero, febrero y marzo: 120, 150 y 90.',
     lambda n: bool(_grafico(n, 'bar', labels=lambda p: len(p.get('labels') or []) == 3,
                             valores=lambda p: [s.get('values') for s in p.get('series') or []] == [[120, 150, 90]]))),
    ('a_torta', 'Un gráfico de torta (view=gastos) con alquiler 50, comida 30 y transporte 20.',
     lambda n: bool(_grafico(n, 'pie', labels=lambda p: len(p.get('labels') or []) == 3))),
    ('a_lineas', 'Un gráfico de líneas (view=precios) con dos series, Palta y Limón, de lunes a miércoles (valores '
     'inventados).',
     lambda n: bool(_grafico(n, 'line', series=lambda p: len(p.get('series') or []) == 2))),
    ('a_imagen', 'Una imagen de https://ejemplo.pe/palta.png con texto alternativo «Palta» y debajo una nota en estilo '
     'caption «Foto de referencia».',
     lambda n: any(_p(i).get('src') == 'https://ejemplo.pe/palta.png' for i in _todos(n, 'Image'))
     and _estilo(n, 'referencia', 'caption')),
    ('a_codigo', 'Un título «Cómo instalar» y un bloque de código bash con «pip install alfalfa».',
     lambda n: any(_p(c).get('language') == 'bash' and 'pip install' in _p(c).get('code', '')
                   for c in _todos(n, 'Code')) and _estilo(n, 'instalar', 'title')),
    ('a_confirmar', 'Una confirmación (view=confirmar): el texto «¿Borro el archivo?» y dos botones, «Confirmar» '
     '(primary) que manda confirmar y «Cancelar» (secondary) que manda cancelar.',
     lambda n: _variante(n, 'confirmar', 'primary') and len(_botones(n, 'cancelar')) == 1),
    ('a_moneda', 'Un Select «Moneda» con Soles y Dólares, y debajo un texto que muestre la moneda elegida leyendo el '
     'estado (sin llamarte).',
     lambda n: any(_p(t).get('text') == {'$': _p(s).get('name')} for s in _todos(n, 'Select')
                   for t in _todos(n, 'Text'))),
    ('a_reserva', 'Un formulario de reserva (view=reserva): fecha (obligatoria) y número de personas. «Reservar» '
     'envía reservar con los dos.',
     lambda n: _form(n, 'reservar', 2) and _kind(n, 'date') and _kind(n, 'number')),
    ('a_edad', 'Pide la edad (un número, con el texto de ayuda «p. ej. 30») y un botón «Calcular» que mande '
     'calcular_edad con la edad.',
     lambda n: any(_p(i).get('kind') == 'number' and _p(i).get('placeholder') for i in _todos(n, 'Input'))
     and (len(_botones(n, 'calcular_edad')) == 1 or _form(n, 'calcular_edad', 1))),
    ('a_detalle', 'Una tarjeta «Palta» con subtítulo «Fruta», meta «hoy», y el detalle «Sube un 3 % esta semana»; al '
     'pulsarla manda abrir_producto.',
     lambda n: any(_p(c).get('title') == 'Palta' and _p(c).get('detail')
                   and _envio(c, 'press').get('send') == 'abrir_producto' for c in _todos(n, 'Card'))),
    ('a_tabla_grafico', 'Una tabla con las ventas por mes (enero 120, febrero 150) y debajo un gráfico de barras con '
     'los mismos datos.',
     lambda n: len(_todos(n, 'Table')) == 1 and bool(_grafico(n, 'bar'))),
    ('a_botonera', 'Tres botones en fila: «Guardar» (primary, manda guardar), «Duplicar» (secondary, manda duplicar) '
     'y «Borrar» (danger, manda borrar).',
     lambda n: _variante(n, 'guardar', 'primary') and len(_botones(n, 'duplicar')) == 1
     and _variante(n, 'borrar', 'danger') and _direccion(n, 'horizontal')),
    ('a_borrar_cuenta', 'Un aviso «Esta acción no se puede deshacer» y un botón «Borrar cuenta» de peligro que manda '
     'borrar_cuenta.',
     lambda n: _variante(n, 'borrar_cuenta', 'danger')),
    ('c_tablero', 'Dibuja el tablero de CuyMercado (view=tablero): los filtros; una rejilla de 4 columnas con dos '
     'cifras de totales, «Subieron» (icono subio) y «Bajaron» (icono bajo); un panel «Lo que más bajó» con las barras '
     'de los 6 productos que más bajaron; y al final la nota «Fuente: MIDAGRI · Gran Mercado Mayorista de Lima.» en '
     'estilo caption.',
     lambda n: len(_todos(n, 'Filters')) == 1 and _stats(n) == ['bajaron', 'subieron'] and _barras(n, 'baja', 6)
     and all(_enlazado(x) for c in ('Filters', 'Stat', 'PriceBars') for x in _todos(n, c))),
    ('c_subidas', 'Un tablero de subidas (view=subidas): los filtros, un panel «Lo que más subió» con las barras de '
     'los 5 que más subieron y otro panel «Historial» con el historial de papa y cebolla.',
     lambda n: bool(_barras(n, 'sube', 5)) and bool(_lineas(n, ['cebolla', 'papa'])) and len(_todos(n, 'Panel')) == 2),
    ('c_bulbos', 'Un panel «Cebolla y ajo» con el historial de cebolla y ajo y, debajo del panel, la nota «Precios '
     'por kilo» en estilo caption.',
     lambda n: bool(_lineas(n, ['ajo', 'cebolla'])) and _estilo(n, 'kilo', 'caption')),
    ('c_tres_cifras', 'Sólo las tres cifras de totales en una rejilla de 3 columnas: subieron (icono subio), bajaron '
     '(icono bajo) e iguales (icono igual).',
     lambda n: _stats(n) == ['bajaron', 'iguales', 'subieron'] and _columnas(n, 3)
     and all(_enlazado(s) for s in _todos(n, 'Stat'))),
    ('c_palta', 'Un panel «Palta» con el historial de precios de la palta.',
     lambda n: bool(_lineas(n, ['palta'])) and any(_p(x).get('title') == 'Palta' for x in _todos(n, 'Panel'))),
    ('c_top10', 'Los filtros y debajo las barras de los 10 productos que más subieron.',
     lambda n: len(_todos(n, 'Filters')) == 1 and bool(_barras(n, 'sube', 10))),
    ('c_lado', 'Dos paneles lado a lado: «Bajan» con las barras de los 5 que más bajan y «Suben» con las de los 5 que '
     'más suben.',
     lambda n: bool(_barras(n, 'baja', 5)) and bool(_barras(n, 'sube', 5)) and _direccion(n, 'horizontal')),
    ('c_completo', 'Un tablero (view=semana): filtros, una rejilla de 2 columnas con subieron y bajaron, un panel '
     '«Historial» con papa, cebolla y ajo, y la nota «Datos de MIDAGRI» en caption.',
     lambda n: _stats(n) == ['bajaron', 'subieron'] and bool(_lineas(n, ['ajo', 'cebolla', 'papa']))
     and _columnas(n, 2)),
    ('c_tarjeta_cifra', 'Una tarjeta «Resumen de hoy» con dentro la cifra de iguales (icono igual).',
     lambda n: _stats(n) == ['iguales'] and any(
         _p(c).get('title') == 'Resumen de hoy' and any(n.get(h, {}).get('component') == 'Stat'
                                                        for h in c.get('children', []))
         for c in _todos(n, 'Card'))),
    ('c_titulo_barras', 'Un título «Mercado de hoy» y las barras de los 6 productos que más bajaron.',
     lambda n: bool(_barras(n, 'baja', 6)) and _estilo(n, 'mercado', 'title')),
    ('c_alerta', 'Los filtros y un formulario para suscribirse a alertas de un producto (Select con palta, limón y '
     'papa) que envía alerta con el producto.',
     lambda n: len(_todos(n, 'Filters')) == 1 and _form(n, 'alerta', 1)
     and any(_opciones(s) == ['limon', 'palta', 'papa'] for s in _todos(n, 'Select'))),
    ('c_citricos', 'Un panel «Historial de cítricos» con el historial de limón y naranja, y un botón «Descargar CSV» '
     'que manda descargar_csv.',
     lambda n: bool(_lineas(n, ['limon', 'naranja'])) and len(_botones(n, 'descargar_csv')) == 1),
    ('c_cifra_titulo', 'Un texto de título «Cómo va el mercado» y una rejilla de 2 columnas con la cifra de subieron '
     'y la de bajaron.',
     lambda n: _stats(n) == ['bajaron', 'subieron'] and _estilo(n, 'mercado', 'title')),
    ('c_select_lineas', 'Un Select de producto (palta, limón, papa) y el historial de palta, limón y papa debajo.',
     lambda n: bool(_lineas(n, ['limon', 'palta', 'papa']))
     and any(_opciones(s) == ['limon', 'palta', 'papa'] for s in _todos(n, 'Select'))),
]

C = [  # cambios sobre el tablero
    ('p_titulo_hist', 'Cambia el título del panel p_bajo a «Bajas e historial» y añade dentro, al final, el '
     'historial de palta y limón.',
     lambda n: _titulo_panel(n, 'p_bajo', 'Bajas e historial')
     and any(_productos(n[h]) == ['limon', 'palta'] for h in _hijos(n, 'p_bajo')[1:] if h in n)),
    ('p_iguales', 'Quita la cifra «Bajaron» y pon en su lugar, al final de la rejilla, una cifra «Iguales» con el '
     'icono igual.',
     lambda n: 'c_bajaron' not in n and _stats(n) == ['iguales', 'subieron']),
    ('p_mover_fuente', 'Mueve la nota de la fuente al principio, antes de los filtros; lo demás queda igual.',
     lambda n: _hijos(n, 'root') == ['fuente', 'filtros', 'cifras', 'p_bajo']),
    ('p_titulo_limite', 'Cambia el título del panel p_bajo a «Bajas de hoy» y deja sus barras en 3 productos.',
     lambda n: _titulo_panel(n, 'p_bajo', 'Bajas de hoy') and _q(n.get('bajo_top', {})).get('limite') == 3),
    ('p_historial_papa', 'Añade al final de la pantalla un panel «Historial» con el historial de papa.',
     lambda n: _hijos(n, 'root')[:4] == ['filtros', 'cifras', 'p_bajo', 'fuente'] and len(_hijos(n, 'root')) == 5
     and bool(_lineas(n, ['papa']))),
    ('p_borrar_panel', 'Borra el panel p_bajo con lo que tiene dentro.',
     lambda n: 'p_bajo' not in n and 'bajo_top' not in n and _hijos(n, 'root') == ['filtros', 'cifras', 'fuente']),
    ('m_columnas', 'Cambia el número de columnas de la rejilla a 2.',
     lambda n: _p(n.get('cifras', {})).get('columns') == 2 and _stats(n) == ['bajaron', 'subieron']),
    ('m_anadir_iguales', 'Añade una cifra «Iguales» (icono igual) al final de la rejilla, sin quitar nada.',
     lambda n: len(_hijos(n, 'cifras')) == 3 and _q(n.get(_hijos(n, 'cifras')[-1], {})).get('campo') == 'iguales'),
    ('m_fuente_texto', 'Cambia la nota de la fuente a «Fuente: MIDAGRI.»',
     lambda n: _p(n.get('fuente', {})).get('text') == 'Fuente: MIDAGRI.'),
    ('m_suben', 'Cambia las barras de p_bajo para que muestren lo que más subió y el título del panel a «Lo que más '
     'subió».',
     lambda n: _q(n.get('bajo_top', {})).get('orden') == 'sube' and _titulo_panel(n, 'p_bajo', 'Lo que más subió')),
    ('m_boton', 'Añade al final un botón «Actualizar» que mande actualizar.',
     lambda n: _ultimo(n).get('component') == 'Button' and len(_botones(n, 'actualizar')) == 1),
    ('m_quitar_fuente', 'Quita la nota de la fuente.',
     lambda n: 'fuente' not in n and _hijos(n, 'root') == ['filtros', 'cifras', 'p_bajo']),
    ('m_nota_panel', 'Añade dentro de p_bajo, antes de las barras, un texto «Precios en S/ por kg» en estilo caption.',
     lambda n: len(_hijos(n, 'p_bajo')) == 2 and _hijos(n, 'p_bajo')[1] == 'bajo_top'
     and _p(n.get(_hijos(n, 'p_bajo')[0], {})).get('style') == 'caption'),
    ('m_icono', 'Cambia el icono de la cifra «Subieron» a igual.',
     lambda n: _p(n.get('c_subieron', {})).get('icon') == 'igual'),
    ('m_sin_filtros', 'Quita los filtros y añade al final un panel «Historial» con el historial de palta y limón.',
     lambda n: 'filtros' not in n and bool(_lineas(n, ['limon', 'palta']))
     and _hijos(n, 'root')[:3] == ['cifras', 'p_bajo', 'fuente']),
    ('m_intercambiar', 'Intercambia el orden de las dos cifras de la rejilla.',
     lambda n: _hijos(n, 'cifras') == ['c_bajaron', 'c_subieron']),
    ('m_panel_antes', 'Pon el panel p_bajo antes de la rejilla de cifras.',
     lambda n: _hijos(n, 'root') == ['filtros', 'p_bajo', 'cifras', 'fuente']),
    ('m_limite10', 'Cambia el límite de las barras a 10.',
     lambda n: _q(n.get('bajo_top', {})).get('limite') == 10 and _q(n.get('bajo_top', {})).get('orden') == 'baja'),
    ('m_formulario', 'Añade debajo de la fuente un formulario con un campo de correo (obligatorio) y el botón '
     '«Avisarme», que envía avisar con el correo.',
     lambda n: _form(n, 'avisar', 1) and _ultimo(n).get('component') == 'Form'),
    ('m_muted', 'Pon la nota de la fuente en estilo muted.',
     lambda n: _p(n.get('fuente', {})).get('style') == 'muted'),
]
TAREAS = [(i, 'alta', t, f) for i, t, f in A] + [(i, 'cambio', t, f) for i, t, f in C]


def mensaje_de_tarea(brazo, tarea) -> str:
    _, tipo, texto, _ = tarea
    if tipo == 'alta':
        return texto
    if brazo == 'lang2':
        return ('Pantalla actual (view=tablero), con su revisión:\n' + _vistas_lang().resumen_lang('tablero')
                + '\n\nCambio que pide el usuario: ' + texto)
    pantalla = tablero_a2ui() if brazo == 'a2ui_ej' else '\n'.join(_compacto(n) for n in TABLERO)
    return 'Pantalla actual (view=tablero), lo que mandaste:\n' + pantalla + '\n\nCambio que pide el usuario: ' + texto


# -- validación -------------------------------------------------------------------------------------------------
_VALLA = re.compile(r'^\s*```(pacai-ui-lang|pacai-ui|a2ui)((?:\s+[a-z_]+=\S+)*)\s*$')


def bloque(texto):
    attrs, lineas = None, []
    for linea in texto.splitlines():
        if attrs is None:
            m = _VALLA.match(linea)
            if m:
                attrs = dict(a.split('=', 1) for a in m.group(2).split())
                attrs['valla'] = m.group(1)
            continue
        if linea.strip().startswith('```'):
            break
        if linea.strip():
            lineas.append(linea.strip())
    return attrs, lineas


def _vivos(mapa):
    vivos, pila = set(), ['root']
    while pila:
        i = pila.pop()
        if i in mapa and i not in vivos:
            vivos.add(i)
            pila += mapa[i].get('children', [])
    return {i: n for i, n in mapa.items() if i in vivos}


def validar(brazo, tarea, texto):
    """(válida, exacta, errores). Un nodo que no cuelga de root no se dibuja y no cuenta."""
    _, tipo, _, comprobar = tarea
    attrs, lineas = bloque(texto)
    if attrs is None:
        return False, False, ['No hay bloque de pantalla.']
    errores = []
    if attrs['valla'] != VALLAS[brazo]:
        errores.append('Valla equivocada.')
    if brazo == 'lang2':
        attrs_lang = {k: v for k, v in attrs.items() if k != 'valla'} | {'lang': True}
        if tipo == 'cambio':
            vistas = _vistas_lang()
            if 'rev' not in attrs_lang:
                errores.append('El cambio no lleva rev=N.')
        else:
            vistas = Vistas(CAT_LANG)
        data, fallos = vistas.aplicar(attrs_lang, lineas)
        errores += fallos
        vista = vistas.vistas.get(attrs_lang.get('view', 'tablero' if tipo == 'cambio' else ''))
        mapa = _vivos(dict(vista.nodos) if vista else {})
        if data is None and not fallos:
            errores.append('El bloque no cambió nada.')
    elif brazo == 'a2ui_ej':
        try:
            base = mapa_de_a2ui(tablero_a2ui().splitlines()) if tipo == 'cambio' else None
            mapa = _vivos(mapa_de_a2ui(lineas, base))
        except (ValueError, TypeError, KeyError) as exc:
            return False, False, ['No se puede leer: ' + str(exc)[:200]]
        errores += [f'{i}: ' + '; '.join(e[:2]) for i, x in mapa.items() if (e := CAT.errores_del_nodo(x))]
    else:
        vistas = Vistas(CAT)
        if tipo == 'cambio':
            vistas.aplicar({'view': 'tablero'}, [_compacto(n) for n in TABLERO])
        attrs_json = {k: v for k, v in attrs.items() if k != 'valla'}
        data, fallos = vistas.aplicar(attrs_json, lineas)
        errores += fallos
        vista = vistas.vistas.get(attrs_json.get('view', 'tablero' if tipo == 'cambio' else ''))
        mapa = _vivos(dict(vista.nodos) if vista else {})
        if data is None and not fallos:
            errores.append('El bloque no cambió nada.')
    if 'root' not in mapa:
        errores.append('Falta root.')
    for i, x in mapa.items():
        errores += [f'{h} no existe (hijo de {i}).' for h in x.get('children', []) if h not in mapa]
    try:
        exacta = not errores and bool(comprobar(mapa))
    except (KeyError, TypeError, AttributeError, IndexError):
        exacta = False
    return not errores, exacta, errores


# -- peticiones -------------------------------------------------------------------------------------------------
async def pedir(cliente, modelo, mensajes):
    t0 = time.monotonic()
    respuesta = await cliente.post('/api/chat', json={
        'model': modelo, 'messages': mensajes, 'stream': False, 'think': False,
        'options': {'temperature': 0, 'num_predict': MAX_SALIDA}})
    if respuesta.status_code >= 400:
        raise RuntimeError(f'HTTP {respuesta.status_code}: {respuesta.text[:300]}')
    dato = respuesta.json()
    texto = (dato.get('message') or {}).get('content') or ''
    return texto, dato.get('prompt_eval_count') or 0, dato.get('eval_count') or 0, time.monotonic() - t0


def coste(precio, entrada, salida):
    return (entrada * precio['entrada'] + salida * precio['salida']) / 1e6


async def una(proveedor, modelo, brazo, tarea, repeticion):
    precio = proveedor.precio(modelo)
    mensajes = [{'role': 'system', 'content': sistema(brazo)},
                {'role': 'user', 'content': mensaje_de_tarea(brazo, tarea)}]
    fila = {'modelo': modelo, 'brazo': brazo, 'tarea': tarea[0], 'tipo': tarea[1], 'repeticion': repeticion}
    async with proveedor.cliente(httpx.Timeout(180, connect=10), tarea={'effort': 'low'}) as cliente:
        try:
            texto, entrada, salida, total = await pedir(cliente, modelo, mensajes)
        except Exception as exc:  # noqa: BLE001 - un fallo del servicio se anota
            return fila | {'error': str(exc)[:300], 'coste': 0}
        valida, exacta, errores = validar(brazo, tarea, texto)
        fila |= {'valida': valida, 'exacta': exacta, 'errores': errores[:6], 'entrada': entrada, 'salida': salida,
                 'total': total, 'coste': coste(precio, entrada, salida), 'respuesta': texto[:4000]}
        if valida:
            return fila | {'valida_tras': True, 'exacta_tras': exacta}
        peticion = ('El motor rechazó el bloque: ' + '; '.join(errores[:8])
                    + '\nVuelve a mandar el bloque entero, corregido.')
        try:
            texto2, entrada2, salida2, _ = await pedir(cliente, modelo, mensajes + [
                {'role': 'assistant', 'content': texto}, {'role': 'user', 'content': peticion}])
        except Exception as exc:  # noqa: BLE001
            return fila | {'error_correccion': str(exc)[:300], 'valida_tras': False, 'exacta_tras': False}
        valida2, exacta2, errores2 = validar(brazo, tarea, texto2)
        return fila | {'valida_tras': valida2, 'exacta_tras': exacta2, 'errores_tras': errores2[:6],
                       'entrada2': entrada2, 'salida2': salida2, 'coste2': coste(precio, entrada2, salida2),
                       'respuesta2': texto2[:4000]}


async def main() -> None:
    args = argparse.ArgumentParser()
    args.add_argument('--salida', required=True)
    args.add_argument('--repeticiones', type=int, default=2)
    args.add_argument('--simultaneas', type=int, default=6)
    args.add_argument('--tope', type=float, default=1.0, help='USD: no lanza más peticiones al alcanzarlo')
    args.add_argument('--brazos', default=','.join(BRAZOS))
    args.add_argument('--solo', help='tareas separadas por comas')
    opciones = args.parse_args()
    from alfalfa.proveedores.alfalfa import Alfalfa

    proveedor = Alfalfa()
    if not proveedor.configurado():
        sys.exit('El proveedor Alfalfa (suscripción CUY) no está activado en este equipo.')
    tareas = [t for t in TAREAS if not opciones.solo or t[0] in opciones.solo.split(',')]
    trabajos = [(m, b, t, r) for r in range(opciones.repeticiones) for t in tareas for m in MODELOS
                for b in opciones.brazos.split(',')]
    semaforo = asyncio.Semaphore(opciones.simultaneas)
    gastado = {'usd': 0.0, 'parado': False}
    salida = Path(opciones.salida).open('a', encoding='utf-8')

    async def correr(trabajo):
        async with semaforo:
            if gastado['usd'] >= opciones.tope:
                gastado['parado'] = True
                return
            fila = await una(proveedor, *trabajo)
            gastado['usd'] += fila.get('coste', 0) + fila.get('coste2', 0)
            salida.write(json.dumps(fila, ensure_ascii=False) + '\n')
            salida.flush()

    await asyncio.gather(*(correr(t) for t in trabajos))
    print(json.dumps({'gastado_usd': round(gastado['usd'], 4), 'parado_por_tope': gastado['parado']}))


if __name__ == '__main__':
    asyncio.run(main())
