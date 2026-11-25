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


# -- otros formatos de salida: A2UI v1.0 y json-render ----------------------------------------------------------
# Para comparar la validez al primer intento con el mismo catálogo. El modelo escribe el formato ajeno y aquí se
# convierte a nodos `pacai.ui/1` y se valida igual que los otros brazos. Los eventos (`events`, con las acciones de
# PACAI) son una adaptación nuestra: ni A2UI ni json-render tienen exactamente esas acciones.
FORMATO_A2UI = '''Pantallas en el chat: escribe cada pantalla en un bloque ```a2ui con mensajes A2UI v1.0, uno por línea:
{"version":"1.0","createSurface":{"surfaceId":"<view>","catalogId":"cuymercado"}}
{"version":"1.0","updateComponents":{"surfaceId":"<view>","components":[{"id":"root","component":"Stack","direction":"vertical","children":["titulo"]},{"id":"titulo","component":"Text","text":"Hola","style":"title"}]}}
- Cada componente lleva id, component, sus props al mismo nivel, children (ids) y, si tiene eventos,
  events ({"press":{"send":"nombre","with":["clave"]}}). La raíz se llama root.
- Leer el estado: {"@path":"/clave"} (también dentro de query); una salida de Calc o Data: {"@path":"/m/pct"}.
- Cambio de una pantalla que ya existe: sólo updateComponents con los componentes nuevos o cambiados, enteros
  (un id repetido sustituye al componente). Para quitar uno, sácalo de los children de su padre.
- Input y Select guardan solos lo que escribe el usuario en la clave de su name.'''  # noqa: E501
FORMATO_JR = '''Pantallas en el chat: escribe cada pantalla en un bloque ```json-render view=<view> con un spec de json-render:
{"root":"root","elements":{"root":{"type":"Stack","props":{"direction":"vertical"},"children":["titulo"]},"titulo":{"type":"Text","props":{"text":"Hola","style":"title"},"children":[]}}}
- Cada elemento lleva type, props, children (ids) y, si tiene eventos, events ({"press":{"send":"nombre","with":["clave"]}}).
  La raíz se llama root.
- Leer el estado: {"$state":"/clave"} (también dentro de query); una salida de Calc o Data: {"$state":"/m/pct"}.
- Cambio de una pantalla que ya existe: un bloque ```json-render view=<view> con operaciones JSON Patch sobre el spec,
  una por línea: {"op":"replace","path":"/elements/x/props/title","value":"…"}, {"op":"add","path":"/elements/x/children/-","value":"y"},
  {"op":"add","path":"/elements/y","value":{…}}, {"op":"remove","path":"/elements/y"}.
- Input y Select guardan solos lo que escribe el usuario en la clave de su name.'''  # noqa: E501
VALLAS = {'json': 'pacai-ui', 'lang': 'pacai-ui-lang', 'a2ui': 'a2ui', 'jsonrender': 'json-render', 'openui': 'openui'}

# -- OpenUI Lang (https://www.openui.com/docs/openui-lang): `id = Comp(args)` con argumentos posicionales ------------
# El orden de los argumentos es el de las props del esquema; los hijos van detrás de las props, luego `si` y, en los
# componentes con eventos, `events` al final (adaptación nuestra: un objeto {press: {send: "x", with: ["k"]}} con
# las acciones de PACAI, en vez de @ToAssistant/@Set/@OpenUrl). `null` salta un opcional.
FIRMAS_BASE = {
    'Stack': ['direction', 'gap'], 'Card': ['title', 'subtitle', 'meta', 'tags', 'detail', 'image'],
    'Text': ['text', 'style', 'value', 'rojo'], 'Table': ['columns', 'rows'], 'List': ['items'],
    'Button': ['label', 'variant', 'disabled'], 'Form': ['submit_label'],
    'Input': ['name', 'label', 'kind', 'placeholder', 'required', 'min', 'max', 'step'],
    'Select': ['name', 'label', 'options', 'multiple', 'required'], 'Chart': ['kind', 'labels', 'series'],
    'Image': ['src', 'alt'], 'Code': ['code', 'language'],
    'Calc': ['name', 'preset', 'compra', 'venta', 'cantidad'], 'Data': ['name', 'query'],
}


def firmas():
    """Componente → (props en orden, admite hijos, eventos)."""
    from alfalfa.pacai.ui import CON_HIJOS, EVENTOS
    salida = {c: (props, c in CON_HIJOS or c == 'Data', EVENTOS.get(c, [])) for c, props in FIRMAS_BASE.items()}
    for nombre, spec in DECLARACION['components'].items():
        salida[nombre] = (list(spec['props'].get('properties', {})), bool(spec.get('children')), spec.get('events', []))
    return salida


FIRMAS = firmas()


def _firma_texto(comp):
    props, hijos, eventos = FIRMAS[comp]
    orden = props + (['children'] if hijos else []) + ['si'] + (['events'] if eventos else [])
    return comp + '(' + ', '.join(orden) + ')'


FORMATO_OPENUI = '''Pantallas en el chat: escribe cada pantalla en un bloque ```openui view=<view> en OpenUI Lang, una sentencia por línea:
root = Stack("vertical", "medium", [titulo, enviar])
titulo = Text("3 tickets", "title")
enviar = Button("Crear", "primary", null, null, {press: {send: "crear_ticket"}})
- id = Componente(argumentos): los argumentos son POSICIONALES, en el orden de la firma de abajo; null salta un
  opcional y los opcionales del final se omiten. La raíz se llama root y puedes nombrar nodos que defines más abajo.
- Valores: "texto", números, true/false, listas [..], objetos {clave: valor} (query va así), $clave lee el estado
  ($m.pct una salida de Calc o Data). children es una lista de ids. si es {"$": "vista", "es": "detalle"}.
- events (sólo donde la firma lo pone) es {press: {send: "nombre", with: ["clave"]}}; change sólo admite set.
- Cambio de una pantalla que ya existe: reescribe enteras las sentencias de los nodos que cambian y las de los nuevos;
  para quitar un nodo, sácalo de los children de su padre.
- Input y Select guardan solos lo que escribe el usuario en la clave de su name.
Firmas:
'''  # noqa: E501


def _piezas_openui():
    return '''Piezas: name crea estado y $name lo lee; Data publica slots tipados que sólo se leen en su subárbol.
m = Calc("m", "margen", $compra, $venta, $cantidad) publica m.pct (fracción), m.u y m.total (dinero) y m.perdida.
Text("Margen:", null, $m.pct) escribe la etiqueta y el slot con su formato; Text("Total:", null, $m.total, $m.perdida) lo pinta en rojo; nulo es «—».
Alterna con el argumento si = {"$": "vista", "es": "valor"}: valor repite una option; Select sin required empieza en la primera.
Valida con required, kind, min, max y step (fecha: min $q.hoy); Form bloquea su submit hasta que todo vale.
q = Data("q", {tipo: "formPedido"}, [hijos]) espera sus argumentos y gana la última respuesta: formPedido da hoy/lista; comparar da frase/series.'''  # noqa: E501


class _LectorOpenUI:
    """Expresiones de OpenUI Lang. Los componentes en línea reciben un id `_n1`, `_n2`…"""
    _NUM = re.compile(r'-?\d+(?:\.\d+)?')
    _ID = re.compile(r'[A-Za-z_][A-Za-z0-9_]*')
    _CLAVE = re.compile(r'[A-Za-z_$][A-Za-z0-9_]*')
    _ENLACE = re.compile(r'\$([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?)')

    def __init__(self, mapa):
        self.mapa, self.n = mapa, 0

    def sentencia(self, texto):
        m = re.match(r'\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*', texto)      # OpenUI admite camelCase
        if not m:
            raise ValueError('Una sentencia es id = Componente(...): ' + texto[:40])
        self.t, self.i = texto, m.end()
        valor = self.valor()
        self._esp()
        if self.i != len(self.t):
            raise ValueError('Sobra texto: ' + self.t[self.i:self.i + 30])
        if not (isinstance(valor, tuple) and valor[0] == 'comp'):
            raise ValueError(m.group(1) + ' no es un componente.')
        self.mapa[m.group(1)] = self._nodo(m.group(1), valor[1], valor[2])

    def _esp(self):
        while self.i < len(self.t) and self.t[self.i].isspace():
            self.i += 1

    def _ve(self, c):
        self._esp()
        if not self.t.startswith(c, self.i):
            raise ValueError(f'Esperaba {c} en «{self.t[self.i:self.i + 30]}»')
        self.i += len(c)

    def _lista(self, cierre):
        salida = []
        self._esp()
        if self.t.startswith(cierre, self.i):
            self.i += 1
            return salida
        while True:
            salida.append(self.valor())
            self._esp()
            if self.t.startswith(',', self.i):
                self.i += 1
                self._esp()
                if self.t.startswith(cierre, self.i):
                    self.i += 1
                    return salida
                continue
            self._ve(cierre)
            return salida

    def valor(self):
        self._esp()
        c = self.t[self.i:self.i + 1]
        if c in ('"', "'"):
            fin = self.i + 1
            while fin < len(self.t) and self.t[fin] != c:
                fin += 2 if self.t[fin] == '\\' else 1
            crudo = self.t[self.i:fin + 1]
            self.i = fin + 1
            return json.loads('"' + crudo[1:-1].replace('\\\'', "'").replace('"', '\\"') + '"') if c == "'" \
                else json.loads(crudo)
        if c == '[':
            self.i += 1
            return ['lista', self._lista(']')]
        if c == '{':
            self.i += 1
            obj = {}
            self._esp()
            if self.t.startswith('}', self.i):
                self.i += 1
                return obj
            while True:
                self._esp()
                if self.t[self.i] in '"\'':
                    clave = self.valor()
                else:
                    m = self._CLAVE.match(self.t, self.i)
                    if not m:
                        raise ValueError('Clave de objeto inválida.')
                    clave, self.i = m.group(0), m.end()
                self._ve(':')
                obj[clave] = self._dato(self.valor())
                self._esp()
                if self.t.startswith(',', self.i):
                    self.i += 1
                    self._esp()
                    if self.t.startswith('}', self.i):
                        self.i += 1
                        return obj
                    continue
                self._ve('}')
                return obj
        if c == '$':
            m = self._ENLACE.match(self.t, self.i)
            self.i = m.end()
            return {'$': m.group(1)}
        m = self._NUM.match(self.t, self.i)
        if m:
            self.i = m.end()
            return json.loads(m.group(0))
        m = self._ID.match(self.t, self.i)
        if not m:
            raise ValueError('No entiendo «' + self.t[self.i:self.i + 30] + '»')
        self.i = m.end()
        palabra = m.group(0)
        if palabra in ('true', 'false', 'null'):
            return {'true': True, 'false': False, 'null': None}[palabra]
        self._esp()
        if palabra[0].isupper() and self.t.startswith('(', self.i):
            self.i += 1
            return ('comp', palabra, self._lista(')'))
        return ('ref', palabra)

    def _dato(self, v):
        """Un valor de prop: sin referencias a nodos."""
        if isinstance(v, tuple):
            raise ValueError('Un nodo sólo va en children: ' + str(v[1]))
        if isinstance(v, list) and v and v[0] == 'lista':
            return [self._dato(x) for x in v[1]]
        return v

    def _nodo(self, ident, comp, args):
        if comp not in FIRMAS:
            raise ValueError(f'{comp} no está en el catálogo.')
        props_orden, hijos_ok, eventos = FIRMAS[comp]
        orden = props_orden + (['children'] if hijos_ok else []) + ['si'] + (['events'] if eventos else [])
        if len(args) > len(orden):
            raise ValueError(f'{comp} recibe {len(args)} argumentos y su firma tiene {len(orden)}.')
        props, hijos, evs = {}, None, None
        for nombre, v in zip(orden, args):
            if v is None:
                continue
            if nombre == 'children':
                if not (isinstance(v, list) and v and v[0] == 'lista'):
                    raise ValueError(f'children de {ident} no es una lista.')
                hijos = []
                for h in v[1]:
                    if isinstance(h, tuple) and h[0] == 'ref':
                        hijos.append(h[1])
                    elif isinstance(h, tuple) and h[0] == 'comp':
                        self.n += 1
                        nuevo = f'_n{self.n}'
                        self.mapa[nuevo] = self._nodo(nuevo, h[1], h[2])
                        hijos.append(nuevo)
                    else:
                        raise ValueError(f'children de {ident} lleva algo que no es un nodo.')
            elif nombre == 'events':
                evs = self._dato(v)
            else:
                props[nombre] = self._dato(v)
        return _nodo(ident, comp, props, hijos, evs)


def mapa_de_openui(lineas, base=None):
    """Los nodos de un bloque OpenUI Lang (sobre `base` si es un cambio). Une líneas hasta cerrar paréntesis."""
    mapa = dict(base or {})
    lector = _LectorOpenUI(mapa)
    actual, nivel = '', 0
    for linea in lineas:
        if linea.startswith('//'):
            continue
        actual += (' ' if actual else '') + linea
        nivel = sum(actual.count(a) for a in '([{') - sum(actual.count(c) for c in ')]}')
        if nivel <= 0:
            lector.sentencia(actual)
            actual = ''
    if actual:
        raise ValueError('Sentencia sin cerrar: ' + actual[:40])
    return mapa


def _openui_valor(v):
    if isinstance(v, dict):
        if set(v) == {'$'}:
            return '$' + v['$']
        return '{' + ', '.join((k if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', k) else json.dumps(k)) + ': '
                               + _openui_valor(x) for k, x in v.items()) + '}'
    if isinstance(v, list):
        return '[' + ', '.join(_openui_valor(x) for x in v) + ']'
    return json.dumps(v, ensure_ascii=False)


def a_openui(nodos):
    lineas = []
    for n in nodos:
        props_orden, hijos_ok, eventos = FIRMAS[n['component']]
        orden = props_orden + (['children'] if hijos_ok else []) + ['si'] + (['events'] if eventos else [])
        valores = {**(n.get('props') or {}), 'children': n.get('children'), 'events': n.get('events')}
        args = [valores.get(k) for k in orden]
        while args and args[-1] is None:
            args.pop()
        texto = ', '.join('null' if a is None else ('[' + ', '.join(a) + ']') if k == 'children' else _openui_valor(a)
                          for k, a in zip(orden, args))
        lineas.append(f'{n["id"]} = {n["component"]}({texto})')
    return '\n'.join(lineas)


def _piezas_en(enlace: str) -> str:
    """Las líneas de piezas con la forma de leer el estado de otro formato."""
    return re.sub(r'\{"\$":"([a-z_.]+)"\}',
                  lambda m: '{"' + enlace + '":"/' + m.group(1).replace('.', '/') + '"}', PIEZAS_JSON)


# -- prompt con un ejemplo por componente ----------------------------------------------------------------------
# Los ejemplos se escriben una vez como nodos `pacai.ui/1` (los de CuyMercado salen del campo `example` de la
# declaración) y se traducen a la sintaxis de cada formato. Cubren los fallos vistos: enlaces en vez de literales
# (Filters, Stat), `options` como objetos, los `kind` de Input (multiline, no textarea) y el alcance de los slots de
# Data y Calc (quien lee `q.hoy` va dentro del Data; `m.pct` lo publica un Calc llamado m).
EJEMPLOS_BASE = {
    'Stack': [{'id': 'fila', 'component': 'Stack', 'props': {'direction': 'horizontal', 'gap': 'small'},
               'children': ['a', 'b']}],
    'Card': [{'id': 'tarjeta', 'component': 'Card', 'props': {'title': 'Palta', 'meta': 'hoy · fruta',
                                                              'tags': ['fruta'], 'detail': 'Sube un 3 %.'},
              'events': {'press': {'send': 'abrir_producto'}}}],
    'Text': [{'id': 'nota', 'component': 'Text', 'props': {'text': 'Precios por kilo', 'style': 'caption'}}],
    'Table': [{'id': 'tabla', 'component': 'Table', 'props': {
        'columns': [{'key': 'producto', 'label': 'Producto'}], 'rows': [{'producto': 'Palta'}]}}],
    'List': [{'id': 'lista', 'component': 'List', 'props': {'items': [{'id': 'a1', 'title': 'Palta',
                                                                      'subtitle': 'fruta'}]}}],
    'Button': [{'id': 'enviar', 'component': 'Button', 'props': {'label': 'Enviar', 'variant': 'primary'},
                'events': {'press': {'send': 'enviar', 'with': ['correo']}}}],
    'Form': [{'id': 'contacto', 'component': 'Form', 'props': {'submit_label': 'Mandar'}, 'children': ['correo'],
              'events': {'submit': {'send': 'contacto', 'with': ['correo']}}},
             {'id': 'correo', 'component': 'Input', 'props': {'name': 'correo', 'label': 'Correo', 'kind': 'email',
                                                               'required': True}}],
    'Input': [{'id': 'cantidad', 'component': 'Input', 'props': {'name': 'cantidad', 'label': 'Cantidad',
                                                                  'kind': 'number', 'required': True, 'min': 1,
                                                                  'max': 500, 'step': 1}},
              {'id': 'mensaje', 'component': 'Input', 'props': {'name': 'mensaje', 'label': 'Mensaje',
                                                                 'kind': 'multiline'}}],
    'Select': [{'id': 'ver', 'component': 'Select', 'props': {'name': 'vista', 'label': 'Ver', 'options': [
        {'value': 'resumen', 'label': 'Resumen'}, {'value': 'detalle', 'label': 'Detalle'}]}},
               {'id': 'detalle', 'component': 'Stack', 'props': {'si': {'$': 'vista', 'es': 'detalle'}},
                'children': ['tabla']}],
    'Chart': [{'id': 'grafico', 'component': 'Chart', 'props': {'kind': 'line', 'labels': ['lun', 'mar'],
                                                                'series': [{'name': 'Palta', 'values': [6.2, 6.4]}]}}],
    'Image': [{'id': 'foto', 'component': 'Image', 'props': {'src': 'https://ejemplo.pe/palta.png', 'alt': 'Palta'}}],
    'Code': [{'id': 'codigo', 'component': 'Code', 'props': {'code': 'print(1)', 'language': 'python'}}],
    'Calc': [{'id': 'm', 'component': 'Calc', 'props': {'name': 'm', 'preset': 'margen', 'compra': {'$': 'compra'},
                                                         'venta': {'$': 'venta'}, 'cantidad': {'$': 'cantidad'}}},
             {'id': 'margen', 'component': 'Text', 'props': {'text': 'Margen:', 'value': {'$': 'm.pct'},
                                                              'rojo': {'$': 'm.perdida'}}}],
    'Data': [{'id': 'datos', 'component': 'Data', 'props': {'name': 'q', 'query': {'tipo': 'formPedido'}},
              'children': ['producto', 'fecha']},
             {'id': 'producto', 'component': 'Select', 'props': {'name': 'producto', 'label': 'Producto',
                                                                  'required': True, 'options': {'$': 'q.lista'}}},
             {'id': 'fecha', 'component': 'Input', 'props': {'name': 'fecha', 'label': 'Fecha', 'kind': 'date',
                                                              'min': {'$': 'q.hoy'}}}],
}
EJEMPLOS = {**EJEMPLOS_BASE, **{n: s['example'] for n, s in DECLARACION['components'].items() if s.get('example')}}
CON_EJEMPLOS = {'valor': False}


def _a2ui_componente(n):
    c = {'id': n['id'], 'component': n['component'],
         **{k: _a_enlace(v, '@path') for k, v in (n.get('props') or {}).items()}}
    for k in ('children', 'events'):
        if k in n:
            c[k] = n[k]
    return c


def ejemplo_en(formato, nodos):
    if formato == 'lang':
        return ui_lang.a_lang(nodos, CAT)
    if formato == 'openui':
        return a_openui(nodos)
    if formato == 'a2ui':
        return '\n'.join(_compacto(_a2ui_componente(n)) for n in nodos)
    if formato == 'jsonrender':
        return '\n'.join(json.dumps(n['id']) + ':' + _compacto(
            {'type': n['component'], 'props': _a_enlace(n.get('props') or {}, '$state'),
             'children': n.get('children', []), **({'events': n['events']} if 'events' in n else {})})
            for n in nodos)
    return '\n'.join(_compacto(n) for n in nodos)


def bloque_de_ejemplos(formato):
    cabeza = {'a2ui': 'Ejemplos, un componente A2UI por línea (van dentro de components):',
              'jsonrender': 'Ejemplos, un elemento por línea (van dentro de elements):',
              'json': 'Ejemplos, un nodo por línea:', 'lang': 'Ejemplos, una línea por nodo:',
              'openui': 'Ejemplos, una sentencia por línea:'}[formato]
    return '\n' + cabeza + '\n' + '\n'.join(ejemplo_en(formato, nodos) for nodos in EJEMPLOS.values()) + '\n'


def sistema(formato: str) -> str:
    texto = _sistema(formato)
    if CON_EJEMPLOS['valor']:
        texto = texto[:-len(CIERRE)] + bloque_de_ejemplos(formato) + CIERRE
    return texto


def _sistema(formato: str) -> str:
    if formato == 'lang':
        return CAT.manual() + CIERRE
    if formato == 'openui':
        descripciones = {n: s.get('description', '').replace('{"$":"periodo"}', '$periodo')
                         .replace('{"$":"familia"}', '$familia') for n, s in DECLARACION['components'].items()}
        return (FORMATO_OPENUI + '\n'.join('- ' + _firma_texto(c) + (' · ' + descripciones[c] if descripciones.get(c)
                                                                     else '') for c in FIRMAS)
                + '\n' + _piezas_openui() + '\n' + CIERRE)
    if formato in ('a2ui', 'jsonrender'):
        componentes = 'Componentes:' + CAT_JSON.manual().split('Componentes:', 1)[1]
        cabeza, enlace = (FORMATO_A2UI, '@path') if formato == 'a2ui' else (FORMATO_JR, '$state')
        return cabeza + '\n\n' + componentes + '\n' + _piezas_en(enlace) + '\n' + CIERRE
    return CAT_JSON.manual() + '\n' + PIEZAS_JSON + '\n' + CIERRE


def _compacto(o) -> str:
    return json.dumps(o, ensure_ascii=False, separators=(',', ':'))


def _a_enlace(valor, clave):
    """Un nodo de PACAI → el enlace de otro formato (`{"$":"m.pct"}` → `{"@path":"/m/pct"}`), en profundidad."""
    if isinstance(valor, dict):
        if set(valor) == {'$'}:
            return {clave: '/' + valor['$'].replace('.', '/')}
        return {k: _a_enlace(v, clave) for k, v in valor.items()}
    if isinstance(valor, list):
        return [_a_enlace(v, clave) for v in valor]
    return valor


def _de_enlace(valor, clave):
    if isinstance(valor, dict):
        if set(valor) == {clave} and isinstance(valor[clave], str):
            return {'$': valor[clave].strip('/').replace('/', '.')}
        return {k: _de_enlace(v, clave) for k, v in valor.items()}
    if isinstance(valor, list):
        return [_de_enlace(v, clave) for v in valor]
    return valor


def tablero_en(formato: str) -> str:
    nodos = [json.loads(x) for x in MUESTRAS['tablero']['json'].splitlines()]
    if formato == 'openui':
        return a_openui(nodos)
    if formato == 'a2ui':
        componentes = []
        for n in nodos:
            c = {'id': n['id'], 'component': n['component'],
                 **{k: _a_enlace(v, '@path') for k, v in (n.get('props') or {}).items()}}
            if 'children' in n:
                c['children'] = n['children']
            componentes.append(c)
        crear = {'version': '1.0', 'createSurface': {'surfaceId': 'tablero', 'catalogId': 'cuymercado'}}
        return '\n'.join([_compacto(crear),
                          _compacto({'version': '1.0', 'updateComponents': {'surfaceId': 'tablero',
                                                                             'components': componentes}})])
    return _compacto({'root': 'root', 'elements': {n['id']: {
        'type': n['component'], 'props': {k: _a_enlace(v, '$state') for k, v in (n.get('props') or {}).items()},
        'children': n.get('children', [])} for n in nodos}})


def _nodo(ident, componente, props, hijos, eventos):
    nodo = {'id': ident, 'component': componente}
    if props:
        nodo['props'] = props
    if hijos:                                   # un `children: []` de una hoja no es un error del formato
        nodo['children'] = hijos
    if eventos:
        nodo['events'] = eventos
    return nodo


def mapa_de_a2ui(lineas, base=None):
    """Los nodos que deja un bloque A2UI (sobre `base` si es un cambio); ValueError si no es A2UI válido."""
    mapa = dict(base or {})
    for linea in lineas:
        m = json.loads(linea)
        if not isinstance(m, dict) or m.get('version') != '1.0':
            raise ValueError('Cada mensaje A2UI lleva "version":"1.0".')
        cuerpo = m.get('createSurface') or m.get('updateComponents')
        if not isinstance(cuerpo, dict):
            raise ValueError('Mensaje A2UI desconocido: ' + ', '.join(k for k in m if k != 'version'))
        for c in cuerpo.get('components') or []:
            if not isinstance(c, dict) or not isinstance(c.get('id'), str) or not isinstance(c.get('component'), str):
                raise ValueError('Un componente A2UI sin id o component.')
            props = {k: _de_enlace(v, '@path') for k, v in c.items()
                     if k not in ('id', 'component', 'children', 'events')}
            mapa[c['id']] = _nodo(c['id'], c['component'], props, c.get('children'), c.get('events'))
    return mapa


def _puntero(ruta):
    return [p.replace('~1', '/').replace('~0', '~') for p in ruta.split('/')[1:]]


def aplicar_parche(spec, ops):
    """JSON Patch (add, replace, remove) sobre un spec de json-render."""
    for op in ops:
        partes = _puntero(op['path'])
        padre = spec
        for p in partes[:-1]:
            padre = padre[int(p)] if isinstance(padre, list) else padre[p]
        ultimo = partes[-1]
        if op['op'] == 'remove':
            del padre[int(ultimo) if isinstance(padre, list) else ultimo]
        elif op['op'] in ('add', 'replace'):
            if isinstance(padre, list):
                if ultimo == '-':
                    padre.append(op['value'])
                elif op['op'] == 'add':
                    padre.insert(int(ultimo), op['value'])
                else:
                    padre[int(ultimo)] = op['value']
            else:
                if op['op'] == 'replace' and ultimo not in padre:
                    raise ValueError('replace de algo que no existe: ' + op['path'])
                padre[ultimo] = op['value']
        else:
            raise ValueError('Operación no admitida: ' + str(op.get('op')))
    return spec


def mapa_de_jsonrender(lineas, base_spec=None):
    texto = ''.join(lineas).strip()
    if base_spec is not None and texto.startswith('[{'):          # el documento JSON Patch estándar, un array
        spec = aplicar_parche(json.loads(json.dumps(base_spec)), json.loads(texto))
    elif base_spec is not None and lineas and lineas[0].lstrip().startswith('{"op"'):
        spec = aplicar_parche(json.loads(json.dumps(base_spec)), [json.loads(x) for x in lineas])
    else:
        spec = json.loads(''.join(lineas))
    if not isinstance(spec, dict) or spec.get('root') != 'root' or not isinstance(spec.get('elements'), dict):
        raise ValueError('El spec necesita "root":"root" y elements.')
    mapa = {}
    for ident, el in spec['elements'].items():
        if not isinstance(el, dict) or not isinstance(el.get('type'), str):
            raise ValueError(f'El elemento {ident} no tiene type.')
        mapa[ident] = _nodo(ident, el['type'], _de_enlace(el.get('props') or {}, '$state'), el.get('children'),
                            el.get('events'))
    return mapa


def validar_mapa(mapa, a2ui=False):
    """Los errores de una pantalla ya convertida a nodos de PACAI, con las mismas reglas que los otros brazos."""
    if a2ui:                                    # en A2UI un componente que no cuelga de nadie no se dibuja
        vivos, pila = set(), ['root']
        while pila:
            i = pila.pop()
            if i in mapa and i not in vivos:
                vivos.add(i)
                pila += mapa[i].get('children', [])
        mapa = {i: n for i, n in mapa.items() if i in vivos}
    errores = [f'{i}: ' + '; '.join(e[:2]) for i, n in mapa.items() if (e := CAT.errores_del_nodo(n))]
    errores += [f'{i}: {m}' for i, m in piezas.errores_del_arbol(mapa, CAT.consultas)]
    if 'root' not in mapa:
        errores.append('Falta root.')
    for i, nodo in mapa.items():
        errores += [f'{h} no existe (hijo de {i}).' for h in nodo.get('children', []) if h not in mapa]
    return mapa, errores


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
    if formato in ('a2ui', 'jsonrender', 'openui'):
        return ('Pantalla actual (view=tablero), lo que mandaste:\n' + tablero_en(formato)
                + '\n\nCambio que pide el usuario: ' + texto)
    if formato == 'lang':
        return ('Pantalla actual (view=tablero), con su revisión:\n' + pantalla_base('lang').resumen_lang('tablero')
                + '\n\nCambio que pide el usuario: ' + texto)
    return ('Pantalla actual (view=tablero), los nodos que mandaste:\n' + MUESTRAS['tablero']['json']
            + '\n\nCambio que pide el usuario: ' + texto)


# -- lectura de la respuesta en streaming ---------------------------------------------------------------------
_VALLA = re.compile(r'^\s*```(pacai-ui(?:-lang)?|a2ui|json-render|openui)((?:\s+[a-z_]+=\S+)*)\s*$')


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
    valla_ok = lector.attrs['valla'] == VALLAS[formato]
    if formato in ('a2ui', 'jsonrender', 'openui'):
        try:
            if formato == 'openui':
                base = mapa_de_openui(tablero_en('openui').splitlines()) if tipo == 'cambio' else None
                mapa = mapa_de_openui(lector.lineas, base)
            elif formato == 'a2ui':
                base = mapa_de_a2ui(tablero_en('a2ui').splitlines()) if tipo == 'cambio' else None
                mapa = mapa_de_a2ui(lector.lineas, base)
            else:
                base = json.loads(tablero_en('jsonrender')) if tipo == 'cambio' else None
                mapa = mapa_de_jsonrender(lector.lineas, base)
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            return False, False, ['No se puede leer: ' + str(exc)[:200]], None
        mapa, errores = validar_mapa(mapa, formato in ('a2ui', 'openui'))
        if not valla_ok:
            errores = ['Valla equivocada.'] + errores
        return not errores, bool(mapa) and not errores and _seguro(comprobar, mapa), errores, None
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
                 'respuesta': lector.texto[:4000], 'ejemplos': CON_EJEMPLOS['valor']}
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
                         'total_reintento': total2, 'respuesta_reintento': lector2.texto[:4000]}
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
    args.add_argument('--formatos', default='json,lang', help='json, lang, a2ui y jsonrender, separados por comas')
    args.add_argument('--ejemplos', action='store_true', help='un ejemplo por componente en el prompt')
    args.add_argument('--esfuerzo', choices=('low', 'medium', 'high'), help='esfuerzo que se pide al servicio')
    opciones = args.parse_args()
    ESFUERZO['valor'] = opciones.esfuerzo
    CON_EJEMPLOS['valor'] = opciones.ejemplos
    from alfalfa.proveedores.alfalfa import Alfalfa

    proveedor = Alfalfa()
    if not proveedor.configurado():
        sys.exit('El proveedor Alfalfa (suscripción CUY) no está activado en este equipo.')
    tareas = [t for t in TAREAS if not opciones.solo or t[0] in opciones.solo.split(',')]
    trabajos = [(m, f, t, r) for r in range(opciones.repeticiones) for t in tareas for m in MODELOS
                for f in opciones.formatos.split(',')]
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
