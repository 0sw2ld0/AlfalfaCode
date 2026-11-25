"""Piezas declarativas del perfil `ui` (4.ª lluvia de ideas): prototipo, detrás de `ALFALFA_PACAI_UI_LANG`.

Son props y componentes que siguen siendo sólo datos, sin expresiones ni evaluador:

- `Calc preset=margen name=m compra=… venta=… cantidad=…` publica `m.pct` (fracción), `m.u` y `m.total` (dinero)
  y `m.perdida` (bool). La fórmula es fija: margen = (venta − compra) / venta.
- `si={"$":"vista","es":"resumen"}` en cualquier nodo: se ve sólo si el estado vale exactamente eso (comparado en
  NFC). El motor exige que `es` sea una de las `options` del Select que escribe esa clave.
- `rojo` en Text: `true` (o un enlace que vale `true`) lo pinta en rojo. `value` en Text lee un slot tipado y lo
  escribe con el formateador común.
- Input `required`, `kind`, `min`, `max`, `step` con la semántica de PACAI, no la de HTML (`1.5` con `step=1` falla
  en todos los clientes). Select con `required` empieza vacío; sin él, en su primera opción.
- Form bloquea su propio `submit` mientras un campo visible de su subárbol no es válido.
- `Data name=q query={"tipo":…}` pide una consulta que declara la app (`queries` en la declaración) y publica sus
  salidas tipadas, legibles sólo dentro de su subárbol (`{"$":"q.hoy"}`).

Este módulo es la implementación de referencia en Python: la web (`docs/demos/pacai-ui/ui.js`) hace lo mismo y las
dos pasan los vectores de `tests/conformidad/pacai-ui-piezas.json`. Los números van en coma fija (enteros ×10⁶,
coeficiente de menos de 18 cifras, redondeo half-even por operación): nada de float ni de locale.
"""
from __future__ import annotations

import copy
import re
import unicodedata
from datetime import date
from functools import lru_cache
from typing import Any

__all__ = ['ESCALA', 'SALIDAS_CALC', 'decimal', 'a_texto', 'calc', 'formatear', 'validar_campo', 'mensaje',
           'igual', 'estado_inicial', 'visibles', 'argumentos', 'esquema_piezas', 'errores_del_arbol',
           'COMPONENTES']

ESCALA = 10 ** 6
TOPE = 10 ** 18                       # coeficiente: menos de 18 cifras en total (12 enteras y 6 decimales)
COMPONENTES = ('Calc', 'Data')
SALIDAS_CALC = {'margen': {'pct': 'fraccion', 'u': 'dinero', 'total': 'dinero', 'perdida': 'bool'}}
TIPOS = ('texto', 'numero', 'entero', 'dinero', 'fraccion', 'bool', 'fecha', 'opciones', 'series')
_DECIMAL = re.compile(r'^(-?)(\d{1,12})(?:[.,](\d{1,6}))?$')
_FECHA = re.compile(r'^\d{4}-\d{2}-\d{2}$')
_CORREO = re.compile(r'^[^\s@]+@[^\s@]+\.[^\s@]+$')


# -- coma fija ---------------------------------------------------------------------------------------------
def decimal(valor: Any) -> int | None:
    """Lo que teclea el usuario (o un número del documento) en millonésimas; None si no es un número canónico.

    Admite `-12`, `12.5` y `12,5`, hasta 6 decimales y 12 cifras enteras; sin separador de miles, sin exponente
    y sin espacios dentro. Un `bool` no es un número."""
    if isinstance(valor, bool) or valor is None:
        return None
    if isinstance(valor, int):
        return valor * ESCALA if abs(valor) * ESCALA < TOPE else None
    if isinstance(valor, float):
        valor = repr(valor)
    if not isinstance(valor, str):
        return None
    m = _DECIMAL.match(valor.strip())
    if not m:
        return None
    signo, entera, fraccion = m.group(1), m.group(2), (m.group(3) or '').ljust(6, '0')
    n = int(entera) * ESCALA + int(fraccion)
    return -n if signo and n else n


def _redondear(n: int, divisor: int) -> int:
    """n / divisor redondeado half-even (al par en el empate)."""
    q, r = divmod(abs(n), abs(divisor))
    if 2 * r > abs(divisor) or (2 * r == abs(divisor) and q % 2):
        q += 1
    return -q if (n < 0) != (divisor < 0) and q else q


def _acotar(n: int | None) -> int | None:
    return None if n is None or abs(n) >= TOPE else n


def a_texto(n: int | None) -> str | None:
    """Millonésimas → texto canónico (`-12.5`, `3`): la forma en que los vectores comparan números."""
    if n is None:
        return None
    entera, fraccion = divmod(abs(n), ESCALA)
    texto = str(entera) + ('.' + str(fraccion).rjust(6, '0').rstrip('0') if fraccion else '')
    return ('-' if n < 0 else '') + texto


# -- Calc ---------------------------------------------------------------------------------------------------
def calc(preset: str, compra: Any = None, venta: Any = None, cantidad: Any = None) -> dict[str, Any]:
    """Las salidas de un `Calc`: números en texto canónico (`a_texto`) o None, y `perdida` bool o None. Un nulo
    se propaga; dividir por cero o desbordar da None."""
    if preset != 'margen':
        raise ValueError('Calc sólo conoce el preset margen.')
    c, v, n = decimal(compra), decimal(venta), decimal(cantidad)
    u = _acotar(v - c) if c is not None and v is not None else None
    pct = _acotar(_redondear(u * ESCALA, v)) if u is not None and v else None
    total = _acotar(_redondear(u * n, ESCALA)) if u is not None and n is not None else None
    perdida = v < c if c is not None and v is not None else None
    return {'pct': a_texto(pct), 'u': a_texto(u), 'total': a_texto(total), 'perdida': perdida}


# -- formateador común ---------------------------------------------------------------------------------------
def _miles(entera: int) -> str:
    texto = str(entera)
    grupos = []
    while len(texto) > 3:
        grupos.insert(0, texto[-3:])
        texto = texto[:-3]
    return '.'.join([texto] + grupos)


def _numero(n: int, decimales: int) -> str:
    r = _redondear(n, 10 ** (6 - decimales))
    entera, fraccion = divmod(abs(r), 10 ** decimales)
    texto = _miles(entera) + (',' + str(fraccion).rjust(decimales, '0') if decimales else '')
    return ('-' if r < 0 else '') + texto


def formatear(valor: Any, tipo: str) -> str:
    """Cómo se escribe un slot tipado, igual en todos los clientes y sin locale: `dinero` con 2 decimales,
    `fraccion` como porcentaje con 1 decimal, miles con punto y decimales con coma; nulo es «—»."""
    if valor is None:
        return '—'
    if tipo == 'bool':
        return 'sí' if valor is True else 'no' if valor is False else '—'
    if tipo in ('texto', 'fecha'):
        return valor if isinstance(valor, str) else '—'
    n = decimal(valor)
    if n is None:
        return '—'
    if tipo == 'dinero':
        return _numero(n, 2)
    if tipo == 'fraccion':
        return _numero(n * 100, 1) + ' %' if abs(n * 100) < TOPE else '—'
    if tipo == 'entero':
        return _numero(n, 0)
    if tipo == 'numero':
        texto = a_texto(n)
        entera, _, fraccion = texto.lstrip('-').partition('.')
        return ('-' if n < 0 else '') + _miles(int(entera)) + (',' + fraccion if fraccion else '')
    return '—'


# -- validación de campos ------------------------------------------------------------------------------------
def igual(a: Any, b: Any) -> bool:
    """Igualdad de texto en NFC: `café` precompuesto y `cafe`+U+0301 son lo mismo en todos los clientes."""
    if isinstance(a, str) and isinstance(b, str):
        return unicodedata.normalize('NFC', a) == unicodedata.normalize('NFC', b)
    return a == b and type(a) is type(b)


def _vacio(valor: Any) -> bool:
    return valor is None or valor == '' or valor == [] or (isinstance(valor, str) and not valor.strip())


def validar_campo(componente: str, props: dict, valor: Any) -> str | None:
    """El código del primer error de un Input o Select (None si vale). `props` ya trae sus enlaces resueltos.

    Códigos: obligatorio, numero, minimo, maximo, paso, fecha, correo, opcion."""
    if _vacio(valor):
        return 'obligatorio' if props.get('required') is True else None
    if componente == 'Select':
        opciones = [o.get('value') for o in props.get('options') or [] if isinstance(o, dict)]
        elegidos = valor if isinstance(valor, list) else [valor]
        return None if all(any(igual(e, o) for o in opciones) for e in elegidos) else 'opcion'
    kind = props.get('kind', 'text')
    if kind == 'number':
        n = decimal(valor)
        if n is None:
            return 'numero'
        minimo, maximo, paso = decimal(props.get('min')), decimal(props.get('max')), decimal(props.get('step'))
        if minimo is not None and n < minimo:
            return 'minimo'
        if maximo is not None and n > maximo:
            return 'maximo'
        if paso is not None and paso > 0 and (n - (minimo or 0)) % paso:
            return 'paso'
        return None
    if kind == 'date':
        if not isinstance(valor, str) or not _FECHA.match(valor):
            return 'fecha'
        try:
            date.fromisoformat(valor)
        except ValueError:
            return 'fecha'
        minimo, maximo = props.get('min'), props.get('max')
        if isinstance(minimo, str) and _FECHA.match(minimo) and valor < minimo:
            return 'minimo'
        if isinstance(maximo, str) and _FECHA.match(maximo) and valor > maximo:
            return 'maximo'
        return None
    if kind == 'email':
        return None if isinstance(valor, str) and _CORREO.match(valor.strip()) else 'correo'
    return None


def mensaje(codigo: str, props: dict) -> str:
    """El texto que ve el usuario bajo el campo (sólo se dibuja: nunca vuelve en un `send`)."""
    tipo = 'fecha' if props.get('kind') == 'date' else 'numero'
    return {
        'obligatorio': 'Obligatorio.',
        'numero': 'Escribe un número.',
        'minimo': 'Mínimo ' + formatear(props.get('min'), tipo) + '.',
        'maximo': 'Máximo ' + formatear(props.get('max'), tipo) + '.',
        'paso': 'Usa pasos de ' + formatear(props.get('step'), 'numero') + '.',
        'fecha': 'Escribe una fecha (AAAA-MM-DD).',
        'correo': 'Escribe un correo válido.',
        'opcion': 'Elige una de las opciones.',
    }.get(codigo, 'No es válido.')


# -- estado y visibilidad -------------------------------------------------------------------------------------
def estado_inicial(nodos: dict[str, dict], estado: dict | None = None) -> dict:
    """El estado con el valor de partida de cada Select: vacío con `required`; si no, su primera opción."""
    salida = dict(estado or {})
    for nodo in nodos.values():
        p = nodo.get('props') or {}
        if nodo.get('component') != 'Select' or not isinstance(p.get('name'), str) or p['name'] in salida:
            continue
        opciones = p.get('options') if isinstance(p.get('options'), list) else []
        if p.get('multiple'):
            salida[p['name']] = []
        elif p.get('required') is True or not opciones:
            salida[p['name']] = ''
        else:
            salida[p['name']] = opciones[0].get('value')
    return salida


def visible(nodo: dict, estado: dict) -> bool:
    si = (nodo.get('props') or {}).get('si')
    return not isinstance(si, dict) or igual(estado.get(si.get('$')), si.get('es'))


def visibles(nodos: dict[str, dict], estado: dict) -> list[str]:
    """Los ids que se dibujan, en orden DFS desde root: un nodo con `si` falso se salta con su subárbol."""
    salida: list[str] = []

    def recorrer(ident: str, profundidad: int) -> None:
        nodo = nodos.get(ident)
        if nodo is None or ident in salida or profundidad > 12 or not visible(nodo, estado):
            return
        salida.append(ident)
        for hijo in nodo.get('children') or []:
            recorrer(hijo, profundidad + 1)

    recorrer('root', 0)
    return salida


# -- esquema y validación del motor ---------------------------------------------------------------------------
@lru_cache(maxsize=1)
def esquema_piezas() -> dict:
    """El esquema de `pacai.ui/1` con las piezas: el base no cambia (los clientes sin flag siguen igual)."""
    from .ui import esquema

    s = copy.deepcopy(esquema())
    d = s['$defs']
    d['ref']['properties']['$']['pattern'] = r'^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)?$'
    d['si'] = {'type': 'object', 'properties': {'$': {'$ref': '#/$defs/key'}, 'es': {'type': 'string'}},
               'required': ['$', 'es'], 'additionalProperties': False}
    numero = {'anyOf': [{'type': 'number'}, {'type': 'string'}, {'$ref': '#/$defs/ref'}]}
    for props in d['base_props'].values():
        props['properties']['si'] = {'$ref': '#/$defs/si'}
    texto = d['base_props']['Text']
    texto['properties'].update(rojo={'anyOf': [{'type': 'boolean'}, {'$ref': '#/$defs/ref'}]},
                               value={'$ref': '#/$defs/ref'})
    texto['required'] = []
    texto['anyOf'] = [{'required': ['text']}, {'required': ['value']}]
    d['base_props']['Input']['properties'].update(min=numero, max=numero,
                                                  step={'type': 'number', 'exclusiveMinimum': 0})
    d['base_props']['Select']['properties']['required'] = {'type': 'boolean'}
    d['base_props']['Calc'] = {
        'type': 'object', 'properties': {'name': {'$ref': '#/$defs/key'}, 'preset': {'enum': list(SALIDAS_CALC)},
                                         'compra': numero, 'venta': numero, 'cantidad': numero,
                                         'si': {'$ref': '#/$defs/si'}},
        'required': ['name', 'preset'], 'additionalProperties': False}
    d['base_props']['Data'] = {
        'type': 'object', 'properties': {'name': {'$ref': '#/$defs/key'},
                                         'query': {'type': 'object', 'properties': {'tipo': {'type': 'string'}},
                                                   'required': ['tipo']},
                                         'si': {'$ref': '#/$defs/si'}},
        'required': ['name', 'query'], 'additionalProperties': False}
    reglas = d['node']['allOf']
    reglas.append({'if': {'properties': {'component': {'const': 'Calc'}}, 'required': ['component']},
                   'then': {'properties': {'props': {'$ref': '#/$defs/base_props/Calc'},
                                           'events': {'maxProperties': 0}},
                            'not': {'required': ['children']}, 'required': ['props']}})
    reglas.append({'if': {'properties': {'component': {'const': 'Data'}}, 'required': ['component']},
                   'then': {'properties': {'props': {'$ref': '#/$defs/base_props/Data'},
                                           'events': {'maxProperties': 0}}, 'required': ['props']}})
    return s


def _referencias(valor: Any):
    """Los enlaces `{"$": clave}` de una prop, también dentro de objetos y listas (los argumentos de `query`)."""
    if isinstance(valor, dict):
        if set(valor) == {'$'} and isinstance(valor['$'], str):
            yield valor['$']
            return
        for v in valor.values():
            yield from _referencias(v)
    elif isinstance(valor, list):
        for v in valor:
            yield from _referencias(v)


def salidas_de(nodo: dict, consultas: dict) -> dict[str, str]:
    """Los slots tipados que publica un Calc o un Data (vacío si no publica nada)."""
    p = nodo.get('props') or {}
    if nodo.get('component') == 'Calc':
        return SALIDAS_CALC.get(p.get('preset'), {})
    if nodo.get('component') == 'Data' and isinstance(p.get('query'), dict):
        return dict((consultas.get(p['query'].get('tipo')) or {}).get('out') or {})
    return {}


def errores_del_arbol(nodos: dict[str, dict], consultas: dict) -> list[tuple[str, str]]:
    """Lo que el esquema de un nodo suelto no ve: `si` contra las `options` de su Select, consultas declaradas y
    el alcance de los slots `x.y`. Devuelve (id, motivo); el nodo se descarta entero, sin corregirlo."""
    errores: list[tuple[str, str]] = []
    selects: dict[str, list] = {}
    for nodo in nodos.values():
        p = nodo.get('props') or {}
        if nodo.get('component') == 'Select' and isinstance(p.get('name'), str) and isinstance(p.get('options'), list):
            selects.setdefault(p['name'], []).extend(o.get('value') for o in p['options'] if isinstance(o, dict))
    calcs = {n['props']['name']: salidas_de(n, consultas) for n in nodos.values()
             if n.get('component') == 'Calc' and isinstance((n.get('props') or {}).get('name'), str)}
    for ident, nodo in nodos.items():
        si = (nodo.get('props') or {}).get('si')
        if isinstance(si, dict):
            clave, valor = si.get('$'), si.get('es')
            if clave not in selects:
                errores.append((ident, f'si lee «{clave}», que no escribe ningún Select.'))
            elif not any(igual(valor, o) for o in selects[clave]):
                errores.append((ident, f'si es="{valor}" no es una opción de {clave}: '
                                + ', '.join(str(o) for o in selects[clave]) + '.'))
        if nodo.get('component') == 'Data':
            tipo = ((nodo.get('props') or {}).get('query') or {}).get('tipo')
            if tipo not in consultas:
                errores.append((ident, f'La consulta «{tipo}» no la declara la app'
                                + (': ' + ', '.join(sorted(consultas)) if consultas else '') + '.'))

    vistos: set[str] = set()

    def recorrer(ident: str, ambito: dict[str, dict]) -> None:
        nodo = nodos.get(ident)
        if nodo is None or ident in vistos:
            return
        vistos.add(ident)
        for clave, valor in (nodo.get('props') or {}).items():
            if clave == 'si':
                continue
            for ref in _referencias(valor):
                if '.' not in ref:
                    continue
                prefijo, slot = ref.split('.', 1)
                salidas = ambito.get(prefijo) if prefijo in ambito else calcs.get(prefijo)
                if salidas is None:
                    errores.append((ident, f'{ref} no existe aquí: las salidas de Data sólo se leen dentro de '
                                    'su subárbol.'))
                elif salidas and slot not in salidas:      # sin salidas: ya falla su Data
                    errores.append((ident, f'{prefijo} no publica {slot}: ' + ', '.join(salidas) + '.'))
        if nodo.get('component') == 'Data' and isinstance((nodo.get('props') or {}).get('name'), str):
            ambito = {**ambito, nodo['props']['name']: salidas_de(nodo, consultas)}
        for hijo in nodo.get('children') or []:
            recorrer(hijo, ambito)

    recorrer('root', {})
    return errores


def argumentos(valor: Any, leer) -> Any:
    """Los argumentos de una `query` de Data con sus enlaces leídos (`leer(clave)`); None mientras falte alguno
    (nulo o vacío): Data espera a tenerlos todos antes de pedir."""
    if isinstance(valor, dict):
        if set(valor) == {'$'} and isinstance(valor['$'], str):
            leido = leer(valor['$'])
            return None if leido is None or leido == '' else leido
        salida = {}
        for k, v in valor.items():
            salida[k] = argumentos(v, leer)
            if salida[k] is None and v is not None:
                return None
        return salida
    if isinstance(valor, list):
        salida_lista = [argumentos(v, leer) for v in valor]
        return None if any(s is None and v is not None for s, v in zip(salida_lista, valor)) else salida_lista
    return valor
