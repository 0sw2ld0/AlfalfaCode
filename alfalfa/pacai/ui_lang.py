"""PACAI UI Lang (3.ª lluvia de ideas): una forma compacta, sólo datos, de escribir nodos `pacai.ui/1`.

Prototipo, **apagado por defecto**: sólo se usa con `ALFALFA_PACAI_UI_LANG=1`. Sin el flag el motor ni lo enseña ni
lo lee, y el JSON de siempre sigue siendo el camino normal. Los clientes no cambian: el motor lo traduce exacto a
`pacai.ui/1`.

Una línea por nodo, en DFS desde `root`:

    id Comp ["posicional"] clave=valor … > hijo,hijo

- El posicional es una sola cadena JSON justo después del componente y es la prop que declara el catálogo
  (`Text.text`, `Panel.title`…). Nombrarla además, o ponerlo en un componente que no lo declara, es un error.
- Valores: JSON (`"texto"`, números, `true`, objetos, listas: `query` va como JSON compacto), una palabra suelta es
  un enum (`direction=vertical`) y `$clave` lee el estado (`{"$": "clave"}`). `@evento=` lleva las acciones.
- Herencia: el catálogo declara `inherit` (`{"period": "periodo"}`) y el motor pone `{"$": "periodo"}` si la prop
  falta. Un valor explícito prevalece.

Cambios (bloque con `rev=N`, la revisión que el motor le dio al modelo), aplicados enteros o nada:

    ~id ["pos"] clave=valor …   cambia props y conserva los hijos
    ~id … +> a,b                 añade hijos al final (uno que ya estaba se ignora con un aviso)
    ~id … > a,b,c                exige la lista completa de hijos actuales no borrados (sirve para reordenar)
    -id                          borra el nodo (no si tiene más de un padre) y lo que sólo colgaba de él
    id Comp …                    alta de un nodo nuevo

Streaming (`Flujo`): se parsea sólo la línea completa; cada una se valida al llegar y se publica en cuanto ella y
sus ancestros hasta `root` existen. Por contenedor se publica sólo el prefijo contiguo de hijos llegados, así que
cada publicación es un árbol válido y sólo crece. Una línea mala se descarta sin corregirla; al cerrar, el motor
pide de una vez todas las que faltan y acepta un solo reintento.
"""
from __future__ import annotations

import copy
import json
import os
import re
from dataclasses import dataclass, field
from typing import Any

from . import piezas
from .ui import BASE, CON_HIJOS, EVENTOS, Catalogo

__all__ = ['FLAG', 'VALLA_LANG', 'activo', 'Ficha', 'fichas', 'ErrorDeLinea', 'Linea', 'leer_linea', 'nodo_de',
           'Flujo', 'traducir', 'aplicar_cambio', 'a_lang', 'resumen', 'prompt', 'prompt_piezas', 'MAX_LINEAS_PROMPT']

FLAG = 'ALFALFA_PACAI_UI_LANG'
VALLA_LANG = 'pacai-ui-lang'
MAX_LINEAS_PROMPT = 15

# El posicional y la línea del prompt de cada componente base (el catálogo base no lleva estas marcas en su esquema).
POSICIONAL_BASE = {'Text': 'text', 'Card': 'title', 'Button': 'label', 'Input': 'label', 'Select': 'label',
                   'Form': 'submit_label', 'Code': 'code'}
# Cada prop como se escribe (`clave=…`, `clave=a|b`, `clave=true`): una palabra suelta la copia el modelo tal cual.
PROMPT_BASE = {
    'Stack': 'direction=vertical|horizontal|grid gap=none|small|medium|large',
    'Card': 'subtitle="…" meta="…" tags=[…] detail="…" image="https://…"',
    'Text': 'style=body|title|subtitle|caption|muted',
    'Table': 'columns=[{"key":…,"label":…}] rows=[…]',
    'List': 'items=[{"id":…,"title":…,"subtitle":…}]',
    'Button': 'variant=primary|secondary|danger disabled=true',
    'Form': '',
    'Input': 'name=… kind=text|number|multiline|email|date placeholder="…" required=true',
    'Select': 'name=… options=[{"value":…,"label":…}] multiple=true',
    'Chart': 'kind=bar|line|pie labels=[…] series=[{"name":…,"values":[…]}]',
    'Image': 'src="https://…" alt="…"',
    'Code': 'language=…',
    'Calc': 'name=m preset=margen compra=$… venta=$… cantidad=$…',
    'Data': 'name=q query={"tipo":…}',
}

_ID = re.compile(r'[a-z][a-z0-9_]*')
_COMP = re.compile(r'[A-Z][A-Za-z0-9]*')
_CLAVE = re.compile(r'(@?)([A-Za-z_][A-Za-z0-9_]*)=')
_ENLACE = re.compile(r'\$([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?)')
_PALABRA = re.compile(r'[^\s"{}\[\]>,=]+')
_JSON = json.JSONDecoder()


def activo(entorno: dict | None = None) -> bool:
    return (os.environ if entorno is None else entorno).get(FLAG, '').strip().lower() in ('1', 'true', 'si', 'sí')


# -- catálogo ------------------------------------------------------------------------------------------------
@dataclass
class Ficha:
    """Lo que el formato necesita saber de un componente."""
    componente: str
    posicional: str | None = None
    hereda: dict[str, str] = field(default_factory=dict)
    hijos: bool = False
    eventos: tuple[str, ...] = ()
    resumen: str = ''


def fichas(catalogo: Catalogo) -> dict[str, Ficha]:
    """Las fichas de los componentes del catálogo. Uno propio declara `positional` (nombre de prop) e `inherit`
    (prop → clave del estado) junto a su `props`; el esquema de sus props da la línea del prompt."""
    salida: dict[str, Ficha] = {}
    if catalogo.base:
        for nombre in BASE:
            salida[nombre] = Ficha(nombre, POSICIONAL_BASE.get(nombre), {}, nombre in CON_HIJOS,
                                   tuple(EVENTOS.get(nombre, ())), PROMPT_BASE[nombre])
    if catalogo.piezas:
        salida['Calc'] = Ficha('Calc', resumen=PROMPT_BASE['Calc'])
        salida['Data'] = Ficha('Data', hijos=True, resumen=PROMPT_BASE['Data'])
    for nombre, spec in catalogo.propios.items():
        hereda = spec.get('inherit') if isinstance(spec.get('inherit'), dict) else {}
        posicional = spec.get('positional') if isinstance(spec.get('positional'), str) else None
        salida[nombre] = Ficha(nombre, posicional, dict(hereda), bool(spec.get('children')),
                               tuple(spec.get('events') or ()), _resumen_de(spec, posicional, hereda))
    return salida


def _resumen_de(spec: dict, posicional: str | None, hereda: dict) -> str:
    """La línea del prompt de un componente propio, sacada de su esquema: enums como `a|b` y las consultas con su
    `tipo` fijo y el resto de sus claves."""
    partes = []
    for clave, esquema in ((spec.get('props') or {}).get('properties') or {}).items():
        if clave == posicional or clave in hereda:
            continue
        if isinstance(esquema.get('enum'), list):
            partes.append(clave + '=' + '|'.join(str(v) for v in esquema['enum']))
        elif esquema.get('type') == 'object' and isinstance(esquema.get('properties'), dict):
            # Una pista que ya es JSON válido: el modelo la copia tal cual (`"campo":"subieron|bajaron"`).
            interior = []
            for k, v in esquema['properties'].items():
                if 'const' in v:
                    interior.append(json.dumps(k) + ':' + json.dumps(v['const'], ensure_ascii=False))
                elif isinstance(v.get('enum'), list):
                    interior.append(json.dumps(k) + ':"' + '|'.join(str(x) for x in v['enum']) + '"')
                elif v.get('type') == 'array':
                    interior.append(json.dumps(k) + ':[…]')
                else:
                    interior.append(json.dumps(k) + ':…')
            partes.append(clave + '={' + ','.join(interior) + '}')
        elif esquema.get('type') == 'boolean':
            partes.append(clave + '=true')
        else:
            partes.append(clave + '=…')
    return ' '.join(partes)


# -- una línea -----------------------------------------------------------------------------------------------
class ErrorDeLinea(ValueError):
    def __init__(self, motivo: str, ident: str | None = None):
        super().__init__(motivo)
        self.motivo, self.ident = motivo, ident


@dataclass
class Linea:
    tipo: str                                   # alta | cambio | baja
    ident: str
    componente: str | None = None
    posicional: Any = None
    hay_posicional: bool = False
    props: dict[str, Any] = field(default_factory=dict)
    eventos: dict[str, Any] = field(default_factory=dict)
    operador: str | None = None                 # None | '>' | '+>'
    hijos: list[str] = field(default_factory=list)


def _valor(texto: str, i: int) -> tuple[Any, int]:
    """El valor que empieza en `texto[i]` y dónde acaba."""
    if texto.startswith('$', i):
        m = _ENLACE.match(texto, i)
        if not m:
            raise ErrorDeLinea('Un enlace es $clave (letras, cifras y _).')
        return {'$': m.group(1)}, m.end()
    if texto[i] in '"{[' or texto[i] == '-' or texto[i].isdigit() or texto.startswith(('true', 'false', 'null'), i):
        try:
            valor, fin = _JSON.raw_decode(texto, i)
        except ValueError:
            valor, fin = None, -1
        if fin != -1 and (fin == len(texto) or texto[fin].isspace()):
            return valor, fin
        if texto[i] in '"{[':
            raise ErrorDeLinea('JSON mal cerrado en «' + texto[i:i + 40] + '».')
    m = _PALABRA.match(texto, i)
    if not m:
        raise ErrorDeLinea('No entiendo «' + texto[i:i + 30] + '».')
    return m.group(0), m.end()


def leer_linea(linea: str) -> Linea:
    """Una línea de PACAI UI Lang, sin mirar el catálogo. `ErrorDeLinea` si no sigue la gramática."""
    texto = linea.strip()
    if texto.startswith('-'):
        m = _ID.fullmatch(texto[1:])
        if not m:
            raise ErrorDeLinea('Una baja es -id, sin nada más.')
        return Linea('baja', texto[1:])
    if texto.startswith('~'):
        m = _ID.match(texto, 1)
        if not m:
            raise ErrorDeLinea('Un cambio empieza por ~id.')
        salida, i = Linea('cambio', m.group(0)), m.end()
    else:
        m = _ID.match(texto)
        if not m or m.end() < len(texto) and not texto[m.end()].isspace():
            raise ErrorDeLinea('Una línea empieza por un id [a-z][a-z0-9_]*.')
        salida = Linea('alta', m.group(0))
        j = m.end()
        while j < len(texto) and texto[j].isspace():
            j += 1
        c = _COMP.match(texto, j) if j > m.end() else None
        if not c or c.end() < len(texto) and not texto[c.end()].isspace():
            raise ErrorDeLinea('Falta el componente después del id.', salida.ident)
        salida.componente, i = c.group(0), c.end()
    primero = True
    try:
        while i < len(texto):
            if texto[i].isspace():
                i += 1
                continue
            if texto.startswith(('+>', '>'), i):
                salida.operador = '+>' if texto[i] == '+' else '>'
                resto = texto[i + len(salida.operador):].strip()
                hijos = [h.strip() for h in resto.split(',')] if resto else []
                if not hijos or any(not _ID.fullmatch(h) for h in hijos):
                    raise ErrorDeLinea('Los hijos van como id,id tras ' + salida.operador + ', al final de la línea.')
                if len(set(hijos)) != len(hijos):
                    raise ErrorDeLinea('Un hijo repetido en la lista.')
                salida.hijos = hijos
                break
            if texto[i] == '"':
                if not primero:
                    raise ErrorDeLinea('Un texto suelto sólo va justo después del componente (o de ~id).')
                salida.posicional, i = _valor(texto, i)
                salida.hay_posicional = True
                primero = False
                continue
            m = _CLAVE.match(texto, i)
            if not m:
                raise ErrorDeLinea('Esperaba clave=valor en «' + texto[i:i + 30] + '».')
            if m.end() >= len(texto) or texto[m.end()].isspace():
                raise ErrorDeLinea(m.group(2) + '= sin valor.')
            valor, i = _valor(texto, m.end())
            destino = salida.eventos if m.group(1) else salida.props
            if m.group(2) in destino:
                raise ErrorDeLinea(m.group(2) + ' repetida.')
            if m.group(1) and not isinstance(valor, (dict, list)):
                raise ErrorDeLinea('@' + m.group(2) + ' lleva acciones en JSON: {"send":"…"}.')
            destino[m.group(2)] = valor
            primero = False
    except ErrorDeLinea as exc:
        exc.ident = salida.ident
        raise
    return salida


def nodo_de(linea: Linea, catalogo: Catalogo, tabla: dict[str, Ficha], ficha: Ficha | None = None) -> dict:
    """El nodo `pacai.ui/1` de una línea de alta, ya validado contra el catálogo. `ErrorDeLinea` si no vale."""
    ficha = ficha or tabla.get(linea.componente or '')
    if ficha is None:
        raise ErrorDeLinea(f'{linea.componente} no está en el catálogo.', linea.ident)
    props = _props_con_posicional(linea, ficha)
    for prop, clave in ficha.hereda.items():
        props.setdefault(prop, {'$': clave})
    spec = catalogo.propios.get(ficha.componente) or {}
    esquema = spec.get('props') or {}
    for prop in esquema.get('required') or []:
        defecto = (esquema.get('properties') or {}).get(prop, {})
        if prop not in props and 'default' in defecto:
            props[prop] = copy.deepcopy(defecto['default'])     # sólo los defaults que declara el catálogo
    nodo: dict[str, Any] = {'id': linea.ident, 'component': ficha.componente}
    if props:
        nodo['props'] = props
    if linea.operador == '+>':
        raise ErrorDeLinea('+> sólo va en un cambio (~id); en un alta los hijos van con >.', linea.ident)
    if linea.hijos:
        if not ficha.hijos:
            raise ErrorDeLinea(f'{ficha.componente} no admite hijos.', linea.ident)
        nodo['children'] = list(linea.hijos)
    if linea.eventos:
        fuera = set(linea.eventos) - set(ficha.eventos)
        if fuera:
            raise ErrorDeLinea(f'{ficha.componente} no tiene el evento ' + ', '.join(sorted(fuera))
                               + (' (tiene ' + ', '.join(ficha.eventos) + ')' if ficha.eventos else '') + '.',
                               linea.ident)
        nodo['events'] = dict(linea.eventos)
    _validar(nodo, catalogo, linea.ident)
    return nodo


def _props_con_posicional(linea: Linea, ficha: Ficha) -> dict:
    props = dict(linea.props)
    if linea.hay_posicional:
        if ficha.posicional is None:
            raise ErrorDeLinea(f'{ficha.componente} no tiene texto posicional; nombra la prop.', linea.ident)
        if ficha.posicional in props:
            raise ErrorDeLinea(f'{ficha.posicional} va posicional o nombrada, no las dos.', linea.ident)
        props = {ficha.posicional: linea.posicional, **props}
    return props


def _validar(nodo: dict, catalogo: Catalogo, ident: str) -> None:
    errores = catalogo.errores_del_nodo(nodo)
    if errores:
        raise ErrorDeLinea('; '.join(errores[:3]), ident)


# -- alta completa en streaming --------------------------------------------------------------------------------
@dataclass
class Diagnostico:
    linea: int | None
    ident: str | None
    motivo: str

    def texto(self) -> str:
        donde = (f'línea {self.linea}' if self.linea else '') + (f' ({self.ident})' if self.ident else '')
        return (donde.strip() + ': ' if donde.strip() else '') + self.motivo


class Flujo:
    """Un alta completa que llega por trozos. `alimentar` devuelve las publicaciones nuevas (listas de nodos
    `pacai.ui/1` a mandar como `patch`); `cerrar` da el árbol final y lo que hay que pedirle al modelo."""

    def __init__(self, catalogo: Catalogo, tabla: dict[str, Ficha] | None = None):
        self.catalogo = catalogo
        self.tabla = tabla if tabla is not None else fichas(catalogo)
        self.pendiente = ''
        self.nodos: dict[str, dict] = {}            # válidos, en orden de llegada
        self.publicado: dict[str, list[str]] = {}   # id → hijos publicados
        self.diagnosticos: list[Diagnostico] = []
        self.n = 0
        self.cerrado = False
        self.reintentado = False

    def alimentar(self, trozo: str) -> list[list[dict]]:
        self.pendiente += trozo
        publicaciones = []
        while '\n' in self.pendiente:
            linea, self.pendiente = self.pendiente.split('\n', 1)
            publicacion = self._linea(linea)
            if publicacion:
                publicaciones.append(publicacion)
        return publicaciones

    def _linea(self, linea: str, solo_nuevos: bool = False) -> list[dict]:
        if not linea.strip():
            return []
        self.n += 1
        try:
            leida = leer_linea(linea)
            if leida.tipo != 'alta':
                raise ErrorDeLinea('En un alta completa sólo van líneas de nodo (los cambios llevan rev=N).',
                                   leida.ident)
            if leida.ident in self.nodos:
                raise ErrorDeLinea('Ese id ya está definido.', leida.ident)
            nodo = nodo_de(leida, self.catalogo, self.tabla)
        except ErrorDeLinea as exc:
            self.diagnosticos.append(Diagnostico(self.n, exc.ident, exc.motivo))
            return []
        self.nodos[nodo['id']] = nodo
        return self._publicar()

    def _visible(self) -> tuple[list[str], dict[str, list[str]]]:
        orden: list[str] = []
        hijos: dict[str, list[str]] = {}

        def recorrer(ident: str, profundidad: int) -> None:
            orden.append(ident)
            hijos[ident] = []
            for hijo in self.nodos[ident].get('children', []):
                if hijo not in self.nodos or hijo in hijos or profundidad >= self.catalogo.max_profundidad:
                    break
                hijos[ident].append(hijo)
                recorrer(hijo, profundidad + 1)

        if 'root' in self.nodos:
            recorrer('root', 0)
        return orden, hijos

    def _publicar(self) -> list[dict]:
        orden, hijos = self._visible()
        salida = []
        for ident in orden:
            if self.publicado.get(ident) != hijos[ident]:
                nodo = self.nodos[ident]
                salida.append(dict(nodo, children=list(hijos[ident])) if 'children' in nodo else nodo)
                self.publicado[ident] = list(hijos[ident])
        return salida

    def cerrar(self) -> tuple[list[dict], list[Diagnostico]]:
        """El árbol final (hijos que no llegaron, fuera) y los diagnósticos que quedan."""
        if self.pendiente.strip():
            self._linea(self.pendiente)
        self.pendiente = ''
        self.cerrado = True
        return self._final()

    def _final(self) -> tuple[list[dict], list[Diagnostico]]:
        diagnosticos = list(self.diagnosticos)
        nodos = dict(self.nodos)
        if self.catalogo.piezas:
            descartados = set()
            for ident, motivo in piezas.errores_del_arbol(nodos, self.catalogo.consultas):
                diagnosticos.append(Diagnostico(None, ident, motivo))
                descartados.add(ident)
            for ident in descartados:
                nodos.pop(ident, None)
        if 'root' not in nodos:
            diagnosticos.append(Diagnostico(None, 'root', 'Falta root.'))
            return [], diagnosticos
        orden: list[str] = []
        salida: dict[str, dict] = {}

        def recorrer(ident: str, profundidad: int) -> None:
            orden.append(ident)
            nodo = nodos[ident]
            presentes = []
            for hijo in nodo.get('children', []):
                if hijo in orden:
                    diagnosticos.append(Diagnostico(None, hijo, f'{hijo} cuelga de más de un sitio.'))
                elif hijo not in nodos:
                    if not any(d.ident == hijo for d in diagnosticos):
                        diagnosticos.append(Diagnostico(None, hijo, f'Falta la línea de {hijo} (hijo de {ident}).'))
                elif profundidad + 1 >= self.catalogo.max_profundidad:
                    diagnosticos.append(Diagnostico(None, hijo, 'Demasiada profundidad.'))
                else:
                    presentes.append(hijo)
                    recorrer(hijo, profundidad + 1)
            salida[ident] = dict(nodo, children=presentes) if 'children' in nodo else nodo

        recorrer('root', 0)
        for ident in nodos:
            if ident not in salida:
                diagnosticos.append(Diagnostico(None, ident, f'{ident} no cuelga de root.'))
        return [salida[i] for i in orden], diagnosticos

    def peticion(self, diagnosticos: list[Diagnostico]) -> str:
        """Lo que se le pide al modelo: sólo esas líneas, una vez."""
        return ('Rehaz sólo estas líneas de PACAI UI Lang, sin repetir las que ya valen:\n'
                + '\n'.join('- ' + d.texto() for d in diagnosticos))

    def reintentar(self, texto: str) -> tuple[list[dict], list[Diagnostico]]:
        """Las líneas que manda el modelo tras `peticion`. Una sola vez; no sustituye nodos que ya valían."""
        if self.reintentado:
            raise RuntimeError('Sólo se pide una vez.')
        self.reintentado = True
        self.diagnosticos = []
        for linea in texto.splitlines():
            self._linea(linea)
        return self._final()


def traducir(texto: str, catalogo: Catalogo) -> tuple[list[dict], list[Diagnostico]]:
    """Un alta completa de una vez: los nodos `pacai.ui/1` y los diagnósticos."""
    flujo = Flujo(catalogo)
    flujo.alimentar(texto if texto.endswith('\n') else texto + '\n')
    return flujo.cerrar()


# -- cambios atómicos ---------------------------------------------------------------------------------------------
@dataclass
class Cambio:
    nodos: dict[str, dict]                       # la pantalla resultante
    actualizados: list[dict]                     # los nodos que van en el `patch`
    borrados: list[str]
    avisos: list[str]


def _padres(nodos: dict[str, dict]) -> dict[str, list[str]]:
    padres: dict[str, list[str]] = {}
    for ident, nodo in nodos.items():
        for hijo in nodo.get('children', []):
            padres.setdefault(hijo, []).append(ident)
    return padres


def aplicar_cambio(nodos: dict[str, dict], texto: str, catalogo: Catalogo,
                   tabla: dict[str, Ficha] | None = None) -> tuple[Cambio | None, list[Diagnostico]]:
    """Un bloque de cambios contra la pantalla `nodos`. Entero o nada: con un solo diagnóstico, None y la pantalla
    se queda como estaba. La revisión la compara quien llama (`Vistas`)."""
    tabla = tabla if tabla is not None else fichas(catalogo)
    diagnosticos: list[Diagnostico] = []
    lineas: list[tuple[int, Linea]] = []
    for n, linea in enumerate((x for x in texto.splitlines() if x.strip()), 1):
        try:
            lineas.append((n, leer_linea(linea)))
        except ErrorDeLinea as exc:
            diagnosticos.append(Diagnostico(n, exc.ident, exc.motivo))
    nuevos = copy.deepcopy(nodos)
    padres = _padres(nodos)
    altas: dict[str, dict] = {}
    tocados: set[str] = set()
    borrados: list[str] = []
    avisos: list[str] = []
    vistos: set[str] = set()
    for n, linea in lineas:
        if linea.ident in vistos and linea.tipo != 'alta':
            diagnosticos.append(Diagnostico(n, linea.ident, 'Un mismo id sólo cambia una vez por bloque.'))
        vistos.add(linea.ident)
    # Bajas primero: `>` se valida sin depender del orden de las líneas.
    for n, linea in (x for x in lineas if x[1].tipo == 'baja'):
        if linea.ident not in nodos:
            diagnosticos.append(Diagnostico(n, linea.ident, f'{linea.ident} no está en la pantalla.'))
        elif linea.ident == 'root':
            diagnosticos.append(Diagnostico(n, 'root', 'root no se borra.'))
        elif len(padres.get(linea.ident, [])) > 1:
            diagnosticos.append(Diagnostico(n, linea.ident, f'{linea.ident} tiene más de un padre: '
                                            + ', '.join(padres[linea.ident]) + '.'))
        else:
            nuevos.pop(linea.ident, None)
            borrados.append(linea.ident)
            for padre in padres.get(linea.ident, []):
                if padre in nuevos:
                    nuevos[padre]['children'] = [h for h in nuevos[padre]['children'] if h != linea.ident]
                    tocados.add(padre)
    for n, linea in (x for x in lineas if x[1].tipo == 'alta'):
        if linea.ident in nodos or linea.ident in altas:
            diagnosticos.append(Diagnostico(n, linea.ident, f'{linea.ident} ya existe: cámbialo con ~{linea.ident}.'))
            continue
        try:
            altas[linea.ident] = nodo_de(linea, catalogo, tabla)
        except ErrorDeLinea as exc:
            diagnosticos.append(Diagnostico(n, exc.ident, exc.motivo))
    nuevos.update(copy.deepcopy(altas))
    for n, linea in (x for x in lineas if x[1].tipo == 'cambio'):
        nodo = nuevos.get(linea.ident)
        if nodo is None or linea.ident in altas:
            diagnosticos.append(Diagnostico(n, linea.ident, f'{linea.ident} no está en la pantalla.'))
            continue
        ficha = tabla.get(nodo['component'])
        try:
            if ficha is None:
                raise ErrorDeLinea(f'{nodo["component"]} no está en el catálogo.', linea.ident)
            props = _props_con_posicional(linea, ficha)
            if linea.eventos:
                fuera = set(linea.eventos) - set(ficha.eventos)
                if fuera:
                    raise ErrorDeLinea(f'{ficha.componente} no tiene el evento ' + ', '.join(sorted(fuera)) + '.',
                                       linea.ident)
                nodo['events'] = {**nodo.get('events', {}), **linea.eventos}
            if props:
                nodo['props'] = {**nodo.get('props', {}), **props}
            if linea.operador and not ficha.hijos:
                raise ErrorDeLinea(f'{ficha.componente} no admite hijos.', linea.ident)
            actuales = list(nodo.get('children', []))
            if linea.operador == '+>':
                for hijo in linea.hijos:
                    if hijo in actuales:
                        avisos.append(f'{hijo} ya era hijo de {linea.ident}.')
                    else:
                        actuales.append(hijo)
                nodo['children'] = actuales
            elif linea.operador == '>':
                faltan = [h for h in actuales if h not in linea.hijos]
                sobran = [h for h in linea.hijos if h not in actuales and h not in altas]
                if faltan:
                    raise ErrorDeLinea('> exige todos los hijos actuales; falta ' + ', '.join(faltan)
                                       + ' (para añadir usa +>, para quitar -id).', linea.ident)
                if sobran:
                    raise ErrorDeLinea('> nombra hijos que no existen: ' + ', '.join(sobran) + '.', linea.ident)
                nodo['children'] = list(linea.hijos)
            _validar(nodo, catalogo, linea.ident)
        except ErrorDeLinea as exc:
            diagnosticos.append(Diagnostico(n, exc.ident, exc.motivo))
            continue
        tocados.add(linea.ident)
    # Referencias, alcanzables y piezas, sobre la pantalla resultante.
    for ident in sorted(tocados | set(altas)):
        nodo = nuevos.get(ident)
        for hijo in (nodo or {}).get('children', []):
            if hijo not in nuevos:
                diagnosticos.append(Diagnostico(None, ident, f'{hijo} no existe (hijo de {ident}).'))
    alcanzables: set[str] = set()
    pila = ['root'] if 'root' in nuevos else []
    while pila:
        ident = pila.pop()
        if ident in alcanzables or ident not in nuevos:
            continue
        alcanzables.add(ident)
        pila.extend(nuevos[ident].get('children', []))
    for ident in list(nuevos):
        if ident in alcanzables:
            continue
        if ident in altas:
            diagnosticos.append(Diagnostico(None, ident, f'{ident} no cuelga de ningún nodo: añádelo con +>.'))
        elif ident in nodos and ident not in alcanzables and _alcanzable(nodos, ident):
            del nuevos[ident]                      # colgaba sólo de algo que se borró
            borrados.append(ident)
    if catalogo.piezas:
        for ident, motivo in piezas.errores_del_arbol(nuevos, catalogo.consultas):
            if ident in tocados or ident in altas:
                diagnosticos.append(Diagnostico(None, ident, motivo))
    if len(nuevos) > catalogo.max_nodos:
        diagnosticos.append(Diagnostico(None, None, f'La pantalla pasaría de {catalogo.max_nodos} nodos.'))
    if diagnosticos:
        return None, diagnosticos
    orden = [i for i in nuevos if i in tocados or i in altas]
    return Cambio(nuevos, [nuevos[i] for i in orden if i not in borrados], borrados, avisos), []


def _alcanzable(nodos: dict[str, dict], objetivo: str) -> bool:
    pila, vistos = ['root'], set()
    while pila:
        ident = pila.pop()
        if ident == objetivo:
            return True
        if ident in vistos or ident not in nodos:
            continue
        vistos.add(ident)
        pila.extend(nodos[ident].get('children', []))
    return False


# -- de pantalla a texto ------------------------------------------------------------------------------------------
def _escribir(valor: Any) -> str:
    if isinstance(valor, dict) and set(valor) == {'$'} and isinstance(valor['$'], str):
        return '$' + valor['$']
    if isinstance(valor, str) and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_-]*', valor) \
            and valor not in ('true', 'false', 'null'):
        return valor
    return json.dumps(valor, ensure_ascii=False, separators=(',', ':'))


def a_lang(nodos: list[dict], catalogo: Catalogo, tabla: dict[str, Ficha] | None = None) -> str:
    """Los nodos escritos en PACAI UI Lang (en el orden dado), quitando lo que el motor hereda. Para medir y para
    las pruebas de ida y vuelta: `traducir(a_lang(n)) == n`."""
    tabla = tabla if tabla is not None else fichas(catalogo)
    lineas = []
    for nodo in nodos:
        ficha = tabla[nodo['component']]
        props = dict(nodo.get('props') or {})
        for prop, clave in ficha.hereda.items():
            if props.get(prop) == {'$': clave}:
                del props[prop]
        partes = [nodo['id'], nodo['component']]
        if ficha.posicional and isinstance(props.get(ficha.posicional), str):
            partes.append(json.dumps(props.pop(ficha.posicional), ensure_ascii=False))
        partes += [k + '=' + _escribir(v) for k, v in props.items()]
        partes += ['@' + k + '=' + json.dumps(v, ensure_ascii=False, separators=(',', ':'))
                   for k, v in (nodo.get('events') or {}).items()]
        if nodo.get('children'):
            partes.append('> ' + ','.join(nodo['children']))
        lineas.append(' '.join(partes))
    return '\n'.join(lineas)


def resumen(nodos: dict[str, dict], rev: int, tabla: dict[str, Ficha]) -> str:
    """Lo que el modelo necesita para escribir un cambio sin que se le reenvíe la pantalla: la revisión y, por nodo,
    id, componente, posicional y la lista completa de hijos (nunca truncada). Sin queries ni el resto de props."""
    lineas = [f'rev={rev}']
    for ident, nodo in nodos.items():
        ficha = tabla.get(nodo['component'])
        partes = [ident, nodo['component']]
        pos = (nodo.get('props') or {}).get(ficha.posicional) if ficha and ficha.posicional else None
        if isinstance(pos, str):
            partes.append(json.dumps(pos, ensure_ascii=False))
        if nodo.get('children'):
            partes.append('> ' + ','.join(nodo['children']))
        lineas.append(' '.join(partes))
    return '\n'.join(lineas)


# -- prompt --------------------------------------------------------------------------------------------------------
def prompt(catalogo: Catalogo, tabla: dict[str, Ficha] | None = None, ancho: int = 150) -> str:
    """Las instrucciones del formato, generadas del catálogo: como mucho `MAX_LINEAS_PROMPT` líneas."""
    tabla = tabla if tabla is not None else fichas(catalogo)
    cabeza = [
        f'Pantalla: bloque ```{VALLA_LANG} view=nombre con una línea por nodo, id Comp ["pos"] clave=valor … '
        '> hijo,hijo. La raíz se llama root y va primero; el resto en DFS.',
        'Valores: "texto", números, true/false, enums sin comillas, $clave lee el estado; objetos y listas en JSON '
        'compacto; @evento={"send":"x","with":["k"]}.',
        'IDs literales [a-z][a-z0-9_]*, cada uno una vez; nada de prosa ni comentarios dentro del bloque.',
    ]
    herencia: dict[tuple[str, str], list[str]] = {}
    for ficha in tabla.values():
        for prop, clave in ficha.hereda.items():
            herencia.setdefault((prop, clave), []).append(ficha.componente)
    if herencia:
        props = sorted({p for p, _ in herencia})
        cabeza.append('Omite ' + ' y '.join(props) + ': el motor pone ' + ', '.join(
            f'{p}=${c}' for p, c in sorted(herencia)) + ' en ' + ', '.join(
            sorted({c for v in herencia.values() for c in v})) + '.')
    piezas_lineas = []
    for ficha in tabla.values():
        texto = ficha.componente + (f' "{ficha.posicional}"' if ficha.posicional else '') + (
            ' ' + ficha.resumen if ficha.resumen else '') + ''.join(
            ' @' + e + '={…}' for e in ficha.eventos) + (' > hijos' if ficha.hijos else '')
        piezas_lineas.append(texto)
    cola = [
        f'Cambio: bloque ```{VALLA_LANG} view=nombre rev=N, con la rev de la pantalla que ves (no la siguiente). '
        '~id ["pos"] clave=valor … cambia props y conserva hijos; altas con líneas normales.',
        '+> ids añade hijos al final; -id borra; > ids exige TODOS los hijos actuales (sólo para reordenar).',
        'Ejemplo: ~panel "Nuevo título" +> nota  /  nota Text "Hecho" style=caption. Si se te piden líneas, '
        'devuelve sólo esas.',
    ]
    # Los componentes se agrupan en líneas de `ancho` caracteres; si no caben en el máximo, las líneas se alargan.
    hueco = MAX_LINEAS_PROMPT - len(cabeza) - len(cola)
    while True:
        componentes: list[str] = []
        for texto in piezas_lineas:
            if componentes and len(componentes[-1]) + 2 + len(texto) <= ancho:
                componentes[-1] += '; ' + texto
            else:
                componentes.append(texto)
        if len(componentes) <= hueco:
            return '\n'.join(cabeza + componentes + cola)
        ancho += 50


def prompt_piezas(catalogo: Catalogo) -> str:
    """Las líneas de las piezas declarativas (sólo si el cliente las declara): 6 como mucho."""
    if not catalogo.piezas:
        return ''
    consultas = '; '.join(f'{t} da ' + '/'.join(sorted((c.get('out') or {}))) for t, c in catalogo.consultas.items())
    return '\n'.join([
        'Piezas: name crea estado y $name lo lee; Data publica slots tipados que sólo se leen en su subárbol.',
        'Calc preset=margen name=m compra=$c venta=$v cantidad=$n publica m.pct (fracción), m.u y m.total (dinero) '
        'y m.perdida.',
        'Text "Margen:" value=$m.pct escribe la etiqueta y el slot con su formato; rojo=$m.perdida lo pinta en rojo; '
        'nulo es «—».',
        'Alterna con si={"$":"vista","es":"valor"}: valor repite una option; Select sin required empieza en la '
        'primera.',
        'Valida con required, kind, min, max y step (fecha min=$q.hoy); Form bloquea su @submit hasta que todo '
        'vale.',
        'Data name=q query={"tipo":…} > hijos espera sus argumentos y gana la última respuesta' + (
            ': ' + consultas + '.' if consultas else '.'),
    ])
