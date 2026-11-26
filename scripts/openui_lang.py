"""OpenUI Lang 1.0 sobre el catálogo del perfil `ui`, para los pilotos de pantallas.

Sigue la especificación oficial (https://github.com/thesysdev/openui, `spec/language.md` y `spec/prompt.md`, versión
1.0, leída el 26-11-2026), no el SDK: no hay dependencias nuevas. Lo que sí hace como la especificación:

- una sentencia por línea, `nombre = Expresión`; una sentencia sigue tras el salto de línea si hay un `(`, `[` o `{`
  sin cerrar o una cadena sin terminar (§1.4);
- comas opcionales, cadenas con comillas dobles o simples, comentarios `//` y `#` fuera de las cadenas (§1.2, §1.3);
- argumentos posicionales en el orden de la firma; `null` salta un opcional (§1.5);
- `$clave` lee el estado (en PACAI, `{"$": "clave"}`);
- acciones con `@`: `@OpenUrl(url)`, `@Set($clave, valor)` y una acción propia de la librería, `@Send(nombre, with?)`,
  que es como se declaran en 1.0 las acciones del anfitrión (§6.3; en PACAI, `send`). Varias, en `Action([...])`;
- componentes en línea y referencias hacia adelante (§2.3); el último que define un nombre gana (§2.4);
- la entrada es `root`; si no hay `root`, la primera sentencia que llama al componente raíz de la librería (`Stack`)
  renderiza con un `no-root` que no es fatal (§2.2);
- cambios en modo edición (§7): el parche trae sólo las sentencias que cambian; un nombre que existe se sustituye, uno
  nuevo se añade, `nombre = null` borra, y después se quita lo que `root` ya no alcanza;
- streaming (§4): una sentencia completa no cambia; la cola pendiente se lee cerrando la cadena y los corchetes
  abiertos, y si aun así no se puede leer, no se produce.

Lo que no hace: expresiones con operadores, `@Each`, `Query`, `Mutation` ni funciones; el catálogo del perfil `ui`
no las usa. Un operador fuera de una cadena es un error de sintaxis.

La salida son nodos `pacai.ui/1` (`{id, component, props, children, events}`): así pasan el mismo validador que el
JSON de PACAI y PACAI UI Lang. Los componentes en línea reciben ids `_n1`, `_n2`…
"""
from __future__ import annotations

import json
import re
from functools import lru_cache

from alfalfa.pacai.ui import BASE, CON_HIJOS, EVENTOS, esquema

RAIZ_LIBRERIA = 'Stack'
VALLA = 'openui-lang'


# -- firmas ----------------------------------------------------------------------------------------------------
def _sin_ref(spec):
    """El esquema de una prop sin la alternativa de enlace (`{"$": …}`), que en OpenUI es `$clave`."""
    if isinstance(spec, dict) and 'anyOf' in spec:
        resto = [s for s in spec['anyOf'] if s.get('$ref') != '#/$defs/ref']
        if len(resto) == 1:
            return resto[0]
    return spec


def tipo_texto(spec) -> str:
    """El tipo de una prop como lo escribe la firma de OpenUI (`spec/prompt.md` §5.1)."""
    spec = _sin_ref(spec or {})
    if 'enum' in spec:
        return ' | '.join(json.dumps(v, ensure_ascii=False) for v in spec['enum'])
    if 'const' in spec:
        return json.dumps(spec['const'], ensure_ascii=False)
    if spec.get('$ref') == '#/$defs/key':
        return 'string'
    tipo = spec.get('type')
    if tipo in ('integer', 'number'):
        return 'number'
    if tipo in ('string', 'boolean'):
        return tipo
    if tipo == 'array':
        return (tipo_texto(spec['items']) if spec.get('items') else 'any') + '[]'
    if tipo == 'object':
        campos = spec.get('properties')
        if not campos:
            return 'object'
        req = spec.get('required', [])
        return '{' + ', '.join(k + ('' if k in req else '?') + ': ' + tipo_texto(v) for k, v in campos.items()) + '}'
    return 'any'


@lru_cache(maxsize=4)
def _firmas_de(clave: str):
    declaracion = json.loads(clave)
    base = esquema()['$defs']['base_props']
    firmas = {}
    componentes = [(c, base[c], c in CON_HIJOS, EVENTOS.get(c, [])) for c in BASE]
    componentes += [(n, s.get('props') or {}, bool(s.get('children')), s.get('events', []))
                    for n, s in declaracion.get('components', {}).items()]
    for nombre, props, hijos, eventos in componentes:
        propiedades = props.get('properties', {})
        req = [p for p in propiedades if p in props.get('required', [])]
        opt = [p for p in propiedades if p not in req]
        # Orden de la firma: las props obligatorias, los hijos, las opcionales y, al final, las acciones (una por
        # evento). Así los opcionales del final se pueden omitir, como en la librería de referencia de OpenUI.
        orden = [(p, propiedades[p], True) for p in req]
        if hijos:
            orden.append(('children', {'type': 'array'}, False))
        orden += [(p, propiedades[p], False) for p in opt]
        orden += [(e, {'action': True}, False) for e in eventos]
        firmas[nombre] = orden
    return firmas


def firmas(declaracion) -> dict:
    """Componente → [(argumento, esquema, obligatorio)], en el orden de los argumentos posicionales."""
    return _firmas_de(json.dumps(declaracion))


def firma_texto(nombre, orden) -> str:
    partes = []
    for arg, spec, obligatorio in orden:
        if arg == 'children':
            texto = 'children: Component[]'
        elif spec.get('action'):
            texto = arg + '?: ActionExpression'
        else:
            texto = arg + ('' if obligatorio else '?') + ': ' + tipo_texto(spec)
        partes.append(texto)
    return nombre + '(' + ', '.join(partes) + ')'


# -- de nodos PACAI a OpenUI Lang ------------------------------------------------------------------------------
_NOMBRE = re.compile(r'[A-Za-z_][A-Za-z0-9_]*')


def _valor(v) -> str:
    if isinstance(v, dict):
        if set(v) == {'$'}:
            return '$' + v['$']
        return '{' + ', '.join((k if _NOMBRE.fullmatch(k) else json.dumps(k, ensure_ascii=False)) + ': ' + _valor(x)
                               for k, x in v.items()) + '}'
    if isinstance(v, list):
        return '[' + ', '.join(_valor(x) for x in v) + ']'
    return json.dumps(v, ensure_ascii=False)


def _accion(a) -> str:
    if 'send' in a:
        con = ', ' + _valor(a['with']) if a.get('with') else ''
        return '@Send(' + json.dumps(a['send'], ensure_ascii=False) + con + ')'
    if 'open_url' in a:
        return '@OpenUrl(' + json.dumps(a['open_url'], ensure_ascii=False) + ')'
    if 'set' in a:
        pasos = ['@Set($' + k + ', ' + _valor(v) + ')' for k, v in a['set'].items()]
        return pasos[0] if len(pasos) == 1 else 'Action([' + ', '.join(pasos) + '])'
    raise ValueError('Acción desconocida: ' + json.dumps(a))


def _acciones(spec) -> str:
    lista = spec if isinstance(spec, list) else [spec]
    return _accion(lista[0]) if len(lista) == 1 else 'Action([' + ', '.join(_accion(a) for a in lista) + '])'


def a_openui(nodos, declaracion) -> str:
    """Los nodos `pacai.ui/1` como un programa OpenUI Lang, una sentencia por nodo, en el orden dado."""
    tabla = firmas(declaracion)
    lineas = []
    for n in nodos:
        orden = tabla[n['component']]
        props, eventos = n.get('props') or {}, n.get('events') or {}
        args = []
        for arg, spec, _ in orden:
            if arg == 'children':
                args.append('[' + ', '.join(n.get('children') or []) + ']')
            elif spec.get('action'):
                args.append(_acciones(eventos[arg]) if arg in eventos else None)
            else:
                args.append(_valor(props[arg]) if arg in props else None)
        while args and args[-1] is None:
            args.pop()
        lineas.append(f'{n["id"]} = {n["component"]}(' + ', '.join('null' if a is None else a for a in args) + ')')
    return '\n'.join(lineas)


# -- lectura -----------------------------------------------------------------------------------------------------
class ErrorOpenUI(ValueError):
    """Un error con su código de la especificación (`spec/language.md` §8.2) o de la capa común del benchmark."""

    def __init__(self, codigo, mensaje):
        super().__init__(mensaje)
        self.codigo = codigo


_NUM = re.compile(r'-?[0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?')
_SENTENCIA = re.compile(r'\s*(\$?[A-Za-z_][A-Za-z0-9_]*)\s*=(?!=)\s*')


def extraer(texto: str) -> str:
    """El programa de una respuesta (§1.2): el contenido de los bloques cercados si el primero empieza por una
    sentencia; si no, el texto de fuera si lo tiene. Un bloque sin cerrar llega al final."""
    bloques, fuera, actual, dentro = [], [], None, False
    for linea in texto.splitlines():
        if linea.lstrip().startswith('```') and linea.strip().count('```') == 1:
            if not dentro:
                dentro, actual = True, []
                resto = linea.lstrip()[3:].strip()
                etiqueta, _, codigo = resto.partition(' ')
                if codigo and _SENTENCIA.match(codigo):
                    actual.append(codigo)
                if etiqueta == 'openui-js':
                    actual = None
            else:
                dentro = False
                if actual is not None:
                    bloques.append('\n'.join(actual))
            continue
        if dentro:
            if actual is not None:
                actual.append(linea)
        else:
            fuera.append(linea)
    if dentro and actual is not None:
        bloques.append('\n'.join(actual))
    if bloques and _primera_sentencia(bloques[0]):
        return '\n'.join(bloques)
    texto_fuera = '\n'.join(fuera)
    # «Si el texto de fuera de los bloques tiene una sentencia, ese texto es el programa»; sus líneas sin la forma
    # `nombre = …` se saltan sin error (§1.4). Así se lee una valla abierta a media línea tras una frase.
    if any(_SENTENCIA.match(linea.strip()) for linea in texto_fuera.splitlines()):
        return texto_fuera
    return '\n'.join(bloques)


def _primera_sentencia(texto):
    for linea in texto.splitlines():
        s = linea.strip()
        if not s or s.startswith('//') or s.startswith('#'):
            continue
        return bool(_SENTENCIA.match(s))
    return False


def _sin_comentarios(linea: str) -> str:
    """Quita `//…` y `#…` que no estén dentro de una cadena (para una línea que empieza fuera de una cadena)."""
    salida, comilla, i = [], None, 0
    while i < len(linea):
        c = linea[i]
        if comilla:
            salida.append(c)
            if c == '\\' and i + 1 < len(linea):
                salida.append(linea[i + 1])
                i += 1
            elif c == comilla:
                comilla = None
        elif c in '"\'':
            comilla = c
            salida.append(c)
        elif c == '#' or linea.startswith('//', i):
            break
        else:
            salida.append(c)
        i += 1
    return ''.join(salida)


def sentencias(programa: str, cerrar_cola: bool = True) -> tuple[list[str], str | None]:
    """Parte el programa en sentencias completas (§1.4) y la cola pendiente, si la hay. Con `cerrar_cola` (fin del
    stream), la cola se cierra (§4, regla 7) y pasa a ser una sentencia más."""
    completas, actual, pila, comilla = [], '', [], None
    for linea in programa.split('\n'):
        if not comilla:
            linea = _sin_comentarios(linea)
        actual += ('\n' if actual else '') + linea
        i = len(actual) - len(linea)
        while i < len(actual):
            c = actual[i]
            if comilla:
                if c == '\\':
                    i += 1
                elif c == comilla:
                    comilla = None
            elif c in '"\'':
                comilla = c
            elif c in '([{':
                pila.append(c)
            elif c in ')]}' and pila and {'(': ')', '[': ']', '{': '}'}[pila[-1]] == c:
                pila.pop()
            i += 1
        if not pila and not comilla:
            if actual.strip():
                completas.append(actual.strip())
            actual = ''
    cola = actual.strip() or None
    if cola and cerrar_cola:
        completas.append(cerrar(cola))
        cola = None
    return completas, cola


def cerrar(cola: str) -> str:
    """Cierre implícito (§4, regla 2): cierra la cadena abierta y luego los corchetes, del último al primero."""
    pila, comilla, i = [], None, 0
    while i < len(cola):
        c = cola[i]
        if comilla:
            if c == '\\':
                i += 1
            elif c == comilla:
                comilla = None
        elif c in '"\'':
            comilla = c
        elif c in '([{':
            pila.append(c)
        elif c in ')]}' and pila and {'(': ')', '[': ']', '{': '}'}[pila[-1]] == c:
            pila.pop()
        i += 1
    salida = cola
    if comilla:
        salida += ('\\' if salida.endswith('\\') and not salida.endswith('\\\\') else '') + comilla
    return salida + ''.join({'(': ')', '[': ']', '{': '}'}[c] for c in reversed(pila))


class _Expr:
    """Lee una expresión. Devuelve valores Python, ('ref', nombre), ('comp', nombre, args), ('accion', nombre, args)
    o ('state', nombre)."""

    def __init__(self, texto):
        self.t, self.i = texto, 0

    def _esp(self):
        while self.i < len(self.t) and self.t[self.i] in ' \t\r\n,':
            self.i += 1

    def _esp_sin_coma(self):
        while self.i < len(self.t) and self.t[self.i] in ' \t\r\n':
            self.i += 1

    def fin(self):
        self._esp_sin_coma()
        if self.i < len(self.t):
            resto = self.t[self.i:self.i + 20]
            codigo = 'malformed-syntax' if resto[:1] not in '+-*/%?&|!<>=' else 'unsupported-expression'
            raise ErrorOpenUI(codigo, 'Sobra texto tras la expresión: «' + resto + '».')

    def _lista(self, cierre):
        salida = []
        while True:
            self._esp()
            if self.i >= len(self.t):
                raise ErrorOpenUI('malformed-syntax', 'Falta ' + cierre + '.')
            if self.t[self.i] == cierre:
                self.i += 1
                return salida
            salida.append(self.valor())

    def valor(self):
        self._esp_sin_coma()
        if self.i >= len(self.t):
            raise ErrorOpenUI('malformed-syntax', 'Falta un valor.')
        c = self.t[self.i]
        if c in '"\'':
            fin = self.i + 1
            while fin < len(self.t) and self.t[fin] != c:
                fin += 2 if self.t[fin] == '\\' else 1
            crudo = self.t[self.i + 1:fin]
            self.i = fin + 1
            if c == '"':
                try:
                    return json.loads('"' + crudo.replace('\n', '\\n') + '"')
                except json.JSONDecodeError as exc:
                    raise ErrorOpenUI('malformed-syntax', 'Cadena mal escrita: ' + crudo[:30]) from exc
            return re.sub(r"\\(['\\nt])", lambda m: {'n': '\n', 't': '\t'}.get(m.group(1), m.group(1)), crudo)
        if c == '[':
            self.i += 1
            return ['lista', self._lista(']')]
        if c == '{':
            self.i += 1
            obj = {}
            while True:
                self._esp()
                if self.i >= len(self.t):
                    raise ErrorOpenUI('malformed-syntax', 'Falta }.')
                if self.t[self.i] == '}':
                    self.i += 1
                    return obj
                if self.t[self.i] in '"\'':
                    clave = self.valor()
                else:
                    m = re.compile(r'\$?([A-Za-z0-9_]+)').match(self.t, self.i)
                    if not m:
                        raise ErrorOpenUI('malformed-syntax', 'Clave de objeto inválida.')
                    clave, self.i = m.group(1), m.end()
                self._esp_sin_coma()
                if not self.t.startswith(':', self.i):
                    raise ErrorOpenUI('malformed-syntax', 'Falta : tras la clave ' + str(clave) + '.')
                self.i += 1
                obj[str(clave)] = self.valor()
        if c == '$':
            m = re.compile(r'\$([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)').match(self.t, self.i)
            if not m:
                raise ErrorOpenUI('malformed-syntax', 'Un $ sin nombre.')
            self.i = m.end()
            return ('state', m.group(1))
        if c == '@':
            m = _NOMBRE.match(self.t, self.i + 1)
            if not m:
                raise ErrorOpenUI('malformed-syntax', 'Un @ sin nombre.')
            self.i = m.end()
            self._esp_sin_coma()
            if not self.t.startswith('(', self.i):
                raise ErrorOpenUI('malformed-syntax', '@' + m.group(0) + ' sin argumentos.')
            self.i += 1
            return ('accion', m.group(0), self._lista(')'))
        m = _NUM.match(self.t, self.i)
        if m and (c != '-' or self.i + 1 < len(self.t)):
            self.i = m.end()
            return json.loads(m.group(0)) if re.fullmatch(r'-?[0-9]+', m.group(0)) else float(m.group(0))
        m = _NOMBRE.match(self.t, self.i)
        if not m:
            raise ErrorOpenUI('malformed-syntax', 'No entiendo «' + self.t[self.i:self.i + 20] + '».')
        self.i = m.end()
        palabra = m.group(0)
        if palabra in ('true', 'false', 'null'):
            return {'true': True, 'false': False, 'null': None}[palabra]
        self._esp_sin_coma()
        if self.t.startswith('(', self.i) and (palabra[0].isupper() or palabra == 'Action'):
            self.i += 1
            args = self._lista(')')
            return ('accion', 'Action', args) if palabra == 'Action' else ('comp', palabra, args)
        return ('ref', palabra)


def leer(lineas_o_texto, declaracion):
    """Las sentencias de un programa: {nombre: valor leído} en orden, los errores por sentencia y las líneas sin la
    forma `nombre = …`, que la especificación salta sin error (§1.4)."""
    texto = lineas_o_texto if isinstance(lineas_o_texto, str) else '\n'.join(lineas_o_texto)
    completas, _ = sentencias(texto)
    programa, errores = {}, []
    for s in completas:
        m = _SENTENCIA.match(s)
        if not m:
            continue
        nombre = m.group(1)
        lector = _Expr(s[m.end():])
        try:
            valor = lector.valor()
            lector.fin()
        except ErrorOpenUI as exc:
            errores.append((nombre, exc.codigo, str(exc)))
            continue
        programa.pop(nombre, None)              # el último que define un nombre gana y queda en su sitio
        programa[nombre] = valor
    return programa, errores


class _Construir:
    """De sentencias leídas a nodos `pacai.ui/1`, con los errores de cada componente."""

    def __init__(self, programa, declaracion, primero=1):
        self.programa, self.tabla = programa, firmas(declaracion)
        self.nodos, self.errores, self.n = {}, [], primero - 1
        self.sin_resolver = set()

    def nodo_de_sentencia(self, nombre):
        valor = self.programa[nombre]
        if not (isinstance(valor, tuple) and valor[0] == 'comp'):
            return None
        return self._nodo(nombre, valor[1], valor[2])

    def _dato(self, v, donde):
        if isinstance(v, tuple):
            if v[0] == 'state':
                return {'$': v[1]}
            if v[0] == 'ref' and v[1] in self.programa and not isinstance(self.programa[v[1]], tuple):
                return self._dato(self.programa[v[1]], donde)          # una sentencia de datos (`labels = [...]`)
            if v[0] == 'ref':
                if v[1] not in self.programa:
                    self.sin_resolver.add(v[1])
                    return None
                raise ErrorOpenUI('type-mismatch', donde + ': un componente donde va un dato.')
            raise ErrorOpenUI('type-mismatch', donde + ': ' + v[0] + ' donde va un dato.')
        if isinstance(v, list) and v and v[0] == 'lista':
            return [x for x in (self._dato(x, donde) for x in v[1])]
        if isinstance(v, dict):
            return {k: self._dato(x, donde) for k, x in v.items()}
        return v

    def _accion(self, v, donde):
        if isinstance(v, tuple) and v[0] == 'ref' and v[1] in self.programa:
            v = self.programa[v[1]]
        if not (isinstance(v, tuple) and v[0] == 'accion'):
            raise ErrorOpenUI('type-mismatch', donde + ' espera una acción @… o Action([...]).')
        nombre, args = v[1], v[2]
        if nombre == 'Action':
            if len(args) != 1 or not (isinstance(args[0], list) and args[0] and args[0][0] == 'lista'):
                raise ErrorOpenUI('type-mismatch', 'Action espera una lista de acciones.')
            pasos = []
            for x in args[0][1]:
                paso = self._accion(x, donde)
                pasos += paso if isinstance(paso, list) else [paso]
            return pasos[0] if len(pasos) == 1 else pasos
        if nombre == 'Send':
            if not args or not isinstance(args[0], str) or len(args) > 2:
                raise ErrorOpenUI('type-mismatch', '@Send(nombre, with?): nombre es un texto.')
            accion = {'send': args[0]}
            if len(args) == 2 and args[1] is not None:
                con = self._dato(args[1], donde)
                if not (isinstance(con, list) and all(isinstance(k, str) for k in con)):
                    raise ErrorOpenUI('type-mismatch', '@Send: with es una lista de claves.')
                accion['with'] = con
            return accion
        if nombre == 'OpenUrl':
            if len(args) != 1 or not isinstance(args[0], str):
                raise ErrorOpenUI('type-mismatch', '@OpenUrl(url): url es un texto.')
            return {'open_url': args[0]}
        if nombre == 'Set':
            if len(args) != 2 or not (isinstance(args[0], tuple) and args[0][0] == 'state'):
                raise ErrorOpenUI('type-mismatch', '@Set($clave, valor).')
            return {'set': {args[0][1]: self._dato(args[1], donde)}}
        if nombre in ('ToAssistant', 'Reset', 'Run'):
            raise ErrorOpenUI('unsupported-action', '@' + nombre + ' no lo ejecuta este cliente.')
        raise ErrorOpenUI('unknown-function', '@' + nombre + ' no es una acción de la librería.')

    def _nodo(self, ident, comp, args):
        if comp not in self.tabla:
            raise ErrorOpenUI('unknown-component', comp + ' no está en la librería.')
        orden = self.tabla[comp]
        if len(args) > len(orden):
            raise ErrorOpenUI('excess-args', f'{comp} recibe {len(args)} argumentos y su firma tiene {len(orden)}.')
        nodo = {'id': ident, 'component': comp}
        props, eventos, hijos = {}, {}, None
        for (arg, spec, obligatorio), v in zip(orden, args):
            if v is None:
                if obligatorio:
                    raise ErrorOpenUI('null-required', f'{comp}: {arg} es obligatorio.')
                continue
            if arg == 'children':
                if not (isinstance(v, list) and v and v[0] == 'lista'):
                    raise ErrorOpenUI('type-mismatch', f'{comp}: children va entre [...].')
                hijos = []
                for h in v[1]:
                    if isinstance(h, tuple) and h[0] == 'ref':
                        if h[1] not in self.programa:
                            self.sin_resolver.add(h[1])
                        hijos.append(h[1])
                    elif isinstance(h, tuple) and h[0] == 'comp':
                        self.n += 1
                        nuevo = f'_n{self.n}'
                        try:
                            self.nodos[nuevo] = self._nodo(nuevo, h[1], h[2])
                            hijos.append(nuevo)
                        except ErrorOpenUI as exc:
                            self.errores.append((nuevo, exc.codigo, str(exc)))
                    elif h is not None:
                        raise ErrorOpenUI('type-mismatch', f'{comp}: children lleva algo que no es un componente.')
            elif spec.get('action'):
                eventos[arg] = self._accion(v, comp + '.' + arg)
            else:
                dato = self._dato(v, comp + '.' + arg)
                if dato is not None:
                    props[arg] = dato
        faltan = [a for a, _, o in orden[len(args):] if o]
        if faltan:
            raise ErrorOpenUI('missing-required', f'{comp}: faltan {", ".join(faltan)}.')
        if props:
            nodo['props'] = props
        if hijos:
            nodo['children'] = hijos
        if eventos:
            nodo['events'] = eventos
        return nodo


def _alcanzables(programa, entrada):
    vistos, pila = set(), [entrada]
    while pila:
        nombre = pila.pop()
        if nombre in vistos or nombre not in programa:
            continue
        vistos.add(nombre)
        pila += list(_referencias(programa[nombre]))
    return vistos


def _referencias(v):
    if isinstance(v, tuple):
        if v[0] == 'ref':
            yield v[1]
        elif v[0] in ('comp', 'accion'):
            for x in v[2]:
                yield from _referencias(x)
    elif isinstance(v, list):
        for x in v[1] if v and v[0] == 'lista' else v:
            yield from _referencias(x)
    elif isinstance(v, dict):
        for x in v.values():
            yield from _referencias(x)


def fusionar(actual: dict, parche: dict) -> dict:
    """Modo edición (§7): sustituye por nombre, añade los nuevos, `nombre = null` borra y, si hay `root`, quita lo que
    `root` ya no alcanza."""
    programa = dict(actual)
    for nombre, valor in parche.items():
        if valor is None:
            programa.pop(nombre, None)
        else:
            programa[nombre] = valor
    if 'root' in programa:
        vivos = _alcanzables(programa, 'root')
        programa = {k: v for k, v in programa.items() if k in vivos or k.startswith('$')}
    return programa


def mapa(lineas, declaracion, base=None):
    """(nodos `pacai.ui/1` por id, errores [(sentencia, código, mensaje)], avisos). Con `base` (el texto del programa
    actual) la respuesta es un parche en modo edición. Como la especificación, un error quita la sentencia o el
    componente y lo demás se dibuja; aquí cada error cuenta para la validez, como en el benchmark de OpenUI."""
    parche, errores = leer(lineas, declaracion)
    avisos = []
    if base is not None:
        actual, _ = leer(base, declaracion)
        programa = fusionar(actual, parche)
    else:
        programa = parche
    if not programa:
        return {}, errores + [(None, 'parse-failed', 'La respuesta no tiene ninguna sentencia.')], avisos
    entrada = 'root' if 'root' in programa else None
    if entrada is None:
        for nombre, valor in programa.items():
            if not nombre.startswith('$') and isinstance(valor, tuple) and valor[0] == 'comp' \
                    and valor[1] == RAIZ_LIBRERIA:
                entrada = nombre
                avisos.append('no-root')
                break
    construir = _Construir(programa, declaracion)
    for nombre in programa:
        if nombre.startswith('$'):
            continue
        try:
            nodo = construir.nodo_de_sentencia(nombre)
        except ErrorOpenUI as exc:
            errores.append((nombre, exc.codigo, str(exc)))
            continue
        if nodo is not None:
            construir.nodos[nombre] = nodo
    errores += construir.errores
    if entrada is None:
        errores.append((None, 'no-root', 'No hay root ni una sentencia que llame a ' + RAIZ_LIBRERIA + '.'))
        return construir.nodos, errores, avisos
    nodos = construir.nodos
    if entrada != 'root':                      # la entrada de reserva se dibuja como root
        nodos = {('root' if k == entrada else k): ({**v, 'id': 'root'} if k == entrada else v)
                 for k, v in nodos.items()}
        for v in nodos.values():
            if entrada in v.get('children', []):
                v['children'] = ['root' if h == entrada else h for h in v['children']]
    for nombre in sorted(construir.sin_resolver):
        errores.append((None, 'unresolved', nombre + ' no está definido.'))
    vivos = _alcanzables({('root' if k == entrada else k): v for k, v in programa.items()}, 'root')
    vivos |= {k for k in nodos if k.startswith('_n')}
    for nombre in programa:
        nombre_nodo = 'root' if nombre == entrada else nombre
        if not nombre.startswith('$') and nombre_nodo not in vivos and nombre_nodo in nodos:
            errores.append((nombre, 'orphan', nombre + ' no cuelga de root y no se dibuja.'))
    return nodos, errores, avisos
