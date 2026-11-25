"""Piloto de PACAI UI Lang frente al JSON de hoy, con modelos del servicio Alfalfa (la suscripción CUY).

    python scripts/piloto_pacai_ui_lang.py --salida resultados.jsonl [--repeticiones 2]

Usa sólo el proveedor `alfalfa` ya activado en este equipo: el código CUY lo lee AlfalfaCode del Llavero (o de
`ALFALFA_CODIGO`) y nunca pasa por este script. Nada de claves directas de OpenAI ni de Anthropic.

Por cada modelo, formato y tarea manda una petición en streaming (temperatura 0, sin herramientas) y mide:
- válida al primer intento: el bloque pasa el catálogo, el árbol y las piezas sin un solo diagnóstico;
- exacta: además hace lo que pedía la tarea (comprobaciones por tarea, abajo);
- tokens de entrada y salida (los que cuenta el servicio) y el coste con los precios del proveedor;
- TTFT, primera pieza (PACAI UI Lang: el primer nodo que publica `Flujo`; JSON y cambios: al cerrar el bloque,
  que es cuando el motor de hoy puede dibujar) y tiempo total;
- con un fallo, un solo reintento con los diagnósticos, y si queda válida después.
Es un piloto: con pocas peticiones por celda no certifica los umbrales de adopción.
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

from alfalfa.pacai import piezas, ui_lang  # noqa: E402
from alfalfa.pacai.ui import Catalogo, Vistas, ascii_de  # noqa: E402

CONFORMIDAD = RAIZ / 'tests' / 'conformidad'
DECLARACION = json.loads((CONFORMIDAD / 'catalogo-cuymercado.json').read_text(encoding='utf-8'))
MUESTRAS = json.loads((CONFORMIDAD / 'muestras-pacai-ui-lang.json').read_text(encoding='utf-8'))['muestras']
MODELOS = ('gpt-6-luna', 'claude-haiku-5-5')
MAX_SALIDA = 1500
CAT = Catalogo.de(DECLARACION, {ui_lang.FLAG: '1'})            # valida los dos formatos con las piezas
CAT_JSON = Catalogo.de(DECLARACION, {})

PIEZAS_JSON = '''Piezas: name crea estado y {"$":"name"} lo lee; Data publica slots tipados que sólo se leen en su subárbol.
Calc {"name":"m","preset":"margen","compra":{"$":"c"},"venta":{"$":"v"},"cantidad":{"$":"n"}} publica m.pct (fracción), m.u y m.total (dinero) y m.perdida.
Text {"text":"Margen:","value":{"$":"m.pct"}} escribe la etiqueta y el slot con su formato; "rojo":{"$":"m.perdida"} lo pinta en rojo; nulo es «—».
Alterna con "si":{"$":"vista","es":"valor"}: valor repite una option; Select sin required empieza en la primera.
Valida con required, kind, min, max y step (fecha "min":{"$":"q.hoy"}); Form bloquea su submit hasta que todo vale.
Data {"name":"q","query":{"tipo":…}} con children espera sus argumentos y gana la última respuesta: formPedido da hoy/lista; comparar da frase/series.'''  # noqa: E501

CIERRE = ('\nResponde sólo con el bloque de la pantalla, sin texto antes ni después, sin herramientas ni comentarios.')


def sistema(formato: str) -> str:
    if formato == 'lang':
        return CAT.manual() + CIERRE
    return CAT_JSON.manual() + '\n' + PIEZAS_JSON + '\n' + CIERRE


# -- tareas ---------------------------------------------------------------------------------------------------
def nodos_de(vista):
    return vista


def _todos(nodos, componente):
    return [n for n in nodos.values() if n['component'] == componente]


def _p(n):
    return n.get('props') or {}


def ok_tablero(n):
    stats = sorted(_p(s)['query'].get('campo') for s in _todos(n, 'Stat'))
    barras = _todos(n, 'PriceBars')
    textos = [t for t in _todos(n, 'Text') if 'MIDAGRI' in str(_p(t).get('text'))]
    return (len(_todos(n, 'Filters')) == 1 and stats == ['bajaron', 'subieron'] and len(barras) == 1
            and _p(barras[0])['query'].get('orden') == 'baja' and _p(barras[0])['query'].get('limite') == 6
            and bool(textos) and any(_p(g).get('columns') == 4 for g in _todos(n, 'Grid')))


def ok_margen(n):
    entradas = {_p(i)['name'] for i in _todos(n, 'Input') if _p(i).get('kind') == 'number'}
    calcs = [c for c in _todos(n, 'Calc') if _p(c).get('preset') == 'margen']
    if len(entradas) != 3 or len(calcs) != 1:
        return False
    c = _p(calcs[0])
    if {c.get(k, {}).get('$') if isinstance(c.get(k), dict) else None for k in ('compra', 'venta', 'cantidad')} \
            != entradas:
        return False
    nombre = c['name']
    leidos = {(_p(t).get('value') or {}).get('$') for t in _todos(n, 'Text')}
    rojos = {(_p(t).get('rojo') or {}).get('$') for t in _todos(n, 'Text') if isinstance(_p(t).get('rojo'), dict)}
    return {f'{nombre}.pct', f'{nombre}.u', f'{nombre}.total'} <= leidos and f'{nombre}.perdida' in rojos


def ok_vista(n):
    selects = [s for s in _todos(n, 'Select') if {o.get('value') for o in _p(s).get('options') or []}
               == {'resumen', 'detalle'}]
    if len(selects) != 1:
        return False
    clave = _p(selects[0])['name']
    estado = piezas.estado_inicial(n, {})
    resumen = set(piezas.visibles(n, {**estado, clave: 'resumen'}))
    detalle = set(piezas.visibles(n, {**estado, clave: 'detalle'}))
    stats = {s['id'] for s in _todos(n, 'Stat')}
    datos = {i for i, x in n.items() if x['component'] in ('Table', 'Chart', 'PriceBars', 'PriceLines')}
    return len(stats) == 3 and stats <= resumen and not stats & detalle and len(datos) >= 2 \
        and datos <= detalle and not datos & resumen


def ok_pedido(n):
    datos = [d for d in _todos(n, 'Data') if _p(d)['query'].get('tipo') == 'formPedido']
    forms = [f for f in _todos(n, 'Form') if (f.get('events') or {}).get('submit')]
    entradas = {_p(i).get('kind'): _p(i) for i in _todos(n, 'Input')}
    sel = [s for s in _todos(n, 'Select') if _p(s).get('required') is True]
    num = entradas.get('number', {})
    fecha = entradas.get('date', {})
    return (len(datos) == 1 and len(forms) == 1 and len(sel) == 1 and decimal_igual(num.get('min'), 1)
            and decimal_igual(num.get('max'), 500) and isinstance(fecha.get('min'), dict)
            and fecha['min'].get('$', '').endswith('.hoy') and 'email' in entradas)


def decimal_igual(a, b):
    return piezas.decimal(a) is not None and piezas.decimal(a) == piezas.decimal(b)


def ok_comparar(n):
    selects = {_p(s)['name'] for s in _todos(n, 'Select')}
    datos = [d for d in _todos(n, 'Data') if _p(d)['query'].get('tipo') == 'comparar']
    if len(selects) != 2 or len(datos) != 1:
        return False
    args = {v.get('$') for v in _p(datos[0])['query'].values() if isinstance(v, dict)}
    nombre = _p(datos[0])['name']
    frase = any((_p(t).get('value') or {}).get('$') == f'{nombre}.frase' for t in _todos(n, 'Text'))
    return args == selects and frase and bool(_todos(n, 'PriceLines') or _todos(n, 'Chart'))


def ok_variante(n):
    barras = [b for b in _todos(n, 'PriceBars') if _p(b)['query'].get('orden') == 'sube'
              and _p(b)['query'].get('limite') == 5]
    lineas = [x for x in _todos(n, 'PriceLines') if _productos(x) == ['cebolla', 'papa']]
    return len(barras) == 1 and len(lineas) == 1 and len(_todos(n, 'Panel')) >= 1


def _productos(nodo):
    """Los productos de una consulta sin tildes: «limón» y «limon» son el mismo para la tarea."""
    return sorted(ascii_de(p, minusculas=True) for p in (_p(nodo).get('query') or {}).get('productos') or []
                  if isinstance(p, str))


def ok_p1(n):
    hijo = [i for i in n.get('p_bajo', {}).get('children', [])
            if n.get(i, {}).get('component') == 'PriceLines' and _productos(n[i]) == ['limon', 'palta']]
    return _p(n.get('p_bajo', {})).get('title') == 'Bajas e historial' and len(hijo) == 1


def ok_p2(n):
    campos = [_p(n[i])['query'].get('campo') for i in n.get('cifras', {}).get('children', []) if i in n]
    iguales = [n[i] for i in n.get('cifras', {}).get('children', []) if i in n
               and _p(n[i])['query'].get('campo') == 'iguales']
    return 'c_bajaron' not in n and sorted(campos) == ['iguales', 'subieron'] and _p(iguales[0]).get('icon') == 'igual'


def ok_p3(n):
    return n['root'].get('children') == ['fuente', 'filtros', 'cifras', 'p_bajo']


def ok_p4(n):
    return (_p(n.get('p_bajo', {})).get('title') == 'Bajas de hoy'
            and _p(n.get('bajo_top', {})).get('query', {}).get('limite') == 3)


def ok_n1(n):
    lineas = [x for x in _todos(n, 'PriceLines') if _productos(x) == ['ajo', 'cebolla']]
    nota = [t for t in _todos(n, 'Text') if 'kilo' in str(_p(t).get('text')) and _p(t).get('style') == 'caption']
    paneles = [x for x in _todos(n, 'Panel') if _p(x).get('title') == 'Cebolla y ajo']
    return len(lineas) == 1 and len(nota) == 1 and len(paneles) == 1


def ok_n2(n):
    forms = [f for f in _todos(n, 'Form') if isinstance((f.get('events') or {}).get('submit'), dict)]
    if len(forms) != 1:
        return False
    envio = forms[0]['events']['submit']
    campos = {_p(i).get('kind', 'text'): _p(i) for i in _todos(n, 'Input')}
    return (envio.get('send') == 'contacto' and len(envio.get('with') or []) == 3
            and campos.get('email', {}).get('required') is True and 'multiline' in campos
            and campos.get('text', {}).get('required') is True)


def ok_n3(n):
    hijos = n['root'].get('children') or []
    ultimo = n.get(hijos[-1], {}) if hijos else {}
    dentro = [n[i] for i in ultimo.get('children', []) if i in n and n[i]['component'] == 'PriceLines']
    return (hijos[:4] == ['filtros', 'cifras', 'p_bajo', 'fuente'] and ultimo.get('component') == 'Panel'
            and _p(ultimo).get('title') == 'Historial' and len(dentro) == 1 and _productos(dentro[0]) == ['papa'])


def ok_n4(n):
    return 'p_bajo' not in n and 'bajo_top' not in n and n['root'].get('children') == ['filtros', 'cifras', 'fuente']


TAREAS = [
    ('tablero', 'alta', 'Dibuja el tablero de CuyMercado (view=tablero): los filtros; una rejilla de 4 columnas con '
     'dos cifras de totales, «Subieron» (icono subio) y «Bajaron» (icono bajo); un panel «Lo que más bajó» con las '
     'barras de los 6 productos que más bajaron; y al final la nota «Fuente: MIDAGRI · Gran Mercado Mayorista de '
     'Lima.» en estilo caption.', ok_tablero),
    ('margen', 'alta', 'Haz una calculadora de margen (view=margen): precio de compra, precio de venta y cantidad '
     '(números). Muestra el margen en %, la ganancia por unidad y la ganancia total, que se actualizan al escribir; '
     'si la venta es menor que la compra, las ganancias salen en rojo.', ok_margen),
    ('vista', 'alta', 'Haz una vista que cambia (view=vista): un Select «Ver» con resumen y detalle. En resumen, '
     'tres cifras de totales (subieron, bajaron, iguales). En detalle, las barras de los 6 que más bajaron y el '
     'historial de palta y limón. Al cambiar el Select se ve la otra parte sin volver a pedir la pantalla.',
     ok_vista),
    ('pedido', 'alta', 'Haz un formulario de pedido (view=pedido) con la consulta formPedido: producto (Select '
     'obligatorio con la lista de la consulta), cantidad entre 1 y 500 (de uno en uno), fecha de entrega que no sea '
     'pasada y correo válido, todos obligatorios. «Enviar» manda crear_pedido con los cuatro valores.', ok_pedido),
    ('comparar', 'alta', 'Compara dos productos de CuyMercado (view=comparar): dos Select de producto (palta, limón, '
     'papa), la frase de comparación de la consulta comparar con esos dos productos, y el historial de precios de '
     'los dos.', ok_comparar),
    ('variante', 'alta', 'Dibuja un tablero de subidas (view=subidas): los filtros, un panel «Lo que más subió» con '
     'las barras de los 5 productos que más subieron y otro panel «Historial» con el historial de papa y cebolla.',
     ok_variante),
    ('p1', 'cambio', 'Cambia el título del panel p_bajo a «Bajas e historial» y añade dentro, al final, el '
     'historial de palta y limón.', ok_p1),
    ('p2', 'cambio', 'Quita la cifra «Bajaron» y pon en su lugar, al final de la rejilla, una cifra «Iguales» con '
     'el icono igual.', ok_p2),
    ('p3', 'cambio', 'Mueve la nota de la fuente al principio, antes de los filtros; lo demás queda igual.', ok_p3),
    ('p4', 'cambio', 'Cambia el título del panel p_bajo a «Bajas de hoy» y deja sus barras en 3 productos.', ok_p4),
    # Nuevas para la segunda ronda (el prompt se corrigió mirando las diez de arriba).
    ('n1', 'alta', 'Dibuja (view=bulbos) un panel «Cebolla y ajo» con el historial de cebolla y ajo y, debajo del '
     'panel, la nota «Precios por kilo» en estilo caption.', ok_n1),
    ('n2', 'alta', 'Haz un formulario de contacto (view=contacto): nombre (texto, obligatorio), correo válido '
     '(obligatorio) y mensaje (multilínea). El botón «Mandar» envía contacto con los tres valores.', ok_n2),
    ('n3', 'cambio', 'Añade al final de la pantalla un panel «Historial» con el historial de papa.', ok_n3),
    ('n4', 'cambio', 'Borra el panel p_bajo con lo que tiene dentro.', ok_n4),
]


def pantalla_base(formato: str) -> Vistas:
    vistas = Vistas(CAT)
    if formato == 'lang':
        vistas.aplicar({'view': 'tablero', 'lang': True}, MUESTRAS['tablero']['lang'].splitlines())
    else:
        vistas.aplicar({'view': 'tablero'}, MUESTRAS['tablero']['json'].splitlines())
    return vistas


def mensaje_de_tarea(formato: str, tarea) -> str:
    nombre, tipo, texto, _ = tarea
    if tipo == 'alta':
        return texto
    if formato == 'lang':
        return ('Pantalla actual (view=tablero), con su revisión:\n' + pantalla_base('lang').resumen_lang('tablero')
                + '\n\nCambio que pide el usuario: ' + texto)
    return ('Pantalla actual (view=tablero), los nodos que mandaste:\n' + MUESTRAS['tablero']['json']
            + '\n\nCambio que pide el usuario: ' + texto)


# -- lectura de la respuesta en streaming ---------------------------------------------------------------------
_VALLA = re.compile(r'^\s*```(pacai-ui(?:-lang)?)((?:\s+[a-z_]+=\S+)*)\s*$')


class Lector:
    """Sigue el texto del modelo según llega: encuentra la valla y, en un alta compacta, alimenta `Flujo`."""

    def __init__(self, formato: str, tipo: str, t0: float):
        self.formato, self.tipo, self.t0 = formato, tipo, t0
        self.buffer, self.texto = '', ''
        self.attrs = None
        self.lineas: list[str] = []
        self.cerrada = False
        self.flujo = ui_lang.Flujo(CAT) if formato == 'lang' and tipo == 'alta' else None
        self.primera_pieza = None
        self.cierre = None

    def alimentar(self, trozo: str) -> None:
        self.texto += trozo
        self.buffer += trozo
        while '\n' in self.buffer:
            linea, self.buffer = self.buffer.split('\n', 1)
            self._linea(linea)

    def terminar(self) -> None:
        if self.buffer:
            self._linea(self.buffer)
            self.buffer = ''
        if self.attrs is not None and not self.cerrada:
            self._cerrar()

    def _cerrar(self) -> None:
        self.cerrada = True
        self.cierre = time.monotonic() - self.t0
        if self.primera_pieza is None and self.lineas:
            self.primera_pieza = self.cierre

    def _linea(self, linea: str) -> None:
        if self.cerrada:
            return
        if self.attrs is None:
            m = _VALLA.match(linea)
            if m:
                self.attrs = dict(a.split('=', 1) for a in m.group(2).split())
                self.attrs['valla'] = m.group(1)
            return
        if linea.strip().startswith('```'):
            self._cerrar()
            return
        if not linea.strip():
            return
        self.lineas.append(linea.strip())
        if self.flujo is not None and self.flujo.alimentar(linea + '\n') and self.primera_pieza is None:
            self.primera_pieza = time.monotonic() - self.t0


def validar(formato: str, tarea, lector: Lector, previo=None):
    """(válida, exacta, diagnósticos, estado para el reintento)."""
    nombre, tipo, _, comprobar = tarea
    if lector.attrs is None:
        return False, False, ['No hay bloque de pantalla.'], previo
    valla_ok = lector.attrs['valla'] == ('pacai-ui-lang' if formato == 'lang' else 'pacai-ui')
    if formato == 'lang' and tipo == 'alta':
        flujo = previo or lector.flujo
        if previo is not None:
            nodos, diagnosticos = flujo.reintentar('\n'.join(lector.lineas))
        else:
            nodos, diagnosticos = flujo.cerrar()
        textos = [d.texto() for d in diagnosticos] + ([] if valla_ok else ['Valla equivocada.'])
        mapa = {n['id']: n for n in nodos}
        return not textos, bool(mapa) and not textos and _seguro(comprobar, mapa), textos, flujo
    vistas = pantalla_base(formato) if tipo == 'cambio' else Vistas(CAT)
    if formato == 'lang':
        attrs = {k: v for k, v in lector.attrs.items() if k != 'valla'} | {'lang': True}
        data, errores = vistas.aplicar(attrs, lector.lineas)
        if 'rev' not in attrs:
            errores = ['El cambio no lleva rev=N.'] + errores
    else:
        attrs = {k: v for k, v in lector.attrs.items() if k != 'valla'}
        data, errores = vistas.aplicar(attrs, lector.lineas)
    nombre_vista = attrs.get('view', 'tablero' if tipo == 'cambio' else '')
    vista = vistas.vistas.get(nombre_vista)
    mapa = dict(vista.nodos) if vista else {}
    if formato == 'json':
        errores = list(errores) + [f'{i}: {m}' for i, m in piezas.errores_del_arbol(mapa, CAT.consultas)]
        if 'root' not in mapa:
            errores.append('Falta root.')
        for i, nodo in mapa.items():
            for hijo in nodo.get('children', []):
                if hijo not in mapa:
                    errores.append(f'{hijo} no existe (hijo de {i}).')
    if data is None and not errores:
        errores = ['El bloque no cambió nada.']
    if not valla_ok:
        errores = ['Valla equivocada.'] + list(errores)
    return not errores, bool(mapa) and not errores and _seguro(comprobar, mapa), list(errores), None


def _seguro(comprobar, mapa):
    try:
        return bool(comprobar(mapa))
    except (KeyError, TypeError, AttributeError, IndexError):
        return False


# -- peticiones -----------------------------------------------------------------------------------------------
ESFUERZO = {'valor': None}


async def pedir(cliente, modelo, mensajes, lector):
    t0 = lector.t0
    uso = {}
    ttft = None
    lector.trozos = 0
    async with cliente.stream('POST', '/api/chat', json={
            'model': modelo, 'messages': mensajes, 'stream': True,
            'options': {'temperature': 0, 'num_predict': MAX_SALIDA},
            **({'think': False} if ESFUERZO['valor'] == 'low' else {})}) as respuesta:
        if respuesta.status_code >= 400:
            cuerpo = (await respuesta.aread()).decode('utf-8', 'replace')[:300]
            raise RuntimeError(f'HTTP {respuesta.status_code}: {cuerpo}')
        async for linea in respuesta.aiter_lines():
            if not linea.strip():
                continue
            dato = json.loads(linea)
            trozo = (dato.get('message') or {}).get('content') or ''
            if trozo:
                lector.trozos += 1
                if ttft is None:
                    ttft = time.monotonic() - t0
                lector.alimentar(trozo)
            if dato.get('done'):
                uso = {k: dato.get(k) for k in ('prompt_eval_count', 'eval_count', 'alfalfa_cache')}
    lector.terminar()
    return ttft, time.monotonic() - t0, uso


def coste(precio, uso):
    entrada, salida = uso.get('prompt_eval_count') or 0, uso.get('eval_count') or 0
    return (entrada * precio['entrada'] + salida * precio['salida']) / 1e6


async def una(proveedor, modelo, formato, tarea, repeticion):
    nombre, tipo, _, _ = tarea
    mensajes = [{'role': 'system', 'content': sistema(formato)},
                {'role': 'user', 'content': mensaje_de_tarea(formato, tarea)}]
    precio = proveedor.precio(modelo)
    fila = {'modelo': modelo, 'formato': formato, 'tarea': nombre, 'tipo': tipo, 'repeticion': repeticion,
            'esfuerzo': ESFUERZO['valor']}
    tarea_cliente = {'effort': ESFUERZO['valor']} if ESFUERZO['valor'] else None
    async with proveedor.cliente(httpx.Timeout(180, connect=10), tarea=tarea_cliente) as cliente:
        lector = Lector(formato, tipo, time.monotonic())
        try:
            ttft, total, uso = await pedir(cliente, modelo, mensajes, lector)
        except Exception as exc:  # noqa: BLE001 - un fallo del servicio se anota, no para el piloto
            return fila | {'error': str(exc)[:300]}
        valida, exacta, diagnosticos, estado = validar(formato, tarea, lector)
        fila |= {'valida': valida, 'exacta': exacta, 'diagnosticos': diagnosticos[:6], 'ttft': ttft, 'total': total,
                 'primera_pieza': lector.primera_pieza, 'trozos': lector.trozos,
                 'entrada': uso.get('prompt_eval_count'),
                 'salida': uso.get('eval_count'), 'cache': uso.get('alfalfa_cache'), 'coste': coste(precio, uso),
                 'respuesta': lector.texto[:4000]}
        if not valida:
            if formato == 'lang' and tipo == 'alta' and estado is not None:
                peticion = estado.peticion([ui_lang.Diagnostico(None, None, d) for d in diagnosticos])
                peticion += '\nDevuélvelas en un bloque ```pacai-ui-lang con el mismo view.'
            else:
                peticion = ('El motor rechazó el bloque: ' + '; '.join(diagnosticos[:8])
                            + '\nVuelve a mandar el bloque entero, corregido.')
            mensajes2 = mensajes + [{'role': 'assistant', 'content': lector.texto},
                                    {'role': 'user', 'content': peticion}]
            lector2 = Lector(formato, tipo, time.monotonic())
            try:
                _, total2, uso2 = await pedir(cliente, modelo, mensajes2, lector2)
                valida2, exacta2, diag2, _ = validar(formato, tarea, lector2, estado if formato == 'lang' and
                                                     tipo == 'alta' else None)
                fila |= {'valida_tras_reintento': valida2, 'exacta_tras_reintento': exacta2,
                         'diagnosticos_reintento': diag2[:6], 'entrada_reintento': uso2.get('prompt_eval_count'),
                         'salida_reintento': uso2.get('eval_count'), 'coste_reintento': coste(precio, uso2),
                         'total_reintento': total2}
            except Exception as exc:  # noqa: BLE001
                fila |= {'error_reintento': str(exc)[:300]}
        else:
            fila |= {'valida_tras_reintento': True, 'exacta_tras_reintento': exacta}
        return fila


async def main() -> None:
    args = argparse.ArgumentParser()
    args.add_argument('--salida', required=True)
    args.add_argument('--repeticiones', type=int, default=2)
    args.add_argument('--simultaneas', type=int, default=4)
    args.add_argument('--solo', help='tareas separadas por comas')
    args.add_argument('--esfuerzo', choices=('low', 'medium', 'high'), help='esfuerzo que se pide al servicio')
    opciones = args.parse_args()
    ESFUERZO['valor'] = opciones.esfuerzo
    from alfalfa.proveedores.alfalfa import Alfalfa

    proveedor = Alfalfa()
    if not proveedor.configurado():
        sys.exit('El proveedor Alfalfa (suscripción CUY) no está activado en este equipo.')
    tareas = [t for t in TAREAS if not opciones.solo or t[0] in opciones.solo.split(',')]
    trabajos = [(m, f, t, r) for r in range(opciones.repeticiones) for t in tareas for m in MODELOS
                for f in ('json', 'lang')]
    semaforo = asyncio.Semaphore(opciones.simultaneas)
    salida = Path(opciones.salida).open('a', encoding='utf-8')

    async def correr(trabajo):
        async with semaforo:
            fila = await una(proveedor, *trabajo)
            salida.write(json.dumps(fila, ensure_ascii=False) + '\n')
            salida.flush()
            print(fila['modelo'], fila['formato'], fila['tarea'], fila.get('valida'), fila.get('exacta'),
                  fila.get('error', ''), flush=True)

    await asyncio.gather(*(correr(t) for t in trabajos))


if __name__ == '__main__':
    asyncio.run(main())
