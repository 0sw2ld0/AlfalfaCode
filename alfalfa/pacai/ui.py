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
    'Stack': 'direction (vertical|horizontal), gap (none|small|medium|large)',
    'Card': 'title, subtitle',
    'Text': 'text (texto plano), style (body|title|subtitle|caption|muted)',
    'Table': 'columns [{key, label}], rows [objetos]',
    'List': 'items [{id, title, subtitle}]',
    'Button': 'label, variant (primary|secondary|danger), disabled',
    'Form': 'submit_label',
    'Input': 'name, label, kind (text|number|multiline|email|date), placeholder, required',
    'Select': 'name, label, options [{value, label}], multiple',
    'Chart': 'kind (bar|line|pie), labels [texto], series [{name, values [números]}]',
    'Image': 'src (https: o data:image/…), alt',
    'Code': 'code, language',
}
CLAVES_DE_NODO = ('id', 'component', 'props', 'children', 'events')
_NOMBRE = re.compile(r'^[a-z][a-z0-9_]*$')
_APERTURA = re.compile(r'^\s*```' + VALLA + r'(?P<attrs>(?:\s+[a-z_]+=\S+)*)\s*$')
_CIERRE = re.compile(r'^\s*```\s*$')


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
def _validador(definicion: str):
    import jsonschema

    return jsonschema.Draft202012Validator({**esquema(), '$ref': '#/$defs/' + definicion})


def errores_de(definicion: str, dato: Any) -> list[str]:
    """Errores de `dato` contra `$defs/<definicion>` del esquema (`declaration`, `node`, `update`, `action`)."""
    return [e.message for e in _validador(definicion).iter_errors(dato)]


def _acciones(spec: Any) -> list[dict]:
    return spec if isinstance(spec, list) else [spec]


# Sólo si el motor ofrece web_search y read_web: un modelo de código no las relaciona con «noticias» o «precios».
BUSCAR_ANTES = '''- Si la respuesta necesita información actual o de fuera (noticias, precios, tiempo, versiones,
  datos que no sabes), búscala primero con web_search y read_web; no digas que no tienes internet. Después
  enséñala en una pantalla y pon la url de cada fuente (en un Text o en un Button con open_url).
'''


@dataclass
class Catalogo:
    base: bool = True
    propios: dict[str, dict] = field(default_factory=dict)
    acciones: frozenset[str] = frozenset({'send', 'set', 'open_url'})
    max_nodos: int = MAX_NODOS
    max_profundidad: int = MAX_PROFUNDIDAD

    @classmethod
    def de(cls, declaracion: dict | None) -> Catalogo:
        """El catálogo de una declaración ya validada (`errores_de('declaration', …)`); None vale la de por defecto."""
        d = declaracion or {}
        limites = d.get('limits') or {}
        return cls(base=d.get('catalog', 'base') == 'base', propios=dict(d.get('components') or {}),
                   acciones=frozenset(d.get('actions', ('send', 'set', 'open_url'))),
                   max_nodos=limites.get('max_nodes', MAX_NODOS),
                   max_profundidad=limites.get('max_depth', MAX_PROFUNDIDAD))

    def componentes(self) -> list[str]:
        return (list(BASE) if self.base else []) + list(self.propios)

    def errores_del_nodo(self, nodo: Any) -> list[str]:
        errores = errores_de('node', nodo)
        if errores or not isinstance(nodo, dict):
            return errores
        nombre = nodo['component']
        if nombre in BASE and not self.base or nombre not in BASE and nombre not in self.propios:
            return ['El componente ' + nombre + ' no está en el catálogo del cliente.']
        if nombre in self.propios:
            spec = self.propios[nombre]
            import jsonschema

            errores += [e.message for e in jsonschema.Draft202012Validator(spec['props'])
                        .iter_errors(nodo.get('props', {}))]
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

    def manual(self, web: bool = False) -> str:
        """La parte del prompt de sistema que explica al modelo cómo dibujar en este cliente. Con `web` (el motor le
        ofrece web_search y read_web), también que busque lo que no sabe antes de dibujarlo."""
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
''' + '\n'.join(lineas) + '\n'


def partir(texto: str) -> list[tuple]:
    """Trozos del texto del modelo: ('text', str) o ('ui', atributos, líneas). Una valla sin cerrar llega al final."""
    trozos: list[tuple] = []
    texto_actual: list[str] = []
    ui: tuple[dict, list[str]] | None = None
    for linea in texto.splitlines(keepends=True):
        if ui is None:
            abre = _APERTURA.match(linea)
            if abre:
                if texto_actual:
                    trozos.append(('text', ''.join(texto_actual)))
                    texto_actual = []
                attrs = dict(a.split('=', 1) for a in abre.group('attrs').split())
                ui = (attrs, [])
            else:
                texto_actual.append(linea)
        elif _CIERRE.match(linea):
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

    def rev(self, nombre: str) -> int | None:
        vista = self.vistas.get(nombre)
        return vista.rev if vista else None

    def aplicar(self, attrs: dict, lineas: list[str]) -> tuple[dict | None, list[str]]:
        """Un bloque ```pacai-ui: el `data` de su `patch` (None si no quedó nada válido) y los errores."""
        nombre = ascii_de(attrs.get('view', ''), minusculas=True)
        if not _NOMBRE.match(nombre):
            return None, ['El bloque pacai-ui necesita view=nombre ([a-z][a-z0-9_]*).']
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
                continue
            vista.nodos[dato['id']] = dato
            nodos.append(dato)
        for ident in quitar:
            vista.nodos.pop(ident, None)
        vista.estado.update(estado)
        if placement:
            vista.placement = placement
        if not (nodos or quitar or estado):
            return None, errores
        vista.rev += 1
        data: dict[str, Any] = {'view': nombre, 'op': 'patch', 'rev': vista.rev, 'nodes': nodos}
        if quitar:
            data['remove'] = quitar
        if estado:
            data['state'] = estado
        if placement:
            data['placement'] = placement
        return data, errores

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


def accion_para_el_modelo(textos: list[str], data: dict, rev_actual: int | None) -> str:
    """El mensaje de usuario que lee el modelo por una acción `send` de una pantalla."""
    datos = {k: data[k] for k in ('view', 'node', 'event', 'name', 'values', 'item') if k in data}
    aviso = ''
    if rev_actual is not None and data['rev'] < rev_actual:
        aviso = (f'\nOjo: el usuario tenía delante la revisión {data["rev"]} de la pantalla y ya va por la '
                 f'{rev_actual}; puede que la acción esté desfasada.')
    elif rev_actual is None:
        aviso = '\nOjo: esa pantalla no existe en esta sesión.'
    resumen = '\n'.join(t for t in textos if t.strip())
    return ((resumen + '\n\n') if resumen else '') + '[Acción en la pantalla] ' + json.dumps(
        datos, ensure_ascii=False) + aviso
