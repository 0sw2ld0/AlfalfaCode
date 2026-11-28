"""PACAI UI Lang (3.ª lluvia de ideas): una forma compacta, sólo datos, de escribir nodos `pacai.ui/1`.

Encendido con todos los modelos: es el formato en que el modelo escribe las pantallas. `MODELOS_MEDIDOS` sólo
dice con cuáles se midió y cumple el criterio de adopción (perfil-ui.md §10). `ALFALFA_PACAI_UI_LANG=0` lo apaga
con todos y deja el JSON con ejemplos; `ALFALFA_PACAI_UI_LANG=1` enciende además las piezas declarativas si el
cliente las declara. Los clientes no cambian: el motor lo traduce exacto a `pacai.ui/1`.

Una línea por nodo, en DFS desde `root`:

    id Comp ["posicional"] clave=valor … > hijo,hijo

- El posicional es una sola cadena JSON justo después del componente y es la prop que declara el catálogo
  (`Text.text`, `Panel.title`…). Nombrarla además, o ponerlo en un componente que no lo declara, es un error.
- Valores: JSON (`"texto"`, números, `true`, objetos, listas: `query` va como JSON compacto), una palabra suelta es
  un enum (`direction=vertical`) y `$clave` lee el estado (`{"$": "clave"}`). `@evento=` lleva las acciones.
- Herencia: el catálogo declara `inherit` (`{"period": "periodo"}`) y el motor pone `{"$": "periodo"}` si la prop
  falta. Un valor explícito prevalece.

Cambios (versión 3: el mismo bloque `view=…`; si la vista ya existe es un cambio contra la última revisión que el
motor le enseñó al modelo, y el modelo nunca escribe `rev`), aplicados enteros o nada. Sin
operadores, como el JSON de hoy: cada línea es el nodo entero.

    id Comp …                    alta si el id es nuevo; si ya existe, lo sustituye (con todos sus hijos tras >)
    -id                          borra el nodo, lo quita de su padre y se lleva lo que sólo colgaba de él

Un hijo que la línea del padre deja fuera también se va. Una línea idéntica a lo que ya hay se ignora, y en el
reintento de un alta la línea de un id que ya existe lo sustituye. En un alta sin `root`, la primera línea, si es un
contenedor que no cuelga de nadie, es la raíz con otro nombre (`raiz`, `fila`) y pasa a llamarse `root`; si no, un
Stack vertical nuevo envuelve los nodos de primer nivel. Son reparaciones que nunca cambian lo pedido.

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
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from . import piezas
from .ui import BASE, CON_HIJOS, EVENTOS, Catalogo

__all__ = ['FLAG', 'VALLA_LANG', 'MODELOS_MEDIDOS', 'activo', 'forzado', 'medido', 'para_modelo', 'Ficha', 'fichas',
           'ErrorDeLinea', 'Linea', 'leer_linea', 'nodo_de',
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
    'Select': 'name=… options=[{"value":…,"label":…}] multiple=true required=true',
    'Chart': 'kind=bar|line|pie labels=[…] series=[{"name":…,"values":[…]}]',
    'Image': 'src="https://…" alt="…"',
    'Code': 'language=…',
    'Calc': 'name=m preset=margen compra=$… venta=$… cantidad=$…',
    'Data': 'name=q query={"tipo":…}',
}

_ID = re.compile(r'[a-z][a-z0-9_]*')
# Un id como lo escriben a veces los modelos: con tildes, eñes o mayúsculas después de la primera letra
# (`previsión_panel`, `serviceTable`). Se lee como su forma ASCII en minúsculas si el resultado es un id válido; dos ids
# distintos que dan la misma forma son un error (lo comprueba `Alias`), así que el árbol nunca cambia.
_ID_ANCHO = re.compile(r'[^\W\d_A-Z][\w]*')
_COMP = re.compile(r'[A-Z][A-Za-z0-9]*')
_CLAVE = re.compile(r'(@?)([A-Za-z_][A-Za-z0-9_]*)=')
_ENLACE = re.compile(r'\$([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?)')
_PALABRA = re.compile(r'[^\s"{}\[\]>,=]+')
_JSON = json.JSONDecoder()


# Los modelos con los que PACAI UI Lang cumple el criterio de adopción (perfil-ui.md §10: ≥95 % de pantallas
# válidas a la primera, límite inferior de Wilson ≥90 % y al menos un 20 % menos de coste por pantalla exacta que el
# JSON con ejemplos). Es informativo: el formato va encendido con todos (`para_modelo`).
MODELOS_MEDIDOS = frozenset({'gpt-6-luna', 'claude-haiku-5-5'})
_SI = ('1', 'true', 'si', 'sí')
_NO = ('0', 'false', 'no')
_INSTANTANEA = re.compile(r'-(?:\d{4}-\d{2}-\d{2}|\d{8})$')


def forzado(entorno: dict | None = None) -> bool | None:
    """Lo que dice `ALFALFA_PACAI_UI_LANG`: True (encendido, con las piezas declarativas), False (apagado con todos:
    el JSON con ejemplos) o None (sin la variable, o con otro valor: encendido sin piezas)."""
    valor = (os.environ if entorno is None else entorno).get(FLAG, '').strip().lower()
    return True if valor in _SI else False if valor in _NO else None


def activo(entorno: dict | None = None) -> bool:
    """Si el flag fuerza el formato con cualquier modelo (y con él, las piezas declarativas)."""
    return forzado(entorno) is True


def medido(modelo: str | None) -> bool:
    """Si `modelo` es uno de `MODELOS_MEDIDOS`, con o sin el prefijo del proveedor (`chatgpt/gpt-6-luna`,
    `openrouter/anthropic/claude-haiku-5.5`) o una instantánea con fecha."""
    nombre = str(modelo or '').strip().lower().rsplit('/', 1)[-1].split(':', 1)[0].replace('.', '-')
    return _INSTANTANEA.sub('', nombre) in MODELOS_MEDIDOS


def para_modelo(modelo: str | None, entorno: dict | None = None) -> bool:
    """Si el modelo de la sesión escribe las pantallas en PACAI UI Lang: sí con cualquier modelo, salvo que
    `ALFALFA_PACAI_UI_LANG=0` lo apague. `modelo` se mantiene por si un modelo concreto necesitara el JSON."""
    return forzado(entorno) is not False


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
    escritos: dict[str, str] = field(default_factory=dict)    # id normalizado → como lo escribió el modelo


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


def normalizar_id(texto: str) -> str | None:
    """`previsión_panel` → `prevision_panel`, `serviceTable` → `servicetable`; None si no queda un id válido."""
    if _ID.fullmatch(texto):
        return texto
    if not _ID_ANCHO.fullmatch(texto):
        return None
    sin_tildes = ''.join(c for c in unicodedata.normalize('NFD', texto) if not unicodedata.combining(c)).lower()
    return sin_tildes if _ID.fullmatch(sin_tildes) else None


def _id(texto: str, salida: Linea) -> str | None:
    ident = normalizar_id(texto)
    if ident is not None and ident != texto:
        salida.escritos[ident] = texto
    return ident


class Alias:
    """Los ids que se normalizaron en un bloque: dos formas distintas que dan el mismo id no se juntan nunca (sería
    adivinar que son el mismo nodo). Los ids de la pantalla que ya existe cuentan como escritos tal cual."""

    def __init__(self, existentes=()):
        self.formas: dict[str, str] = {i: i for i in existentes}

    def comprobar(self, linea: Linea) -> list[str]:
        """Las reparaciones que deja la línea. `ErrorDeLinea` si choca con otra forma del mismo id."""
        notas = []
        for ident in [linea.ident, *linea.hijos]:
            forma = linea.escritos.get(ident, ident)
            antes = self.formas.setdefault(ident, forma)
            if antes != forma:
                raise ErrorDeLinea(f'«{antes}» y «{forma}» son ids distintos que sólo cambian en tildes o mayúsculas; '
                                   'los ids van en a-z, 0-9 y _.', linea.ident)
            if ident in linea.escritos and f'{forma} → {ident}' not in notas:
                notas.append(f'{forma} → {ident}')
        return notas


def leer_linea(linea: str) -> Linea:
    """Una línea de PACAI UI Lang, sin mirar el catálogo. `ErrorDeLinea` si no sigue la gramática."""
    texto = linea.strip()
    if texto.startswith('-'):
        crudo = texto[1:].strip()                  # `- fuente` también: el espacio no cambia lo que pide
        salida = Linea('baja', crudo)
        ident = _id(crudo, salida)
        if ident is None:
            raise ErrorDeLinea('Una baja es -id, sin nada más.')
        salida.ident = ident
        return salida
    if texto.startswith('~'):
        m = _ID.match(texto, 1)
        if not m:
            raise ErrorDeLinea('Un cambio empieza por ~id.')
        salida, i = Linea('cambio', m.group(0)), m.end()
    else:
        m = _ID_ANCHO.match(texto)
        salida = Linea('alta', '')
        ident = _id(m.group(0), salida) if m else None
        if ident is None or m.end() < len(texto) and not texto[m.end()].isspace():
            raise ErrorDeLinea('Una línea empieza por un id [a-z][a-z0-9_]*.')
        salida.ident = ident
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
                if not resto and salida.operador == '>':
                    salida.operador = None             # `>` sin nada detrás: sin hijos, como si no estuviera
                    break
                hijos = [_id(h.strip(), salida) for h in resto.split(',')] if resto else []
                if not hijos or any(h is None for h in hijos):
                    raise ErrorDeLinea('Los hijos van como id,id tras ' + salida.operador + ', al final de la línea.')
                if len(set(hijos)) != len(hijos):
                    raise ErrorDeLinea('Un hijo repetido en la lista.')
                salida.hijos = hijos
                break
            if texto[i] == '"' or (texto[i] == '$' and primero and not _CLAVE.match(texto, i)):
                if not primero:
                    raise ErrorDeLinea('Un texto suelto sólo va justo después del componente.')
                salida.posicional, i = _valor(texto, i)      # un texto o un enlace $clave: un valor por posición
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


def nodo_de(linea: Linea, catalogo: Catalogo, tabla: dict[str, Ficha], ficha: Ficha | None = None,
            reparaciones: list[str] | None = None) -> dict:
    """El nodo `pacai.ui/1` de una línea de alta, ya validado contra el catálogo. `ErrorDeLinea` si no vale.
    Con `reparaciones` (sólo en un alta) el texto posicional y la misma prop nombrada no son un error: se queda la
    nombrada y se anota ahí."""
    ficha = ficha or tabla.get(linea.componente or '')
    if ficha is None:
        raise ErrorDeLinea(f'{linea.componente} no está en el catálogo.', linea.ident)
    props = _props_con_posicional(linea, ficha, reparaciones)
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


def _props_con_posicional(linea: Linea, ficha: Ficha, reparaciones: list[str] | None = None) -> dict:
    props = dict(linea.props)
    if linea.hay_posicional:
        if ficha.posicional is None:
            raise ErrorDeLinea(f'{ficha.componente} no tiene texto posicional; nombra la prop.', linea.ident)
        if ficha.posicional in props:
            if reparaciones is None:
                raise ErrorDeLinea(f'{ficha.posicional} va posicional o nombrada, no las dos.', linea.ident)
            reparaciones.append(f'{linea.ident}: {ficha.posicional} iba posicional y nombrada; se queda la nombrada.')
            return props
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
        self.publicado: dict[str, dict] = {}        # id → el nodo tal como se publicó (con sus hijos visibles)
        self.diagnosticos: list[Diagnostico] = []
        self.n = 0
        self.cerrado = False
        self.reintentado = False
        self.reparaciones: list[str] = []          # lo que el motor arregló sin cambiar lo pedido
        self.root_rechazado = False                # una línea de root (aunque sea ilegible) no valió
        self.hijos_rechazados: set[str] = set()    # hijos de una línea de alta que no valió
        self.primero: str | None = None            # el id de la primera línea si valió; '' si no valió
        self.provisional: dict[str, dict] = {}     # el nodo de la línea que aún no llegó entera, si ya se lee
        self.alias = Alias()                       # ids escritos con tildes o mayúsculas, y cómo

    def alimentar(self, trozo: str) -> list[list[dict]]:
        """Las publicaciones que deja este trozo. Una línea entera se valida y se queda; la que todavía llega se lee
        cerrando su texto y sus corchetes y, si ya vale y su id no existe,
        se publica de forma provisional: cuando llegue entera la sustituye, o desaparece si no vale."""
        self.pendiente += trozo
        publicaciones = []
        while '\n' in self.pendiente:
            linea, self.pendiente = self.pendiente.split('\n', 1)
            self.provisional = {}
            publicacion = self._linea(linea)
            if publicacion:
                publicaciones.append(publicacion)
        self.provisional = self._leer_pendiente()
        publicacion = self._publicar()
        if publicacion:
            publicaciones.append(publicacion)
        return publicaciones

    def _leer_pendiente(self) -> dict[str, dict]:
        texto = self.pendiente.strip()
        if self.cerrado or not texto:
            return {}
        try:
            leida = leer_linea(cerrar_cola(texto))
            if leida.tipo != 'alta' or leida.ident in self.nodos:
                return {}                          # nunca pisa una línea entera
            return {leida.ident: nodo_de(leida, self.catalogo, self.tabla, reparaciones=[])}
        except ErrorDeLinea:
            # A medias de una palabra (`style=capt`) no se lee: se queda la última lectura buena de esta línea, para
            # que la pantalla no retroceda. Se va si la línea entera no vale.
            return {i: n for i, n in self.provisional.items() if re.match(re.escape(i) + r'(\s|$)', texto)}

    def _linea(self, linea: str, solo_nuevos: bool = False) -> list[dict]:
        if not linea.strip():
            return []
        self.n += 1
        leida = None
        try:
            leida = leer_linea(linea)
            if leida.tipo != 'alta':
                raise ErrorDeLinea('En una pantalla nueva sólo van líneas de nodo (para borrar, la vista tiene que '
                                   'existir).',
                                   leida.ident)
            for nota in self.alias.comprobar(leida):
                if f'{nota}: los ids van en a-z, 0-9 y _.' not in self.reparaciones:
                    self.reparaciones.append(f'{nota}: los ids van en a-z, 0-9 y _.')
            nodo = nodo_de(leida, self.catalogo, self.tabla, reparaciones=self.reparaciones)
            if leida.ident in self.nodos:
                if self.nodos[leida.ident] == nodo:
                    return []                      # la misma línea otra vez: no cambia nada
                # La última línea de un id gana (también en el reintento, donde el modelo reenvía el bloque).
                if not self.reintentado:
                    self.reparaciones.append(f'{leida.ident} va dos veces: vale la línea {self.n}.')
                del self.nodos[leida.ident]        # vuelve a entrar al final, en el orden de llegada
        except ErrorDeLinea as exc:
            self.diagnosticos.append(Diagnostico(self.n, exc.ident, exc.motivo))
            if self.primero is None:
                self.primero = ''
            if exc.ident == 'root' or re.match(r'root(\s|$)', linea.strip()):
                self.root_rechazado = True
            if leida is not None and leida.tipo == 'alta' and leida.ident not in self.nodos:
                self.hijos_rechazados.update(leida.hijos)
            return []
        self.nodos[nodo['id']] = nodo
        if self.primero is None:
            self.primero = nodo['id']
        return self._publicar()

    def _visible(self) -> tuple[list[str], dict[str, list[str]], dict[str, dict]]:
        orden: list[str] = []
        hijos: dict[str, list[str]] = {}
        nodos = {**self.provisional, **self.nodos}

        def recorrer(ident: str, profundidad: int) -> None:
            orden.append(ident)
            hijos[ident] = []
            for hijo in nodos[ident].get('children', []):
                if hijo not in nodos or hijo in hijos or profundidad >= self.catalogo.max_profundidad:
                    break
                hijos[ident].append(hijo)
                recorrer(hijo, profundidad + 1)

        if 'root' in nodos:
            recorrer('root', 0)
        return orden, hijos, nodos

    def _publicar(self) -> list[dict]:
        """Los nodos visibles que cambiaron desde la última publicación (sus hijos o su contenido)."""
        orden, hijos, nodos = self._visible()
        salida = []
        for ident in orden:
            nodo = nodos[ident]
            nodo = dict(nodo, children=list(hijos[ident])) if 'children' in nodo else nodo
            if self.publicado.get(ident) != nodo:
                salida.append(nodo)
                self.publicado[ident] = nodo
        return salida

    def cerrar(self) -> tuple[list[dict], list[Diagnostico]]:
        """El árbol final (hijos que no llegaron, fuera) y los diagnósticos que quedan."""
        self.provisional = {}
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
            intento_de_root = ('root' in self.nodos or self.root_rechazado
                               or any(d.ident == 'root' for d in diagnosticos))
            motivo = 'Falta root.' if intento_de_root else self._reparar_root(nodos)
            if motivo:
                diagnosticos.append(Diagnostico(None, 'root', motivo))
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

    def _reparar_root(self, nodos: dict[str, dict]) -> str:
        """Un alta sin `root`: si la primera línea es un contenedor que no cuelga de nadie, es la raíz con otro nombre
        («raiz», «fila») y pasa a llamarse `root`; si no, un Stack vertical nuevo envuelve los nodos de primer nivel.
        Devuelve '' si lo hizo, o el diagnóstico si no se puede."""
        viejo = root_renombrado(nodos, self.catalogo, self.primero or '', self.hijos_rechazados)
        if viejo is None:
            return self._envolver_en_root(nodos)
        nodos['root'] = dict(nodos.pop(viejo), id='root')
        aviso = f'Faltaba root: {viejo} va primero, no cuelga de nadie y es un contenedor; pasa a llamarse root.'
        if aviso not in self.reparaciones:
            self.reparaciones.append(aviso)
        return ''

    def _envolver_en_root(self, nodos: dict[str, dict]) -> str:
        """Un alta sin `root` (como en OpenUI Lang, donde la primera sentencia es la raíz): un `root` Stack vertical
        nuevo envuelve los nodos que no son hijos de ningún otro, en el orden en que llegaron. Ningún id cambia, así
        los cambios posteriores siguen valiendo. Devuelve '' si lo hizo, o el diagnóstico si no se puede. Si el
        modelo sí escribió un `root` y se rechazó, no se llama: lo que quería es otra cosa y se le pide."""
        root, motivo = root_que_envuelve(nodos, self.catalogo, self.hijos_rechazados)
        if root is None:
            return motivo
        nodos['root'] = root
        aviso = 'Faltaba root: un Stack vertical envuelve ' + ', '.join(root['children']) + '.'
        if aviso not in self.reparaciones:
            self.reparaciones.append(aviso)
        return ''

    def peticion(self, diagnosticos: list[Diagnostico]) -> str:
        """Lo que se le pide al modelo: sólo esas líneas, una vez."""
        return ('Rehaz sólo estas líneas de PACAI UI Lang, sin repetir las que ya valen:\n'
                + '\n'.join('- ' + d.texto() for d in diagnosticos))

    def reintentar(self, texto: str) -> tuple[list[dict], list[Diagnostico]]:
        """Las líneas que manda el modelo tras `peticion`, una sola vez. Una línea idéntica a una que ya valía se
        ignora y la de un id que ya existe lo sustituye: el modelo suele reenviar el bloque entero."""
        if self.reintentado:
            raise RuntimeError('Sólo se pide una vez.')
        self.reintentado = True
        self.alias = Alias()                       # el reintento reenvía el bloque entero
        self.diagnosticos = []
        for linea in texto.splitlines():
            self._linea(linea)
        return self._final()


def cerrar_cola(texto: str) -> str:
    """Cierre implícito de una línea a medias, para leerla mientras llega: cierra el texto
    entre comillas que siga abierto y después los corchetes y llaves, del último al primero."""
    pila: list[str] = []
    comillas, i = False, 0
    while i < len(texto):
        c = texto[i]
        if comillas:
            if c == '\\':
                i += 1
            elif c == '"':
                comillas = False
        elif c == '"':
            comillas = True
        elif c in '[{':
            pila.append(']' if c == '[' else '}')
        elif c in ']}' and pila and pila[-1] == c:
            pila.pop()
        i += 1
    if comillas:
        texto += ('\\' if texto.endswith('\\') and not texto.endswith('\\\\') else '') + '"'
    return texto + ''.join(reversed(pila))


def root_renombrado(nodos: dict[str, dict], catalogo: Catalogo, primero: str,
                    excluir: set[str] | frozenset[str] = frozenset()) -> str | None:
    """El id de la raíz con otro nombre en un alta sin `root`, o None. Lo es `primero` (el id de la primera línea, que
    tiene que haber valido) si es un contenedor (Stack, Card, Form o un componente propio con hijos) que no cuelga de
    nadie ni es hijo de una línea rechazada: el prompt dice que la raíz va primero, y Haiku la llama a veces «raiz» o
    «fila». Un nodo que no es contenedor no se renombra: lo envuelve un Stack (`root_que_envuelve`)."""
    if not primero or primero == 'root' or primero not in nodos or primero in excluir:
        return None
    if any(primero in n.get('children', []) for n in nodos.values()):
        return None
    componente = nodos[primero].get('component')
    contenedor = componente in CON_HIJOS or bool((catalogo.propios.get(componente) or {}).get('children'))
    return primero if contenedor else None


def root_que_envuelve(nodos: dict[str, dict], catalogo: Catalogo,
                      excluir: set[str] | frozenset[str] = frozenset()) -> tuple[dict | None, str]:
    """El `root` Stack vertical que envuelve los nodos de primer nivel de `nodos` (los que no son hijos de ningún
    otro), en su orden, y '' — o None y el diagnóstico para el modelo. `excluir`: los hijos de una línea rechazada
    (los campos de un Form que no valió), que no suben a la raíz: se quedan sin colgar, como con un root explícito."""
    hijos = {h for nodo in nodos.values() for h in nodo.get('children', [])}
    primeros = [i for i in nodos if i not in hijos and i not in excluir and i != 'root']
    if not primeros:
        return None, 'Falta root.'
    if len(nodos) >= catalogo.max_nodos:
        return None, (f'Falta root, y no cabe uno nuevo: la pantalla ya tiene {catalogo.max_nodos} nodos (el '
                      'máximo).')
    root = {'id': 'root', 'component': 'Stack', 'props': {'direction': 'vertical'}, 'children': primeros}
    return (None, 'Falta root.') if catalogo.errores_del_nodo(root) else (root, '')


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
    """Un bloque de cambios. Una línea con sólo un id (una baja a la que el modelo le quitó el guion) se tolera sólo si
    da igual leerla como nada o como `-id`: las dos lecturas tienen que dar la misma pantalla. Si no, o si hay más de
    una, o si ese id no estaba en la pantalla, el bloque se rechaza como siempre. El motor no adivina qué quería."""
    lineas = texto.splitlines()
    sueltos = [i for i, x in enumerate(lineas) if _ID.fullmatch(x.strip())]
    if len(sueltos) == 1 and lineas[sueltos[0]].strip() in nodos:
        i = sueltos[0]
        ident = lineas[i].strip()
        sin = _aplicar_cambio('\n'.join(lineas[:i] + lineas[i + 1:]), nodos, catalogo, tabla)
        como_baja = _aplicar_cambio('\n'.join(lineas[:i] + ['-' + ident] + lineas[i + 1:]), nodos, catalogo, tabla)
        if sin[0] is not None and como_baja[0] is not None and sin[0].nodos == como_baja[0].nodos:
            sin[0].avisos.insert(0, f'{ident}: una línea con sólo el id; da lo mismo ignorarla que borrarlo, y se '
                                    'ignora.')
            return sin
    return _aplicar_cambio(texto, nodos, catalogo, tabla)


def _aplicar_cambio(texto: str, nodos: dict[str, dict], catalogo: Catalogo,
                    tabla: dict[str, Ficha] | None = None) -> tuple[Cambio | None, list[Diagnostico]]:
    """Un bloque de cambios contra la pantalla `nodos` (versión 3, sin operadores). Entero o nada: con un solo
    diagnóstico, None y la pantalla se queda como estaba. La revisión la compara quien llama (`Vistas`).

    - Cada línea es el nodo entero: un id nuevo es un alta y uno que ya existe se sustituye (con su herencia).
    - `-id` borra el nodo y lo quita de los hijos de sus padres; lo que sólo colgaba de él también se va.
    - Un hijo que el padre reescrito deja fuera de su lista deja de dibujarse y se borra.
    - **La última línea de un id gana**: dos líneas distintas del mismo id se
      quedan con la segunda, y una baja y una línea del mismo id, con la que va después (borrar y reescribir un nodo
      lo reescribe; escribirlo y borrarlo lo borra). Cada vez queda anotado en `Cambio.avisos`.
    - Borrar un id que no está en la pantalla no hace nada: va a los avisos."""
    tabla = tabla if tabla is not None else fichas(catalogo)
    diagnosticos: list[Diagnostico] = []
    avisos: list[str] = []
    ultimo: dict[str, tuple[int, dict | None]] = {}     # id → (línea, nodo; None si la última fue una baja)
    alias = Alias(nodos)
    for n, texto_linea in enumerate((x for x in texto.splitlines() if x.strip()), 1):
        try:
            linea = leer_linea(texto_linea)
            for nota in alias.comprobar(linea):
                if f'{nota}: los ids van en a-z, 0-9 y _.' not in avisos:
                    avisos.append(f'{nota}: los ids van en a-z, 0-9 y _.')
            if linea.tipo == 'cambio':
                raise ErrorDeLinea('Un cambio es la línea entera del nodo («id Comp …», con todos sus hijos tras >), '
                                   'sin ~ ni +>.', linea.ident)
            nodo = None if linea.tipo == 'baja' else nodo_de(linea, catalogo, tabla)
        except ErrorDeLinea as exc:
            diagnosticos.append(Diagnostico(n, exc.ident, exc.motivo))
            continue
        if linea.ident in ultimo and ultimo[linea.ident][1] != nodo:
            antes = ultimo[linea.ident][1]
            avisos.append(f'{linea.ident}: ' + ('se borra y se vuelve a escribir; vale la línea {n}.' if antes is None
                                                 else 'se escribe y se borra; vale la baja de la línea {n}.'
                                                 if nodo is None else 'va dos veces; vale la línea {n}.')
                          .format(n=n))
        ultimo.pop(linea.ident, None)
        ultimo[linea.ident] = (n, nodo)
    escritos = {i: nodo for i, (_, nodo) in ultimo.items() if nodo is not None}
    bajas = [(n, i) for i, (n, nodo) in ultimo.items() if nodo is None]
    nuevos = copy.deepcopy(nodos)
    tocados: set[str] = set()
    borrados: list[str] = []
    for ident, nodo in escritos.items():
        if nodos.get(ident) != nodo:
            nuevos[ident] = copy.deepcopy(nodo)
            tocados.add(ident)
    for n, ident in bajas:
        if ident == 'root':
            diagnosticos.append(Diagnostico(n, 'root', 'root no se borra.'))
        elif ident not in nodos:
            # Borrar lo que no existe no hace nada (queda en los avisos).
            if ident not in escritos:
                avisos.append(f'{ident}: no está en la pantalla; la baja de la línea {n} no hace nada.')
        else:
            nuevos.pop(ident, None)
            borrados.append(ident)
            for padre, nodo in nuevos.items():
                if ident in nodo.get('children', []):
                    nodo['children'] = [h for h in nodo['children'] if h != ident]
                    tocados.add(padre)
    for ident in sorted(tocados):
        for hijo in nuevos.get(ident, {}).get('children', []):
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
        if ident in escritos and ident not in nodos:
            diagnosticos.append(Diagnostico(None, ident, f'{ident} no cuelga de ningún nodo: ponlo en los hijos de '
                                            'su padre (reescribe la línea del padre).'))
        else:
            del nuevos[ident]                      # dejó de colgar de la pantalla
            borrados.append(ident)
    if catalogo.piezas:
        for ident, motivo in piezas.errores_del_arbol(nuevos, catalogo.consultas):
            if ident in tocados:
                diagnosticos.append(Diagnostico(None, ident, motivo))
    if len(nuevos) > catalogo.max_nodos:
        diagnosticos.append(Diagnostico(None, None, f'La pantalla pasaría de {catalogo.max_nodos} nodos.'))
    if diagnosticos:
        return None, diagnosticos
    actualizados = [nuevos[i] for i in nuevos if i in tocados and i not in borrados]
    return Cambio(nuevos, actualizados, [i for i in borrados if i in nodos], avisos), []


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


def a_lang(nodos: list[dict], catalogo: Catalogo | None, tabla: dict[str, Ficha] | None = None) -> str:
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


def resumen(nodos: dict[str, dict], tabla: dict[str, Ficha]) -> str:
    """Lo que el modelo necesita para escribir un cambio: la línea entera de cada nodo, en PACAI UI Lang (sin lo que
    el motor hereda). En la versión 3 no lleva revisión: la pone el motor."""
    return a_lang(list(nodos.values()), None, tabla)


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
        f'Cambio de una pantalla que ya existe: el mismo bloque ```{VALLA_LANG} view=nombre (sin rev: la pone el '
        'motor). Escribe entera la línea de cada nodo que cambia y las de los nuevos. Cada id nuevo va en el > de su '
        'padre, en la posición pedida: reescribe la línea del padre con sus demás hijos, salvo los que se borran.',
        'Para borrar un nodo escribe su id con un guion delante, sola en su línea: -c_bajaron. Un hijo que dejas '
        'fuera de la lista de su padre también se va.',
        'Ejemplo: panel Card "Nuevo título" > nota,extra  /  extra Text "Hecho" style=caption  /  -vieja. Si se te '
        'piden líneas, devuelve sólo esas.',
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
