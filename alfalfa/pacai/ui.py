"""Perfil `ui` de PACAI (docs/pacai/perfil-ui.md): el agente dibuja pantallas en el chat.

- `Catalogo`: lo que el cliente declaró en `hello.payload.render.ui`; valida nodos y escribe la parte del prompt que
  le explica al modelo qué puede dibujar.
- `partir`: separa el texto del modelo de sus bloques ```` ```pacai-ui ````.
- `Vistas`: las pantallas de la sesión (revisión, nodos y estado inicial), para mandar `patch` y la foto `replace`.
- `accion_para_el_modelo`: lo que el modelo lee cuando el usuario pulsa un botón o envía un formulario.
"""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

__all__ = ['ESQUEMA_UI', 'ESQUEMA_ACCION', 'Catalogo', 'Vistas', 'partir', 'errores_de', 'accion_para_el_modelo']

ESQUEMA_UI = 'pacai.ui/1'
ESQUEMA_ACCION = 'pacai.ui.action/1'
VALLA = 'pacai-ui'
MAX_NODOS, MAX_PROFUNDIDAD = 300, 12
BASE = ('Stack', 'Card', 'Text', 'Table', 'List', 'Button', 'Form', 'Input', 'Select', 'Chart', 'Image', 'Code')
CON_HIJOS = {'Stack', 'Card', 'Form'}
EVENTOS = {'Card': ['press'], 'Table': ['row_press'], 'List': ['item_press'], 'Button': ['press'],
           'Form': ['submit'], 'Input': ['change'], 'Select': ['change']}
# Para el modelo: una línea por componente base (las propiedades exactas están en el esquema).
RESUMEN = {
    'Stack': 'direction (vertical|horizontal|grid: tarjetas en columnas), gap (none|small|medium|large)',
    'Card': 'title, subtitle, image (https:), meta (línea pequeña: fecha · tema), tags [texto], '
            'detail (texto largo que el usuario despliega con «Ver más»)',
    'Text': 'text (texto plano), style (body|title|subtitle|caption|muted)',
    'Table': 'columns [{key, label}], rows [objetos]',
    'List': 'items [{id, title, subtitle}]',
    'Button': 'label, variant (primary|secondary|danger), disabled',
    'Form': 'submit_label',
    'Input': 'name, label, kind (text|number|multiline|email|date), placeholder, required',
    'Select': 'name, label, options [{value, label}], multiple, required (empieza vacío y el Form no se envía sin '
              'elegir)',
    'Chart': 'kind (bar|line|pie), labels [texto], series [{name, values [números]}]',
    'Image': 'src (https: o data:image/…), alt',
    'Code': 'code, language',
}
# Un ejemplo por componente base para el prompt (los propios traen el suyo en `example`). Son la forma exacta que se
# copia: enlaces {"$": …} en vez de literales, `options` como objetos, los `kind` de Input y sus eventos. Con ellos,
# las pantallas válidas a la primera de GPT-6 Luna y Claude Haiku 5.5 subieron del 86–89 % al 97 % (624 peticiones).
EJEMPLOS_BASE = {
    'Stack': [{'id': 'fila', 'component': 'Stack', 'props': {'direction': 'horizontal', 'gap': 'small'},
               'children': ['guardar', 'cancelar']}],
    'Card': [{'id': 'palta', 'component': 'Card', 'props': {'title': 'Palta', 'meta': 'hoy · fruta', 'tags': ['fruta'],
                                                            'detail': 'Sube un 3 % esta semana.'},
              'events': {'press': {'send': 'abrir_producto'}}}],
    'Text': [{'id': 'nota', 'component': 'Text', 'props': {'text': 'Precios por kilo', 'style': 'caption'}},
             {'id': 'eleccion', 'component': 'Text', 'props': {'text': {'$': 'moneda'}}}],
    'Table': [{'id': 'tickets', 'component': 'Table', 'props': {
        'columns': [{'key': 'id', 'label': 'Id'}, {'key': 'titulo', 'label': 'Título'}],
        'rows': [{'id': 'T-1', 'titulo': 'Login falla'}]}, 'events': {'row_press': {'send': 'abrir_ticket'}}}],
    'List': [{'id': 'tareas', 'component': 'List', 'props': {'items': [{'id': 't1', 'title': 'Comprar palta',
                                                                       'subtitle': 'hoy'}]},
              'events': {'item_press': {'send': 'ver_tarea'}}}],
    'Button': [{'id': 'guardar', 'component': 'Button', 'props': {'label': 'Guardar', 'variant': 'primary'},
                'events': {'press': {'send': 'guardar', 'with': ['nombre']}}},
               {'id': 'abrir_nota', 'component': 'Button', 'props': {'label': 'Abrir fuente'},
                'events': {'press': {'open_url': 'https://ejemplo.pe/nota'}}}],
    'Form': [{'id': 'contacto', 'component': 'Form', 'props': {'submit_label': 'Mandar'},
              'children': ['correo', 'mensaje'], 'events': {'submit': {'send': 'contacto',
                                                                       'with': ['correo', 'mensaje']}}}],
    'Input': [{'id': 'correo', 'component': 'Input', 'props': {'name': 'correo', 'label': 'Correo', 'kind': 'email',
                                                                'required': True}},
              {'id': 'mensaje', 'component': 'Input', 'props': {'name': 'mensaje', 'label': 'Mensaje',
                                                                 'kind': 'multiline'}},
              {'id': 'cantidad', 'component': 'Input', 'props': {'name': 'cantidad', 'label': 'Cantidad',
                                                                  'kind': 'number', 'placeholder': 'p. ej. 20'}},
              {'id': 'fecha', 'component': 'Input', 'props': {'name': 'fecha', 'label': 'Fecha', 'kind': 'date'}}],
    'Select': [{'id': 'moneda', 'component': 'Select', 'props': {'name': 'moneda', 'label': 'Moneda', 'required': True,
                                                               'options': [
        {'value': 'pen', 'label': 'Soles'}, {'value': 'usd', 'label': 'Dólares'}]}}],
    'Chart': [{'id': 'ventas', 'component': 'Chart', 'props': {'kind': 'line', 'labels': ['ene', 'feb'],
                                                               'series': [{'name': 'Palta', 'values': [6.2, 6.4]}]}}],
    'Image': [{'id': 'foto', 'component': 'Image', 'props': {'src': 'https://ejemplo.pe/palta.png', 'alt': 'Palta'}}],
    'Code': [{'id': 'codigo', 'component': 'Code', 'props': {'code': 'print("hola")', 'language': 'python'}}],
}
EJEMPLO_REJILLA = {'id': 'tarjetas', 'component': 'Stack', 'props': {'direction': 'grid', 'gap': 'small'},
                   'children': ['palta']}
CLAVES_DE_NODO = ('id', 'component', 'props', 'children', 'events')
_NOMBRE = re.compile(r'^[a-z][a-z0-9_]*$')
_ID_ROOT = re.compile(r'"id"\s*:\s*"root"')     # una línea que quería ser root, aunque no se pueda leer
# La valla de un bloque: ``` como siempre y, como también hacen los modelos, ~~~ (la otra valla de CommonMark) o
# ::: (la de las directivas de markdown). Cada bloque se cierra con su misma marca.
MARCAS = ('```', '~~~', ':::')
_MARCA = r'(?P<marca>```|~~~|:::)'
_APERTURA = re.compile(r'^\s*' + _MARCA + VALLA + r'(?P<lang>-lang)?(?P<attrs>(?:\s+[a-z_]+=\S+)*)\s*$')
# La misma valla al final de una línea con texto delante («Aquí está:```pacai-ui view=x»): algunos modelos escriben
# una frase o su razonamiento y abren el bloque sin saltar de línea. Sólo cuenta si tras la valla no queda más que
# sus atributos, si el texto de delante no lleva comillas invertidas (una valla citada en `código`) y si no estamos
# dentro de otro bloque de código.
_APERTURA_A_MEDIA_LINEA = re.compile(_MARCA + VALLA + r'(?P<lang>-lang)?(?P<attrs>(?:\s+[a-z_]+=\S+)*)\s*$')


def tiene_valla(texto: str) -> bool:
    """Si el texto abre algún bloque del perfil `ui`, con cualquiera de sus marcas."""
    return any(marca + VALLA in texto for marca in MARCAS)


def _reapertura(linea: str, lang: bool) -> tuple[dict, str] | None:
    """Los atributos y la marca de una valla del perfil que aparece dentro de un bloque todavía abierto, al principio
    de la línea o detrás de lo que sea, o None."""
    abre = _APERTURA.match(linea) or _APERTURA_A_MEDIA_LINEA.search(linea)
    if abre is None or (abre.group('lang') and not lang):
        return None
    attrs: dict[str, Any] = dict(a.split('=', 1) for a in abre.group('attrs').split())
    if abre.group('lang'):
        attrs['lang'] = True
    return attrs, abre.group('marca')


def _cierra(linea: str, marca: str) -> bool:
    """La línea que cierra un bloque abierto con `marca`: ``` sola en su línea, como siempre; ~~~ o ::: al principio
    de la línea, aunque el modelo escriba algo detrás (se descarta)."""
    texto = linea.strip()
    return texto == marca if marca == '```' else texto.startswith(marca)
_OTRA_VALLA = re.compile(r'^\s*```')


def ascii_de(texto: str, minusculas: bool = False) -> str:
    """Un identificador en el ASCII que pide el formato: `añadir_tarea` → `anadir_tarea`, `descripción` →
    `descripcion`. Lo que no es letra, cifra, `_` o `-` pasa a `_`. Un modelo que piensa en español escribe ids y
    nombres con tildes y eñes, y un botón no debería perderse por eso."""
    plano = ''.join(c for c in unicodedata.normalize('NFKD', texto) if not unicodedata.combining(c))
    plano = re.sub(r'[^A-Za-z0-9_-]', '_', plano)
    return plano.lower() if minusculas else plano


def _normalizar_nodo(nodo: Any) -> Any:
    """El nodo con sus identificadores en ASCII: id, hijos, `name` de los campos, enlaces `{"$": clave}` y, en sus
    acciones, el nombre de `send`, las claves de `with` y las de `set`. Los textos que se ven no se tocan."""
    if not isinstance(nodo, dict):
        return nodo
    nodo = dict(nodo)
    if isinstance(nodo.get('id'), str):
        nodo['id'] = ascii_de(nodo['id'])
    if isinstance(nodo.get('children'), list):
        nodo['children'] = [ascii_de(h) if isinstance(h, str) else h for h in nodo['children']]
    if isinstance(nodo.get('props'), dict):
        props = {}
        for clave, valor in nodo['props'].items():
            if clave == 'name' and isinstance(valor, str):
                valor = ascii_de(valor)
            elif isinstance(valor, dict) and set(valor) == {'$'} and isinstance(valor['$'], str):
                valor = {'$': ascii_de(valor['$'])}
            props[clave] = valor
        nodo['props'] = props
    if isinstance(nodo.get('events'), dict):
        nodo['events'] = {evento: ([_normalizar_accion(a) for a in spec] if isinstance(spec, list)
                                   else _normalizar_accion(spec)) for evento, spec in nodo['events'].items()}
        _sin_set_de_su_campo(nodo)
    return nodo


def _sin_set_de_su_campo(nodo: dict) -> None:
    """Un `Input` o `Select` ya guarda lo que escribe el usuario en la clave de su `name`. Un `set` de esa misma
    clave en su `change` sobra y la machaca (los modelos escriben `{"set": {"titulo": "$value"}}`): se quita, y el
    `change` entero si se queda vacío."""
    nombre = (nodo.get('props') or {}).get('name')
    cambio = nodo['events'].get('change')
    if nodo.get('component') not in ('Input', 'Select') or not isinstance(nombre, str) or cambio is None:
        return
    acciones = []
    for accion in cambio if isinstance(cambio, list) else [cambio]:
        if isinstance(accion, dict) and isinstance(accion.get('set'), dict) and nombre in accion['set']:
            resto = {k: v for k, v in accion['set'].items() if k != nombre}
            if not resto:
                continue
            accion = dict(accion, set=resto)
        acciones.append(accion)
    if acciones:
        nodo['events']['change'] = acciones if isinstance(cambio, list) else acciones[0]
    else:
        del nodo['events']['change']
        if not nodo['events']:
            del nodo['events']


def _normalizar_accion(accion: Any) -> Any:
    if not isinstance(accion, dict):
        return accion
    accion = dict(accion)
    if isinstance(accion.get('send'), str):
        accion['send'] = ascii_de(accion['send'], minusculas=True)
    if isinstance(accion.get('with'), list):
        accion['with'] = [ascii_de(k) if isinstance(k, str) else k for k in accion['with']]
    if isinstance(accion.get('set'), dict):
        accion['set'] = {ascii_de(k): v for k, v in accion['set'].items()}
    return accion


@lru_cache(maxsize=1)
def esquema() -> dict:
    return json.loads(Path(__file__).with_name('pacai-ui-1.schema.json').read_text(encoding='utf-8'))


@lru_cache(maxsize=8)
def _validador(definicion: str, con_piezas: bool = False):
    import jsonschema

    if con_piezas:
        from .piezas import esquema_piezas
        return jsonschema.Draft202012Validator({**esquema_piezas(), '$ref': '#/$defs/' + definicion})
    return jsonschema.Draft202012Validator({**esquema(), '$ref': '#/$defs/' + definicion})


def errores_de(definicion: str, dato: Any, con_piezas: bool = False) -> list[str]:
    """Errores de `dato` contra `$defs/<definicion>` del esquema (`declaration`, `node`, `update`, `action`).
    `con_piezas`: el esquema con las piezas declarativas del prototipo (`piezas.esquema_piezas`)."""
    return [e.message for e in _validador(definicion, con_piezas).iter_errors(dato)]


def _acciones(spec: Any) -> list[dict]:
    return spec if isinstance(spec, list) else [spec]


# Sólo si el motor ofrece web_search y read_web: un modelo de código no las relaciona con «noticias» o «precios».
BUSCAR_ANTES = '''- Si la respuesta necesita información actual o de fuera (noticias, precios, tiempo, versiones,
  datos que no sabes), búscala primero con web_search y read_web; no digas que no tienes internet. Después
  enséñala en una pantalla y pon la url de cada fuente (en un Text o en un Button con open_url).
- No te rindas a la primera: si read_web falla (429, 403, error), lee otro de los resultados que ya tienes;
  prueba al menos tres fuentes. Los títulos, fragmentos y fechas de los resultados de web_search ya sirven para
  una tarjeta si citas su url. Si algo queda sin verificar, dilo en la tarjeta en vez de dejar al usuario sin nada.
'''

# Prototipo, sólo con ALFALFA_PACAI_UI_MANUAL=1: el manual de pantallas se carga bajo demanda, como una habilidad.
# El prompt de sistema lleva sólo AVISO_MANUAL y el modelo pide el manual entero con la herramienta `ui_manual`.
FLAG_MANUAL = 'ALFALFA_PACAI_UI_MANUAL'
HERRAMIENTA_MANUAL = 'ui_manual'
AVISO_MANUAL = '''
Pantallas en el chat (perfil ui): el cliente dibuja pantallas dentro de la conversación. Cuando la respuesta se
entienda mejor como pantalla (una lista o tabla de cosas, tarjetas, una comparativa, un gráfico, resultados de una
búsqueda, algo que el usuario tenga que elegir, rellenar o confirmar, o algo que te pidan enseñar o dibujar aquí),
primero carga el manual con ui_manual y dibújala como dice; no la escribas en markdown. Para una respuesta corta o
una explicación de código escribe texto normal.
'''
UI_MANUAL_TOOL = {'type': 'function', 'function': {
    'name': HERRAMIENTA_MANUAL,
    'description': 'Load the manual for drawing screens in this chat (the components this client can draw and the '
                   'block format). Call it once before your first screen; the manual stays in the conversation.',
    'parameters': {'type': 'object', 'properties': {}, 'required': [], 'additionalProperties': False}}}


def manual_bajo_demanda(entorno: dict | None = None) -> bool:
    import os

    return (os.environ if entorno is None else entorno).get(FLAG_MANUAL, '').strip().lower() in ('1', 'true', 'si',
                                                                                                  'sí')


@dataclass
class Catalogo:
    base: bool = True
    propios: dict[str, dict] = field(default_factory=dict)
    acciones: frozenset[str] = frozenset({'send', 'set', 'open_url'})
    max_nodos: int = MAX_NODOS
    max_profundidad: int = MAX_PROFUNDIDAD
    # 3.ª y 4.ª lluvia (ui_lang.py): `lang` enseña y lee PACAI UI Lang, según el modelo de la sesión (`con_modelo`) o
    # lo que fuerce ALFALFA_PACAI_UI_LANG (`forzado`: True, False o None); `piezas` (sólo con el flag a 1 y si además el
    # cliente declara `pieces: true`) admite Calc, Data, si, rojo, min/max/step…
    # `consultas`: las que declara la app en `queries` ({tipo: {"args": …, "out": {slot: tipo}}}).
    lang: bool = False
    piezas: bool = False
    consultas: dict[str, dict] = field(default_factory=dict)
    forzado: bool | None = None
    # Prototipo (ALFALFA_PACAI_UI_MANUAL=1): el manual entra con la herramienta ui_manual, no en el prompt.
    bajo_demanda: bool = False

    @classmethod
    def de(cls, declaracion: dict | None, entorno: dict | None = None, modelo: str | None = None) -> Catalogo:
        """El catálogo de una declaración ya validada (`errores_de('declaration', …)`); None vale la de por defecto.
        `entorno`: dónde mirar ALFALFA_PACAI_UI_LANG (por defecto, las variables de entorno). `modelo`: el de la
        sesión, si ya se sabe (si no, `con_modelo` antes de cada inferencia)."""
        from .ui_lang import forzado, para_modelo

        d = declaracion or {}
        limites = d.get('limits') or {}
        lang = para_modelo(modelo, entorno)
        piezas = forzado(entorno) is True and d.get('pieces') is True
        return cls(base=d.get('catalog', 'base') == 'base', propios=dict(d.get('components') or {}),
                   acciones=frozenset(d.get('actions', ('send', 'set', 'open_url'))),
                   max_nodos=limites.get('max_nodes', MAX_NODOS),
                   max_profundidad=limites.get('max_depth', MAX_PROFUNDIDAD), lang=lang, piezas=piezas,
                   consultas=dict(d.get('queries') or {}) if piezas and isinstance(d.get('queries'), dict) else {},
                   forzado=forzado(entorno), bajo_demanda=manual_bajo_demanda(entorno))

    def con_modelo(self, modelo: str | None) -> Catalogo:
        """Ajusta el formato al modelo de esta inferencia (hoy PACAI UI Lang con todos; el JSON sólo con el flag a 0).
        Con el flag forzado no cambia. El modelo puede cambiar a mitad de sesión: lo que lea el motor después sigue
        al último prompt que vio el modelo."""
        from .ui_lang import para_modelo

        if self.forzado is None:
            self.lang = para_modelo(modelo, {})
        return self

    def componentes(self) -> list[str]:
        from .piezas import COMPONENTES

        return (list(BASE) if self.base else []) + (list(COMPONENTES) if self.piezas else []) + list(self.propios)

    def errores_del_nodo(self, nodo: Any) -> list[str]:
        errores = errores_de('node', nodo, self.piezas)
        if errores or not isinstance(nodo, dict):
            return errores
        nombre = nodo['component']
        if nombre not in self.componentes():
            return ['El componente ' + nombre + ' no está en el catálogo del cliente.']
        if nombre in self.propios:
            spec = self.propios[nombre]
            import jsonschema

            props = dict(nodo.get('props', {}))
            si = props.pop('si', None) if self.piezas else None
            errores += [e.message for e in jsonschema.Draft202012Validator(spec['props']).iter_errors(props)]
            if si is not None:
                errores += errores_de('si', si, True)
            if nodo.get('children') and not spec.get('children'):
                errores.append(nombre + ' no admite hijos.')
            fuera = set(nodo.get('events', {})) - set(spec.get('events', []))
            if fuera:
                errores.append(nombre + ' no tiene los eventos ' + ', '.join(sorted(fuera)) + '.')
            if 'change' in nodo.get('events', {}) and any('send' in a for a in _acciones(nodo['events']['change'])):
                errores.append('change sólo admite set.')
        usadas = {k for spec in nodo.get('events', {}).values() for a in _acciones(spec) for k in a}
        usadas.discard('with')
        if usadas - self.acciones:
            errores.append('El cliente no ejecuta las acciones ' + ', '.join(sorted(usadas - self.acciones)) + '.')
        return errores

    def ejemplos(self) -> list[dict]:
        """Los nodos de ejemplo del prompt: los de los componentes base y el `example` de cada componente propio.
        Un ejemplo que no pasa el catálogo se salta: el prompt nunca enseña una forma que el validador rechaza."""
        nodos = [n for c in BASE for n in EJEMPLOS_BASE[c]] if self.base else []
        for spec in self.propios.values():
            ejemplo = spec.get('example') if isinstance(spec.get('example'), list) else []
            if ejemplo and not any(self.errores_del_nodo(n) for n in ejemplo):
                nodos += ejemplo
        return nodos

    def manual(self, web: bool = False, ejemplos: bool = True) -> str:
        """La parte del prompt de sistema que explica al modelo cómo dibujar en este cliente. Con `web` (el motor le
        ofrece web_search y read_web), también que busque lo que no sabe antes de dibujarlo. Con `ejemplos`, un
        ejemplo por componente (sin él, sólo la lista de props: así se mide la diferencia). Con el flag de PACAI UI
        Lang, el formato compacto en vez del JSON, con sus ejemplos en esa sintaxis."""
        if self.lang:
            return self._manual_lang(web, ejemplos)
        lineas = []
        if self.base:
            lineas += ['- ' + c + ': ' + RESUMEN[c] + ('; hijos' if c in CON_HIJOS else '')
                       + ('; eventos ' + ', '.join(EVENTOS[c]) if c in EVENTOS else '') for c in BASE]
        for nombre, spec in self.propios.items():
            props = ', '.join((spec.get('props') or {}).get('properties', {}))
            descripcion = spec['description'] + ' · ' if spec.get('description') else ''
            lineas.append('- ' + nombre + ': ' + descripcion + (props or 'sin propiedades')
                          + ('; hijos' if spec.get('children') else '')
                          + ('; eventos ' + ', '.join(spec['events']) if spec.get('events') else ''))
        acciones = ', '.join(sorted(self.acciones))
        return f'''
Pantallas en el chat (perfil ui): el cliente dibuja pantallas dentro de la conversación. Responde con una
pantalla, no con una lista o una tabla de markdown, cuando la respuesta sea:
- una lista o una tabla de cosas (tareas, opciones, resultados, comparaciones);
- algo que el usuario tenga que elegir, rellenar o confirmar (botones, formularios);
- datos que se entienden mejor en un gráfico;
- algo que te pidan «dibujar», «enseñar», «mostrar» o «ver» aquí: eso es una pantalla, no arte ASCII.
Para enseñar algo en el chat no crees archivos ni escribas una página web: dibújalo aquí. Crea archivos sólo si te
piden cambiar el proyecto. Para una respuesta corta (un número, una frase, una explicación) escribe texto normal,
y puedes acompañar una pantalla con una frase antes o después. Una pantalla va en un bloque cercado, una línea JSON
por nodo:

```{VALLA} view=tickets
{{"id":"root","component":"Stack","children":["titulo","enviar"]}}
{{"id":"titulo","component":"Text","props":{{"text":"3 tickets abiertos","style":"title"}}}}
{{"id":"enviar","component":"Button","props":{{"label":"Crear"}},"events":{{"press":{{"send":"crear_ticket"}}}}}}
```

- Para varias cosas con contenido (noticias, productos, resultados): un Stack direction=grid de Card, cada una con
  title, meta, tags y su detail (el texto completo o un resumen largo), y un Button «Abrir fuente» con open_url.
- Ids, view, nombres de acción y claves: ASCII sin tildes ni eñes (a-z, 0-9, _), p. ej. anadir_tarea. Los textos
  que se ven (title, label, text…) van en español normal.
- view: nombre de la pantalla ([a-z0-9_]). Para cambiarla después, repite view y manda solo los nodos nuevos
  o distintos (un id repetido reemplaza al nodo entero). Opcional: placement=latest la mueve al mensaje actual.
- La raíz se llama root. children son ids de otros nodos del mismo view.
- Líneas especiales: {{"remove":["id"]}} borra nodos, {{"state":{{"clave":valor}}}} da valores iniciales a los
  campos, {{"alt":"resumen"}} describe la pantalla en texto.
- Input y Select guardan solos lo que escribe el usuario en la clave de su name: no les pongas change ni set para
  eso (no existe "$value"). Un formulario sólo necesita submit con {{"send":"…","with":["esa_clave"]}}.
- Cualquier prop puede leer el estado con {{"$":"clave"}}.
- Acciones disponibles: {acciones}. {{"send":"nombre","with":["clave"]}} te devuelve la acción con esos
  valores como un mensaje del usuario; {{"set":{{"clave":valor}}}} cambia el estado sin llamarte;
  {{"open_url":"https://…"}} abre un enlace. Como mucho un send por evento; change solo admite set.
- Texto plano: sin HTML ni markdown dentro de los nodos. No pidas contraseñas ni datos de pago.
- Máximo {self.max_nodos} nodos por pantalla.
''' + (BUSCAR_ANTES if web else '') + '''
Componentes:
''' + '\n'.join(lineas) + '\n' + (self._bloque_de_ejemplos() if ejemplos else '')

    def _bloque_de_ejemplos(self) -> str:
        nodos = self.ejemplos()
        if not nodos:
            return ''
        return ('\nEjemplos, un nodo por línea (copia la forma; los ids y los textos son tuyos):\n'
                + '\n'.join(json.dumps(n, ensure_ascii=False, separators=(',', ':')) for n in nodos) + '\n')

    def _manual_lang(self, web: bool, ejemplos: bool = True) -> str:
        from .ui_lang import a_lang, prompt, prompt_piezas

        piezas = prompt_piezas(self)
        # El ejemplo de Stack, en el formato compacto, enseña tarjetas en rejilla (el JSON lo dice en una línea propia
        # que este prompt no lleva): sin él, los modelos usan Grid para «tarjetas en rejilla».
        nodos = [EJEMPLO_REJILLA if n['id'] == 'fila' else n for n in self.ejemplos()] if ejemplos else []
        bloque = ('Ejemplos, una línea por nodo (copia la forma; los ids y los textos son tuyos):\n'
                  + a_lang(nodos, self) + '\n') if nodos else ''
        return ('''
Pantallas en el chat (perfil ui): el cliente dibuja pantallas dentro de la conversación. Responde con una pantalla
cuando la respuesta sea una lista, una tabla, algo que elegir o rellenar, un gráfico o algo que te pidan enseñar aquí;
para una respuesta corta escribe texto normal. No crees archivos para enseñar algo en el chat.
''' + (BUSCAR_ANTES if web else '') + prompt(self) + '\n' + (piezas + '\n' if piezas else '') + bloque
                + f'Acciones: {", ".join(sorted(self.acciones))}; como mucho un send por evento. Input y Select '
                'guardan solos su valor en name: no les pongas @change (sólo admite set). Texto plano, sin HTML ni '
                f'markdown en los nodos. Máximo {self.max_nodos} nodos.\n')


def partir(texto: str, lang: bool = False) -> list[tuple]:
    """Trozos del texto del modelo: ('text', str) o ('ui', atributos, líneas). Una valla sin cerrar llega al final.
    Con `lang` (el flag de PACAI UI Lang), una valla ```pacai-ui-lang lleva `lang: True` en sus atributos; sin él es
    texto, como siempre.

    La valla de apertura también vale al final de una línea con texto delante (ese texto queda como texto normal),
    salvo dentro de otro bloque de código o citada entre comillas invertidas. Sólo se encuentra el bloque: su
    contenido no cambia."""
    trozos: list[tuple] = []
    texto_actual: list[str] = []
    ui: tuple[dict, list[str]] | None = None
    en_codigo = False                          # dentro de otro bloque ``` (que no es pacai-ui)
    for linea in texto.splitlines(keepends=True):
        if ui is None:
            abre = _APERTURA.match(linea)
            delante = ''
            if not abre and not en_codigo:
                media = _APERTURA_A_MEDIA_LINEA.search(linea)
                if media and linea[:media.start()].strip() and '`' not in linea[:media.start()]:
                    abre, delante = media, linea[:media.start()].rstrip() + '\n'
            if abre and not lang and abre.group('lang'):
                abre, delante = None, ''           # sin el flag, una valla pacai-ui-lang es texto, como siempre
            if abre:
                if delante:
                    texto_actual.append(delante)
                if texto_actual:
                    trozos.append(('text', ''.join(texto_actual)))
                    texto_actual = []
                attrs: dict[str, Any] = dict(a.split('=', 1) for a in abre.group('attrs').split())
                if abre.group('lang'):
                    attrs['lang'] = True
                ui = (attrs, [])
                marca = abre.group('marca')
            else:
                if _OTRA_VALLA.match(linea):
                    en_codigo = not en_codigo
                texto_actual.append(linea)
        elif (reabre := _reapertura(linea, lang)) is not None:
            # Otra valla del perfil antes de cerrar ésta: el modelo empezó de nuevo («```.```pacai-ui-lang view=x»).
            # Si es la misma vista, gana el bloque nuevo (lo anterior quedó a medias); si es otra, éste se cierra.
            nuevos, marca = reabre
            if nuevos.get('view') == ui[0].get('view') or 'view' not in ui[0]:
                nuevos['reabierto'] = True
            else:
                trozos.append(('ui', *ui))
            ui = (nuevos, [])
        elif _cierra(linea, marca):
            trozos.append(('ui', *ui))
            ui = None
        elif linea.strip():
            ui[1].append(linea.strip())
    if ui is not None:
        trozos.append(('ui', *ui))
    if texto_actual:
        trozos.append(('text', ''.join(texto_actual)))
    return trozos


@dataclass
class Vista:
    rev: int = 0
    nodos: dict[str, dict] = field(default_factory=dict)
    estado: dict[str, Any] = field(default_factory=dict)
    alt: str | None = None
    placement: str | None = None


class Vistas:
    def __init__(self, catalogo: Catalogo):
        self.catalogo = catalogo
        self.vistas: dict[str, Vista] = {}
        self.ultima: str | None = None          # la última vista que cambió: la que el modelo acaba de ver

    def es_lang(self, lineas: list[str]) -> bool:
        """Si un bloque ```pacai-ui (el del JSON) trae en realidad PACAI UI Lang: sólo con el formato activo, sin
        ninguna línea que empiece por `{` y con cada línea leída como un nodo del catálogo o una baja `-id`."""
        if not self.catalogo.lang or not lineas or any(x.lstrip().startswith('{') for x in lineas):
            return False
        from .ui_lang import ErrorDeLinea, fichas, leer_linea

        tabla = fichas(self.catalogo)
        for linea in lineas:
            try:
                leida = leer_linea(linea)
            except ErrorDeLinea:
                return False
            if leida.tipo == 'cambio' or (leida.tipo == 'alta' and leida.componente not in tabla):
                return False
        return True

    def sin_valla(self, texto: str) -> dict | None:
        """Los atributos de un bloque de PACAI UI Lang escrito sin valla, o None: la respuesta entera se lee como
        un bloque, pero sólo con el formato compacto activo, sin ninguna valla ``` en el texto y si
        cada línea no vacía es una línea del formato (nodos del catálogo o bajas `-id`). Es un cambio de la última
        vista si alguna línea nombra (o cuelga) uno de sus nodos, aparte de root; si no, y trae root sin bajas, es un
        alta en una vista nueva llamada `pantalla` (o `pantalla_2`…). En otro caso es texto."""
        if not self.catalogo.lang or any(marca in texto for marca in MARCAS):
            return None
        from .ui_lang import ErrorDeLinea, fichas, leer_linea

        lineas = [x.strip() for x in texto.splitlines() if x.strip()]
        if not lineas or len(lineas) > self.catalogo.max_nodos:
            return None
        tabla = fichas(self.catalogo)
        ids: list[str] = []
        nombrados: set[str] = set()
        bajas = False
        for linea in lineas:
            try:
                leida = leer_linea(linea)
            except ErrorDeLinea:
                return None
            if leida.tipo == 'cambio' or (leida.tipo == 'alta' and leida.componente not in tabla):
                return None
            bajas = bajas or leida.tipo == 'baja'
            ids.append(leida.ident)
            nombrados.update([leida.ident, *leida.hijos])
        ultima = self.vistas.get(self.ultima or '')
        if ultima is not None and (nombrados - {'root'}) & set(ultima.nodos):
            return {'view': self.ultima, 'lang': True, 'sin_valla': True}
        if 'root' in ids and not bajas:
            nombre, n = 'pantalla', 1
            while nombre in self.vistas and self.vistas[nombre].nodos:
                n += 1
                nombre = f'pantalla_{n}'
            return {'view': nombre, 'lang': True, 'sin_valla': True}
        return None
        self.reparaciones: list[str] = []      # lo que el motor arregló en el último bloque sin cambiar lo pedido

    def rev(self, nombre: str) -> int | None:
        vista = self.vistas.get(nombre)
        return vista.rev if vista else None

    def revisiones(self) -> int:
        """La suma de las revisiones: sólo crece, así que cambia cada vez que alguna vista cambia."""
        return sum(v.rev for v in self.vistas.values())

    def a_datos(self) -> dict:
        """Las vistas para guardarlas con la sesión: al reanudarla siguen su revisión, no vuelven a la 1."""
        return {'version': 1, 'vistas': {n: {'rev': v.rev, 'nodos': list(v.nodos.values()), 'estado': v.estado,
                                             'alt': v.alt, 'placement': v.placement}
                                         for n, v in self.vistas.items()}}

    def cargar(self, datos: Any) -> None:
        """Recupera las vistas guardadas. Lo que no es válido se descarta, y también los nodos que el catálogo de
        este cliente no admite: la vista conserva su revisión y el resto de su contenido."""
        if not isinstance(datos, dict) or datos.get('version') != 1 or not isinstance(datos.get('vistas'), dict):
            return
        for nombre, d in datos['vistas'].items():
            if not isinstance(nombre, str) or not _NOMBRE.match(nombre) or not isinstance(d, dict):
                continue
            rev = d.get('rev')
            if type(rev) is not int or rev < 0:
                continue
            nodos = [n for n in d.get('nodos') or [] if not self.catalogo.errores_del_nodo(n)]
            estado = d.get('estado') if isinstance(d.get('estado'), dict) else {}
            self.vistas[nombre] = Vista(rev=rev, nodos={n['id']: n for n in nodos}, estado=estado,
                                        alt=d['alt'] if isinstance(d.get('alt'), str) else None,
                                        placement=d['placement'] if d.get('placement') in ('inline', 'latest')
                                        else None)

    def aplicar(self, attrs: dict, lineas: list[str]) -> tuple[dict | None, list[str]]:
        """Un bloque ```pacai-ui: el `data` de su `patch` (None si no quedó nada válido) y los errores. En una vista
        nueva sin `root`, la primera línea, si es un contenedor que no cuelga de nadie, pasa a llamarse `root`; si no,
        un `root` Stack vertical envuelve los nodos de primer nivel (las dos quedan en `reparaciones`)."""
        if attrs.get('lang') is True:
            return self.aplicar_lang(attrs, lineas)
        if self.es_lang(lineas):
            # El modelo escribió PACAI UI Lang en la valla del JSON (p. ej. un cliente le pide «un bloque pacai-ui»):
            # se lee como lo que es en vez de rechazar cada línea. `op` y `rev` del JSON no cuentan aquí.
            data, errores = self.aplicar_lang({**attrs, 'lang': True}, lineas)
            self.reparaciones.insert(0, 'Bloque pacai-ui con líneas de PACAI UI Lang: se lee como PACAI UI Lang.')
            return data, errores
        self.reparaciones = []
        nombre = ascii_de(attrs.get('view', ''), minusculas=True)
        if attrs.get('reabierto'):
            self.reparaciones.append(f'El bloque de {nombre} se volvió a abrir sin cerrarse: vale el último.')
        if not _NOMBRE.match(nombre):
            return None, ['El bloque pacai-ui necesita view=nombre ([a-z][a-z0-9_]*).']
        # Un alta: la vista no existe o no tiene nodos (un primer bloque que no dejó ninguno válido no cuenta).
        nueva = nombre not in self.vistas or not self.vistas[nombre].nodos
        root_rechazado = False       # el modelo escribió un root que no vale (aunque sea ilegible): se le pide
        hijos_rechazados: set[str] = set()     # hijos de un nodo rechazado: no suben a la raíz nueva
        primero: str | None = None             # el id de la primera línea de nodo si valió; '' si no valió
        placement = attrs.get('placement')
        errores: list[str] = []
        if placement not in (None, 'inline', 'latest'):
            errores.append('placement vale inline o latest.')
            placement = None
        vista = self.vistas.setdefault(nombre, Vista())
        nodos, quitar, estado = [], [], {}
        for n, linea in enumerate(lineas, 1):
            try:
                dato = json.loads(linea)
            except ValueError:
                errores.append(f'Línea {n}: no es JSON.')
                primero = '' if primero is None else primero
                root_rechazado = root_rechazado or bool(_ID_ROOT.search(linea))
                continue
            if isinstance(dato, dict) and dato and set(dato) <= {'remove', 'state', 'alt'}:
                quitar += [ascii_de(i) for i in dato.get('remove', []) if isinstance(i, str)]
                if isinstance(dato.get('state'), dict):
                    estado.update({ascii_de(k): v for k, v in dato['state'].items()})
                if isinstance(dato.get('alt'), str):
                    vista.alt = dato['alt']
                continue
            if isinstance(dato, dict) and 'component' in dato:
                # Despistes inofensivos de los modelos: un `alt` dentro del nodo es el resumen de la pantalla, y
                # otra clave que no es de un nodo se descarta en vez de perder el nodo entero.
                if isinstance(dato.get('alt'), str):
                    vista.alt = dato['alt']
                dato = {k: v for k, v in dato.items() if k in CLAVES_DE_NODO}
            dato = _normalizar_nodo(dato)
            fallos = self.catalogo.errores_del_nodo(dato)
            if not fallos and dato['id'] not in vista.nodos and len(vista.nodos) >= self.catalogo.max_nodos:
                fallos = [f'La pantalla ya tiene {self.catalogo.max_nodos} nodos.']
            if fallos:
                ident = dato.get('id') if isinstance(dato, dict) else None
                errores.append(f'Línea {n}' + (f' ({ident})' if ident else '') + ': ' + '; '.join(fallos))
                primero = '' if primero is None else primero
                root_rechazado = root_rechazado or ident == 'root' or bool(_ID_ROOT.search(linea))
                if isinstance(dato, dict) and isinstance(dato.get('children'), list) and ident not in vista.nodos:
                    hijos_rechazados.update(h for h in dato['children'] if isinstance(h, str))
                continue
            vista.nodos[dato['id']] = dato
            nodos.append(dato)
            primero = dato['id'] if primero is None else primero
        for ident in quitar:
            vista.nodos.pop(ident, None)
        if nueva and nodos and 'root' not in vista.nodos and not root_rechazado:
            from .ui_lang import root_que_envuelve, root_renombrado

            viejo = root_renombrado(vista.nodos, self.catalogo, primero or '', hijos_rechazados)
            root, motivo = (None, '') if viejo else root_que_envuelve(vista.nodos, self.catalogo, hijos_rechazados)
            if viejo:
                root = dict(vista.nodos.pop(viejo), id='root')
                vista.nodos = {'root': root, **vista.nodos}
                nodos = [root if n['id'] == viejo else n for n in nodos]
                self.reparaciones.append(f'Faltaba root: {viejo} va primero, no cuelga de nadie y es un contenedor; '
                                         'pasa a llamarse root.')
            elif root is None:
                errores.append(motivo)
            else:
                vista.nodos['root'] = root
                nodos.append(root)
                self.reparaciones.append('Faltaba root: un Stack vertical envuelve ' + ', '.join(root['children'])
                                         + '.')
        vista.estado.update(estado)
        if placement:
            vista.placement = placement
        if not (nodos or quitar or estado):
            return None, errores
        vista.rev += 1
        self.ultima = nombre
        data: dict[str, Any] = {'view': nombre, 'op': 'patch', 'rev': vista.rev, 'nodes': nodos}
        if quitar:
            data['remove'] = quitar
        if estado:
            data['state'] = estado
        if placement:
            data['placement'] = placement
        return data, errores

    def aplicar_lang(self, attrs: dict, lineas: list[str]) -> tuple[dict | None, list[str]]:
        """Un bloque ```pacai-ui-lang view=… (prototipo, versión 3). Si la vista ya existe es un cambio atómico contra
        la última revisión que se le enseñó al modelo; si no, un alta completa. El modelo nunca escribe `rev`: si lo
        pone, se ignora y queda anotado en `reparaciones`. Sin el flag se rechaza y el modelo debe usar el JSON."""
        from .ui_lang import Flujo, aplicar_cambio

        self.reparaciones = []
        if not self.catalogo.lang:
            return None, ['PACAI UI Lang no está activado con este modelo: usa el bloque pacai-ui con JSON.']
        nombre = attrs.get('view', '')
        if not isinstance(nombre, str) or not _NOMBRE.match(nombre):
            return None, ['El bloque pacai-ui-lang necesita view=nombre ([a-z][a-z0-9_]*).']
        if 'rev' in attrs:
            self.reparaciones.append(f'rev={attrs["rev"]} ignorado: la revisión la pone el motor.')
        if attrs.get('sin_valla'):
            self.reparaciones.append(f'Pantalla sin valla: se lee como PACAI UI Lang (view={nombre}).')
        if attrs.get('reabierto'):
            self.reparaciones.append(f'El bloque de {nombre} se volvió a abrir sin cerrarse: vale el último.')
        texto = '\n'.join(lineas)
        vista = self.vistas.get(nombre)
        if vista is not None and vista.nodos:
            cambio, diagnosticos = aplicar_cambio(vista.nodos, texto, self.catalogo)
            if cambio is None:
                return None, [d.texto() for d in diagnosticos]
            vista.nodos = cambio.nodos
            vista.rev += 1
            data: dict[str, Any] = {'view': nombre, 'op': 'patch', 'rev': vista.rev, 'nodes': cambio.actualizados}
            if cambio.borrados:
                data['remove'] = cambio.borrados
            self.reparaciones += cambio.avisos      # la última línea de un id ganó: no es un error
            self.ultima = nombre
            return data, []
        flujo = Flujo(self.catalogo)
        flujo.alimentar(texto + '\n')
        nodos, diagnosticos = flujo.cerrar()
        if not nodos:
            return None, [d.texto() for d in diagnosticos]
        self.reparaciones += flujo.reparaciones
        vista = self.vistas.setdefault(nombre, Vista())
        vista.nodos = {n['id']: n for n in nodos}
        vista.rev += 1
        self.ultima = nombre
        return ({'view': nombre, 'op': 'replace', 'rev': vista.rev, 'nodes': nodos, 'alt': self._resumen(vista)},
                [d.texto() for d in diagnosticos])

    def resumen_lang(self, nombre: str) -> str:
        """Lo que lee el modelo para cambiar la vista: sus líneas enteras en PACAI UI Lang (sin revisión)."""
        from .ui_lang import fichas, resumen

        return resumen(self.vistas[nombre].nodos, fichas(self.catalogo))

    def foto(self, nombre: str) -> dict:
        """El `replace` de la vista entera, con su resumen: la foto de referencia del `message_end`."""
        vista = self.vistas[nombre]
        vista.rev += 1
        data: dict[str, Any] = {'view': nombre, 'op': 'replace', 'rev': vista.rev,
                                'nodes': list(vista.nodos.values()), 'alt': vista.alt or self._resumen(vista)}
        if 'root' not in vista.nodos:
            data['nodes'].append({'id': 'root', 'component': 'Stack'})
        if vista.estado:
            data['state'] = dict(vista.estado)
        if vista.placement:
            data['placement'] = vista.placement
        return data

    @staticmethod
    def _resumen(vista: Vista) -> str:
        textos = []
        for nodo in vista.nodos.values():
            p = nodo.get('props', {})
            for clave in ('title', 'text', 'label'):
                if isinstance(p.get(clave), str):
                    textos.append(p[clave])
                    break
        return ('Pantalla: ' + ' · '.join(textos))[:300] if textos else 'Pantalla sin texto.'


def accion_para_el_modelo(textos: list[str], data: dict, rev_actual: int | None, pantalla: str = '') -> str:
    """El mensaje de usuario que lee el modelo por una acción `send` de una pantalla. `pantalla`: con PACAI UI Lang,
    las líneas enteras de la vista, para que pueda cambiarla con el mismo bloque."""
    datos = {k: data[k] for k in ('view', 'node', 'event', 'name', 'values', 'item') if k in data}
    aviso = ''
    if rev_actual is not None and data['rev'] < rev_actual:
        aviso = (f'\nOjo: el usuario tenía delante la revisión {data["rev"]} de la pantalla y ya va por la '
                 f'{rev_actual}; puede que la acción esté desfasada.')
    elif rev_actual is None:
        aviso = '\nOjo: esa pantalla no existe en esta sesión.'
    resumen = '\n'.join(t for t in textos if t.strip())
    return ((resumen + '\n\n') if resumen else '') + '[Acción en la pantalla] ' + json.dumps(
        datos, ensure_ascii=False) + aviso + (f'\nPantalla {data["view"]} ahora:\n{pantalla}' if pantalla else '')
