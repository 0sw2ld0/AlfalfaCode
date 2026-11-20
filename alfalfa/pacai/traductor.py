"""De los eventos de `--stdio` v2 a mensajes PACAI 0.3 (anexo A de la especificación).

`Traductor` no hace entrada y salida ni espera nada: recibe un evento v2 (`tipo` y campos) y devuelve la lista
de mensajes PACAI que le corresponden, ya con su `seq`, su `id` y su hora. Lleva el estado que PACAI exige y v2
no tiene: ejecuciones (raíz y subejecuciones), herramientas abiertas, aprobaciones pendientes y su digest.

Lo que el núcleo no cubre va como extensión `alfalfa.*` (§8), con los campos de v2 en inglés (`nombres.py`).
"""
from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from .jcs import digest
from .nombres import TIPOS, en_ingles, peticion_del_navegador, razon
from .sobre import MAX_BYTES, Emisor, ahora, bytes_de_linea, codificar, en_segundos, prefijo_que_cabe

__all__ = ['Traductor', 'archivos_del_diff', 'CADUCIDAD_APROBACION', 'NAVEGADOR']

# Segundos que una aprobación espera respuesta antes de darse por caducada (§4.5). Por defecto ninguno: en la 0.3
# `expires` es opcional y, como en Claude Code, ACP y Codex, una petición espera hasta que el usuario conteste o el
# agente la cancele. `ALFALFA_CADUCIDAD_APROBACION` o `--caducidad-aprobacion` le ponen plazo.
CADUCIDAD_APROBACION: float | None = None
# La herramienta del cliente que hace de navegador propio de la app (`--navegador`): su `hello` la declara.
NAVEGADOR = 'browser'
TROZO = 4000                  # máximo de caracteres antes de ajustar al límite de bytes
MAX_ARGUMENTOS = 256 * 1024   # bytes de los `arguments` de un `tool_start` (el transporte lo ajusta al negociado)
RECORTADO = '… [recortado]'
# Cómo se tacha un secreto en los argumentos de una herramienta del agente (§4.4): siempre igual, para que el digest
# de lo que viaja cuadre. AlfalfaCode lo redacta como «[REDACTADO]» (v2, registros); en PACAI sale así.
TACHADO = '[redacted]'
REDACTADO = '[REDACTADO]'
MAX_SALIDA = 60000            # caracteres de la salida de una herramienta en un `tool_end`
EDITORES = frozenset({'write_file', 'edit_file', 'apply_patch'})
SINTETICA = 'request_permission'
# Herramientas del motor que lanzan (o relanzan) un subagente: su `tool_start` va en el `run_started` (§4.9).
LANZADORES = frozenset({'spawn_agent', 'send_input'})
# Estados de un subagente (subagentes.py) con los que ya no trabaja.
TERMINADOS = frozenset({'terminado', 'fallido', 'cerrado', 'interrumpido'})
RESULTADO_DE_TURNO = {'completed': 'success', 'blocked': 'blocked', 'failed': 'failed', 'cortado': 'interrupted',
                      'interrupted': 'interrupted'}
# Eventos v2 de AlfalfaCode sin equivalente en el núcleo → tipo de la extensión: `nombres.TIPOS`.
# `motivo` de un permiso v2 → `reason` de PACAI (§4.5). Los demás van como `alfalfa.<motivo>` si se negoció.
RAZON_DE = {'peligroso': 'dangerous', 'red': 'network', 'salir_sandbox': 'outside_sandbox',
            'reintento': 'sandbox_denied_retry', 'regla': 'policy', 'modo': 'policy'}
# Campos de un permiso v2 que PACAI no tiene: viajan en `payload.alfalfa` (en inglés) si se negoció la extensión.
# `motivo` es el principal: `reason` junta varios (`regla` y `modo` son `policy`). `para_sesion` dice si v2 ofrecía
# «en esta sesión» (un permiso de sitio también ofrece `session`).
PERMISO_EXTRA = ('argv', 'proposito', 'motivo', 'motivos', 'justificacion', 'regla', 'intento', 'reintento_de', 'red',
                 'sin_sandbox', 'fallo_sandbox', 'mcp', 'app', 'cwd', 'timeout_seconds', 'diff', 'sitio', 'prefijo',
                 'prefijo_alcance', 'para_sesion')
CODIGOS_DE_ERROR = {'Línea demasiado larga.': 'message_too_large', 'Se requiere UTF-8.': 'invalid_message'}


@dataclass
class Herramienta:
    id: str
    run_id: str
    nombre: str
    argumentos: dict
    lado: str = 'agent'
    v2id: str | None = None          # petición del navegador de la app (lado cliente)
    sintetica: bool = False          # la abrió un permiso que ninguna herramienta abierta explicaba
    cancelada: bool = False          # su aprobación se canceló: su `tool_end` es `cancelled` (§4.5)
    subagente: str | None = None     # el subagente (aN) que lanzó, si es un `spawn_agent` o un `send_input`
    truncada: bool = False           # sus argumentos van acortados (`truncated`): no se puede aprobar (§4.4)


@dataclass
class Ejecucion:
    run_id: str
    agente: str | None = None        # id del subagente en AlfalfaCode (aN); None en la raíz
    activa: bool = True
    cerrando: bool = False           # el agente ya canceló algo suyo: no empieza trabajo nuevo (§4.2)
    tool_call_id: str | None = None  # la herramienta de la madre que la lanzó


@dataclass
class Aprobacion:
    id: str
    v2id: str
    run_id: str
    tool_call_id: str
    nombre: str
    digest: str
    scopes: list[str]
    expira: datetime | None          # sin `expires`, no caduca
    sitio: bool = False               # v2 pide `sitio` (una_vez|sesion|proyecto) en vez de `sesion`
    estado: str = 'pending'           # pending, approved, denied, expired, cancelled
    siempre: bool = False             # v2 ofrece «No volver a preguntar» (`prefijo`): `alfalfa.scope` `always`


def _json_seguro(valor: Any) -> dict | None:
    """El valor como objeto JSON canonizable, o None si no lo es (NaN, claves que no son texto…)."""
    try:
        copia = json.loads(json.dumps(valor, default=str, allow_nan=False))
        digest(copia)
    except (TypeError, ValueError, OverflowError):
        return None
    return copia if isinstance(copia, dict) else None


def tachar(valor: Any) -> Any:
    """Los secretos ya redactados por AlfalfaCode, con la marca de PACAI (`"[redacted]"`); claves y estructura igual."""
    if isinstance(valor, str):
        return valor.replace(REDACTADO, TACHADO)
    if isinstance(valor, list):
        return [tachar(v) for v in valor]
    if isinstance(valor, dict):
        return {k: tachar(v) for k, v in valor.items()}
    return valor


def argumentos_de(texto: str, datos: dict | None) -> tuple[dict, bool]:
    """Los argumentos de una herramienta a partir de su evento v2 de inicio, y si están acortados.

    Sólo se usa cuando el motor no dio los argumentos estructurados. Escribe `Inicio <nombre> · <argumentos>` con
    los argumentos ya sin secretos y recortados a 1000 caracteres. Si caben, son un literal de Python y valen tal
    cual; si no, se rehace lo que enseña la app (orden, carpeta, archivos) y se avisa con `truncated`.
    """
    cuerpo = texto.split(' · ', 1)[1] if texto.startswith('Inicio ') and ' · ' in texto else ''
    if cuerpo:
        try:
            valor = ast.literal_eval(cuerpo)
        except (ValueError, SyntaxError, MemoryError, RecursionError):
            valor = None
        limpio = _json_seguro(valor) if isinstance(valor, dict) else None
        if limpio is not None:
            return limpio, False
    datos = datos or {}
    args: dict[str, Any] = {}
    for origen, destino in (('orden', 'command'), ('cwd', 'cwd'), ('objetivo', 'target'), ('objetivos', 'targets')):
        if datos.get(origen):
            args[destino] = datos[origen]
    return args, True


def _tamano(valor: Any) -> int:
    return len(json.dumps(valor, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))


def recortar_argumentos(args: dict, limite: int) -> tuple[dict, bool]:
    """Los argumentos tal cual si caben en `limite` bytes; si no, una copia con los textos más largos recortados
    (acaban en «… [recortado]») y True. Si ni así caben, se quedan los campos que no son texto largo."""
    if _tamano(args) <= limite:
        return args, False
    copia = json.loads(json.dumps(args))
    for _ in range(200):
        cadenas: list[tuple[Any, Any]] = []
        pendientes = [copia]
        while pendientes:
            actual = pendientes.pop()
            for clave, hijo in (actual.items() if isinstance(actual, dict) else enumerate(actual)):
                if isinstance(hijo, str) and len(hijo) > 200:
                    cadenas.append((actual, clave))
                elif isinstance(hijo, (dict, list)):
                    pendientes.append(hijo)
        if not cadenas:
            break
        contenedor, clave = max(cadenas, key=lambda par: len(par[0][par[1]]))
        texto = contenedor[clave].removesuffix(RECORTADO)
        contenedor[clave] = texto[:len(texto) // 2] + RECORTADO
        if _tamano(copia) <= limite:
            return copia, True
    resto = {k: v for k, v in copia.items() if _tamano(v) <= 1000}
    return (resto if _tamano(resto) <= limite else {}), True


def archivos_del_diff(parche: str) -> list[dict]:
    """`path`, `added` y `removed` de cada archivo de un diff unificado.

    Se cuentan las líneas de cada hunk según su cabecera `@@ -a,b +c,d @@`: una línea añadida que empiece por
    `++` no se confunde con la cabecera `+++` del archivo siguiente."""
    archivos: list[dict] = []
    actual: dict | None = None
    viejas = nuevas = 0
    for linea in parche.splitlines():
        if viejas > 0 or nuevas > 0:
            if linea.startswith('+'):
                actual['added'] += 1
                nuevas -= 1
            elif linea.startswith('-'):
                actual['removed'] += 1
                viejas -= 1
            elif not linea.startswith('\\'):
                viejas, nuevas = viejas - 1, nuevas - 1
        elif linea.startswith('+++ '):
            ruta = linea[4:].strip()
            actual = {'path': ruta[2:] if ruta.startswith('b/') else ruta, 'added': 0, 'removed': 0}
            archivos.append(actual)
        elif linea.startswith('@@') and actual is not None:
            cabecera = re.match(r'@@ -\d+(?:,(\d+))? \+\d+(?:,(\d+))? @@', linea)
            if cabecera:
                viejas = int(cabecera.group(1) if cabecera.group(1) is not None else 1)
                nuevas = int(cabecera.group(2) if cabecera.group(2) is not None else 1)
    return archivos


def _fallo(datos: dict) -> bool:
    """Como el motor: la clasificación manda; sin ella, un `error` o un código de salida distinto de 0."""
    clasificacion = datos.get('clasificacion')
    if isinstance(clasificacion, dict) and 'disposition' in clasificacion:
        return clasificacion['disposition'] == 'failure'
    return bool(datos.get('error')) or datos.get('codigo', 0) != 0


class Traductor:
    def __init__(self, emisor: Emisor, *, perfiles=(), extensiones=(), herramientas_cliente=(),
                 caducidad: float | None = CADUCIDAD_APROBACION, reloj: Callable[[], datetime] = ahora,
                 max_argumentos: int = MAX_ARGUMENTOS, max_bytes: int = MAX_BYTES):
        self.emisor, self.reloj, self.max_argumentos = emisor, reloj, max_argumentos
        self.max_bytes = max_bytes
        self.perfiles, self.extensiones = set(perfiles), set(extensiones)
        self.herramientas_cliente = set(herramientas_cliente)
        self.caducidad = caducidad
        self.raiz: str | None = None                       # ejecución raíz activa
        self.ejecuciones: dict[str, Ejecucion] = {}
        self.subagentes: dict[str, str] = {}                # id aN → run_id de su invocación actual
        self.herramientas: dict[str, Herramienta] = {}      # abiertas, por tool_call_id
        self.aprobaciones: dict[str, Aprobacion] = {}
        self.permisos: dict[str, str] = {}                  # id del permiso v2 → id del approval_request
        self.de_sesion: list[tuple[str, str, str]] = []     # (nombre, digest, approval_id) con alcance session
        self.hay_cambios = False
        self.clientes_cancelados: set[str] = set()          # herramientas del cliente que el agente canceló
        self.motivo_rechazo: str | None = None              # por qué `permiso` devolvió None, si no es lo de siempre
        self.fines_ignorados: dict[tuple[str, str], int] = {}  # (run_id, nombre): fines del motor ya cerrados aquí
        # Quien conteste a v2 cuando PACAI no puede (navegador no declarado o cancelado): (id v2, texto).
        self.al_rechazar_navegador: Callable[[str, str], None] | None = None

    # -- utilidades --------------------------------------------------------
    @property
    def trabajo(self) -> bool:
        return 'work' in self.perfiles

    def _ext(self, nombre: str, payload: dict) -> list[dict]:
        if 'alfalfa' not in self.extensiones:
            return []
        return [self.emisor.mensaje('alfalfa.' + nombre, en_ingles(payload))]

    def _msg(self, tipo: str, payload: dict, reply_to: str | None = None) -> dict:
        return self.emisor.mensaje(tipo, payload, reply_to=reply_to)

    def _activa(self, run_id: str | None) -> bool:
        return run_id is not None and run_id in self.ejecuciones and self.ejecuciones[run_id].activa

    def _abierta(self, run_id: str | None) -> bool:
        """Activa y sin estar en cierre: puede empezar trabajo nuevo (mensajes, herramientas, aprobaciones…)."""
        return self._activa(run_id) and not self.ejecuciones[run_id].cerrando

    def _cancelar_aprobacion(self, a: Aprobacion, h: Herramienta | None = None,
                             motivo: str | None = None) -> list[dict]:
        """La ejecución termina o se interrumpe con la petición sin contestar: `approval_result` `cancelled` del
        agente (§4.5), y la ejecución entra en cierre. Su herramienta se cerrará con `cancelled`. `motivo`: el de v2
        («Turno interrumpido», «Sesión interrumpida»…), en `reason`."""
        a.estado = 'cancelled'
        self.permisos.pop(a.v2id, None)
        if a.run_id in self.ejecuciones:
            self.ejecuciones[a.run_id].cerrando = True
        h = h or self.herramientas.get(a.tool_call_id)
        if h is not None:
            h.cancelada = True
        return [self._msg('approval_result', {'decision': 'cancelled', **({'reason': motivo[:500]} if motivo else {})},
                          reply_to=a.id)]

    # -- entrada principal -------------------------------------------------
    def evento(self, tipo: str, campos: dict) -> list[dict]:
        """Los mensajes PACAI de un evento v2. Los que no tienen equivalente salen como extensión."""
        manejador = getattr(self, '_v2_' + tipo, None)
        if manejador is not None:
            return manejador(campos)
        return self._ext(TIPOS.get(tipo, tipo), {k: v for k, v in campos.items() if k != 'version'})

    # -- ejecuciones (§4.2) ------------------------------------------------
    def empezar_turno(self, input_id: str) -> list[dict]:
        """Un turno del motor abre una ejecución raíz: el `id` del `run_started` es su `run_id`."""
        m = self._msg('run_started', {'input_id': input_id})
        self.raiz = m['id']
        self.ejecuciones[m['id']] = Ejecucion(m['id'])
        self.hay_cambios = False
        return [m]

    def _cerrar_herramientas(self, run_id: str, estado: str = 'cancelled') -> list[dict]:
        """Cierra lo que sigue abierto en una ejecución que termina: las del agente y, con `tool_end` `cancelled` sin
        `reply_to`, las del cliente (§4.4)."""
        salida = []
        for tc in [t for t, h in self.herramientas.items() if h.run_id == run_id]:
            salida += self._cerrar(tc, estado)
        return salida

    def _cerrar(self, tc: str, estado: str, salida: list | None = None, resumen: str | None = None,
                extra: dict | None = None) -> list[dict]:
        h = self.herramientas.pop(tc)
        mensajes: list[dict] = []
        for a in list(self.aprobaciones.values()):
            if a.tool_call_id == tc and a.estado == 'pending':
                mensajes += self._cancelar_aprobacion(a, h)       # antes que su `tool_end` (§4.5)
        if h.cancelada:
            estado, salida, resumen = 'cancelled', None, None
        payload: dict[str, Any] = {'tool_call_id': tc, 'status': estado}
        if salida:
            payload['output'] = salida
        if resumen:
            payload['summary'] = resumen[:300]
        if extra and not h.cancelada:
            payload.update(self._alfalfa(extra))
        if h.lado == 'client':
            return mensajes + self._cancelar_del_cliente(h)
        return mensajes + [self._msg('tool_end', payload)]

    def _cancelar_del_cliente(self, h: Herramienta) -> list[dict]:
        """El agente cancela una herramienta del cliente que sigue abierta al terminar su ejecución (§4.4): la
        ejecución entra en cierre, el motor recibe un «no» y el `tool_end` tardío del cliente se ignorará."""
        self.clientes_cancelados.add(h.id)
        if h.run_id in self.ejecuciones:
            self.ejecuciones[h.run_id].cerrando = True
        if self.al_rechazar_navegador is not None and h.v2id is not None:
            self.al_rechazar_navegador(h.v2id, 'La ejecución terminó y se canceló la herramienta del cliente.')
        return [self._msg('tool_end', {'tool_call_id': h.id, 'status': 'cancelled'})]

    def _terminar(self, run_id: str, resultado: str, detalle: str | None = None) -> list[dict]:
        ej = self.ejecuciones[run_id]
        salida = self._cerrar_herramientas(run_id)
        payload = {'run_id': run_id, 'outcome': resultado}
        if resultado in ('failed', 'blocked'):
            payload['detail'] = (detalle or 'sin detalle')[:1000]
        salida.append(self._msg('run_finished', payload))
        ej.activa = False
        if ej.agente is not None and self.subagentes.get(ej.agente) == run_id:
            del self.subagentes[ej.agente]
        lanzadora = self.herramientas.get(ej.tool_call_id) if ej.tool_call_id else None
        if resultado == 'interrupted' and lanzadora is not None:
            # La madre recibe el `tool_end` `cancelled` de la herramienta que lanzó al subagente y sigue (§4.6);
            # el fin que el motor mande luego por ella ya no sale. Si ya terminó (segundo plano), nada que cerrar.
            clave = (lanzadora.run_id, lanzadora.nombre)
            self.fines_ignorados[clave] = self.fines_ignorados.get(clave, 0) + 1
            salida += self._cerrar(lanzadora.id, 'cancelled')
        return salida

    def cerrar_turno(self, campos: dict, diff: str | dict | None = None, turno: bool = True) -> list[dict]:
        """`turno_terminado` → lo que queda del turno (subejecuciones, uso, cambios) y su `run_finished`.

        Con la extensión, `alfalfa.closing` lleva el `turno_terminado` entero (estado exacto, código de salida, tokens
        y coste de la sesión, cierre…): un cliente rehace con él lo que v2 contaba. `turno=False` cuando no lo cerró
        el motor (`fin` con el turno abierto): entonces no hay `turno_terminado` que contar."""
        if self.raiz is None:
            return []
        raiz = self.raiz
        salida: list[dict] = []
        for run_id, ej in list(self.ejecuciones.items()):
            if ej.activa and ej.agente is not None:      # de las hojas a la raíz (§4.6, §4.9)
                salida += self._terminar(run_id, 'interrupted')
        resultado = RESULTADO_DE_TURNO.get(campos.get('estado'), 'failed')
        uso = {}
        if isinstance(campos.get('coste_turno'), (int, float)):
            uso['cost'] = {'amount': campos['coste_turno'], 'currency': 'USD'}
        if uso or campos.get('tokens_turno'):
            extra = {'turn_tokens': campos['tokens_turno']} if isinstance(campos.get('tokens_turno'), int) else {}
            salida.append(self._msg('usage', {'run_id': raiz, **uso, **self._alfalfa(extra)}))
        if self.trabajo and diff:
            # Un diff unificado (instantáneas) o ya contado (`diff_git`: `files` y quizá `patch`).
            archivos, parche = (diff.get('files'), diff.get('patch')) if isinstance(diff, dict) else \
                (archivos_del_diff(diff), diff)
            if archivos:
                salida.append(self._msg('diff_updated', {'run_id': raiz, 'files': archivos,
                                                         **({'patch': parche} if parche else {})}))
        if turno:
            salida += self._ext('closing', {'run_id': raiz, **{k: v for k, v in campos.items() if k != 'version'}})
        salida += self._terminar(raiz, resultado, str(campos.get('resumen') or ''))
        self.raiz = None
        return salida

    def terminar_trabajo(self, resultado: str, detalle: str | None = None) -> list[dict]:
        """Cierra la ejecución de un trabajo fuera de un turno (una prueba del navegador, una cola de encargos):
        no hubo `turno_terminado`, así que no hay uso, cambios ni `alfalfa.closing`."""
        if self.raiz is None:
            return []
        raiz, self.raiz = self.raiz, None
        return self._terminar(raiz, resultado, detalle)

    def _alfalfa(self, datos: dict) -> dict:
        return {'alfalfa': en_ingles(datos)} if datos and 'alfalfa' in self.extensiones else {}

    def _v2_turno_terminado(self, campos: dict) -> list[dict]:
        return self.cerrar_turno(campos)

    def cerrar_todo(self, campos: dict) -> list[dict]:
        """`fin` → se cierra lo que siga abierto (como interrumpido) y llega el `close` del agente."""
        salida: list[dict] = []
        if self.raiz is not None:
            salida += self.cerrar_turno({'estado': 'interrupted', 'resumen': campos.get('resumen', '')}, turno=False)
        extra = {k: v for k, v in campos.items() if k != 'version'}           # el `fin` de v2 entero
        salida.append(self._msg('close', {'reason': str(campos.get('resumen') or campos.get('estado') or ''),
                                          **self._alfalfa(extra)}))
        return salida

    # -- mensajes (§4.3) ---------------------------------------------------
    def _aviso(self, aviso: tuple[str, dict] | None, por_defecto: dict) -> list[dict]:
        """Lo que no cabe en una ejecución abierta va como extensión: `aviso` es (tipo, campos de v2)."""
        nombre, campos = aviso if aviso is not None else ('notice', por_defecto)
        return self._ext(nombre, campos)

    def _mensaje(self, run_id: str, rol: str, texto: str, extra: dict | None = None,
                 aviso: tuple[str, dict] | None = None) -> list[dict]:
        """`extra`: lo de v2 que el núcleo no lleva (el `turno`), en `payload.alfalfa` del `message_start`."""
        if not texto:
            return []
        if not self._abierta(run_id):
            # En cierre no empieza un mensaje nuevo (§4.2): lo que diga el motor va como aviso de la extensión.
            return self._aviso(aviso, {'run_id': run_id, 'role': rol, 'text': texto})
        inicio = self._msg('message_start', {'run_id': run_id, 'role': rol,
                                             **self._alfalfa({k: v for k, v in (extra or {}).items() if v})})
        salida = [inicio]
        n = 0
        while n < len(texto):
            bloque = {'type': 'text', 'text': ''}
            delta = self._msg('message_delta', {'message_id': inicio['id'], 'blocks': [bloque]})
            espacio = self.max_bytes - bytes_de_linea(codificar(delta))
            bloque['text'] = prefijo_que_cabe(texto[n:n + TROZO], espacio)
            if not bloque['text']:
                # El sobre deja menos de un carácter. El transporte notificará lo que no cabe.
                bloque['text'] = texto[n:]
            n += len(bloque['text'])
            salida.append(delta)
        salida.append(self._msg('message_end', {'message_id': inicio['id']}))
        return salida

    def _v2_evento(self, campos: dict) -> list[dict]:
        rol, texto = campos.get('rol'), campos.get('texto')
        texto = texto if isinstance(texto, str) else ''
        propio = {k: v for k, v in campos.items() if k not in ('version', '_argumentos')}
        if rol == 'user':
            # El cliente ya lo mandó él: el núcleo no lo devuelve. La extensión avisa de cuándo entró en la
            # conversación (en el turno en curso o al abrir el siguiente), con el id del `input`.
            return self._ext('user_message', {'text': texto, **({'input_id': campos['id_cola']}
                                                                if campos.get('id_cola') else {}),
                                              **({'turn': campos['turno']} if campos.get('turno') else {})})
        if rol == 'tool':
            datos = campos.get('herramienta')
            extra = {'text': texto, 'tool': datos, 'turn': campos.get('turno')}
            if isinstance(datos, dict) and datos.get('fase') == 'inicio':
                return self._tool_start(self.raiz, texto, datos, campos.get('_argumentos'), extra, ('notice', propio))
            if isinstance(datos, dict) and datos.get('fase') == 'fin':
                return self._tool_end(self.raiz, datos, texto, extra, ('notice', propio))
            return self._ext('notice', propio)
        extra = {'turn': campos.get('turno')}
        if rol in ('agent', 'assistant') and self._activa(self.raiz):
            return self._mensaje(self.raiz, 'assistant', texto, extra, ('notice', dict(propio, run_id=self.raiz)))
        if rol == 'system' and self._activa(self.raiz) and 'compactacion' not in campos and 'tarifa' not in campos:
            # Lo importado sin autorizar es texto para cualquier cliente; sus datos, en `payload.alfalfa.imported`.
            extra['importadas'] = campos.get('importadas')
            return self._mensaje(self.raiz, 'system', texto, extra, ('notice', dict(propio, run_id=self.raiz)))
        return self._ext('notice', propio)

    # -- herramientas (§4.4) -----------------------------------------------
    def _tool_start(self, run_id: str | None, texto: str, datos: dict, argumentos: Any = None,
                    extra: dict | None = None, aviso: tuple[str, dict] | None = None) -> list[dict]:
        """`argumentos`: los de la llamada del modelo, ya sin secretos (`Transport.argumentos_herramienta`). Sin
        ellos (o si no son un objeto JSON) se sacan, como antes, del texto del evento.

        `extra`: lo de v2 que el núcleo no lleva (el texto del evento, `herramienta` entera y el `turno`), en
        `payload.alfalfa`. `aviso`: sin ejecución abierta, el evento de v2 sale así, en la extensión."""
        if not self._abierta(run_id):
            return self._ext(*aviso) if aviso is not None else []     # sin `tool_start`, su fin tampoco sale
        nombre = str(datos.get('nombre') or 'tool')
        reales = _json_seguro(argumentos) if isinstance(argumentos, dict) else None
        if reales is not None:
            args, recortados = recortar_argumentos(tachar(reales), self.max_argumentos)
        else:
            args, recortados = argumentos_de(texto, datos)
            args = tachar(args)
        payload: dict[str, Any] = {'run_id': run_id, 'name': nombre, 'arguments': args, 'side': 'agent'}
        # Lo legible va aparte (como `title` de ACP): la descripción que enseña la app o, sin ella, el texto de
        # siempre del motor.
        legible = datos.get('descripcion') or (texto.split(' · ', 1)[1] if ' · ' in texto else '')
        if legible:
            payload['title'] = str(legible)[:200]
        if recortados:
            payload['truncated'] = True
        d = digest(args)
        aprobada = next((a for n, g, a in self.de_sesion if n == nombre and g == d), None)
        if aprobada and not recortados:          # una llamada truncada no reutiliza una aprobación `session`
            payload['approval_id'] = aprobada
        payload.update(self._alfalfa({k: v for k, v in (extra or {}).items() if v}))
        m = self._msg('tool_start', payload)
        self.herramientas[m['id']] = Herramienta(m['id'], run_id, nombre, args, truncada=recortados)
        return [m]

    def _tool_end(self, run_id: str | None, datos: dict, texto: str, extra: dict | None = None,
                  aviso: tuple[str, dict] | None = None) -> list[dict]:
        nombre = str(datos.get('nombre') or '')
        # Un fin sin herramienta abierta (ya cerrada aquí, cortada con el turno anterior o sin inicio) no es un
        # `tool_end` del núcleo; el evento de v2 sale en la extensión, porque la app lo enseña («Resultado
        # incierto…» de una orden cortada, el fin de un subagente interrumpido).
        if self.fines_ignorados.get((run_id, nombre)):
            self.fines_ignorados[(run_id, nombre)] -= 1
            return self._ext(*aviso) if aviso is not None else []
        tc = next((t for t, h in self.herramientas.items()
                   if h.run_id == run_id and h.nombre == nombre and h.lado == 'agent'), None)
        if tc is None:
            return self._ext(*aviso) if aviso is not None else []
        estado = 'error' if _fallo(datos) else 'success'
        cuerpo = texto.split('\n', 1)[1] if '\n' in texto else ''
        salida = [{'type': 'text', 'text': cuerpo[:MAX_SALIDA]}] if cuerpo else None
        extra = {k: v for k, v in (extra or {}).items() if v and k != 'text'}
        # El texto de v2 es «nombre\nresultado»: el cliente lo rehace con `name` y `output`. Sólo va aparte si no
        # tiene esa forma (una salida recortada se queda recortada: no se manda dos veces).
        if texto.split('\n', 1)[0] != nombre or ('\n' in texto and not cuerpo):
            extra['text'] = texto[:MAX_SALIDA]
        resumen = None
        if estado == 'error' and cuerpo:
            try:
                error = json.loads(cuerpo).get('error')
            except (ValueError, AttributeError):
                error = None
            resumen = error if isinstance(error, str) else None
        if estado == 'success' and nombre in EDITORES:
            self.hay_cambios = True
        return self._cerrar(tc, estado, salida, resumen, extra)

    # -- subagentes = subejecuciones (§4.9) --------------------------------
    def _subejecucion(self, ident: str, nombre: str = 'subagent', descripcion: str = '') -> list[dict]:
        """Abre la subejecución de un subagente si no tiene una activa (nunca en una raíz en cierre)."""
        if self._activa(self.subagentes.get(ident)) or not self._abierta(self.raiz):
            return []
        agente = {'name': nombre or 'subagent', **({'description': descripcion[:200]} if descripcion else {})}
        lanzadora = self._lanzadora(ident)
        m = self._msg('run_started', {'parent_run_id': self.raiz, 'agent': agente,
                                      **({'tool_call_id': lanzadora.id} if lanzadora else {}),
                                      **self._alfalfa({'agent': ident})})      # el id del subagente (aN)
        self.ejecuciones[m['id']] = Ejecucion(m['id'], agente=ident, tool_call_id=lanzadora.id if lanzadora else None)
        self.subagentes[ident] = m['id']
        return [m]

    def _lanzadora(self, ident: str) -> Herramienta | None:
        """La herramienta abierta de la raíz que lanza este subagente: un `spawn_agent` aún sin subagente, o un
        `send_input` a él. Sin ninguna (el cliente le escribió con `steer`), la subejecución no lleva `tool_call_id`."""
        for h in reversed(list(self.herramientas.values())):
            if h.run_id != self.raiz or h.lado != 'agent' or h.nombre not in LANZADORES or h.subagente is not None:
                continue
            destino = h.argumentos.get('agent')
            if h.nombre == 'spawn_agent' or destino in (None, ident):
                h.subagente = ident
                return h
        return None

    def _v2_subagentes(self, campos: dict) -> list[dict]:
        salida: list[dict] = []
        filas = [f for f in campos.get('subagentes') or [] if isinstance(f, dict) and f.get('id')]
        for fila in filas:
            ident, estado = str(fila['id']), fila.get('estado')
            if estado not in TERMINADOS or self._activa(self.subagentes.get(ident)):
                salida += self._subejecucion(ident, str(fila.get('tipo') or ''), str(fila.get('nombre') or ''))
            run_id = self.subagentes.get(ident)
            if estado in TERMINADOS and self._activa(run_id):
                resultado = {'terminado': 'success', 'fallido': 'failed', 'interrumpido': 'interrupted',
                             'cerrado': 'interrupted'}[estado]
                salida += self._terminar(run_id, resultado, str(fila.get('error') or ''))
        return salida + self._ext('subagents', {'subagentes': filas})

    def _v2_subagente_evento(self, campos: dict) -> list[dict]:
        ident = str(campos.get('agente') or '')
        salida = self._subejecucion(ident)
        run_id = self.subagentes.get(ident)
        rol, texto = campos.get('rol'), str(campos.get('texto') or '')
        # Lo que no cabe en su subejecución (no hay una abierta, o es un rol que el núcleo no tiene) no se pierde.
        aviso = ('subagent_event', {k: v for k, v in campos.items() if k not in ('version', '_argumentos')})
        if not self._activa(run_id) or rol not in ('tool', 'agent', 'assistant', 'system'):
            return salida + self._ext(*aviso)
        if rol == 'tool' and texto.startswith('Inicio '):
            nombre = texto[len('Inicio '):].split(' · ', 1)[0]
            return salida + self._tool_start(run_id, texto, {'nombre': nombre}, campos.get('_argumentos'),
                                             {'text': texto}, aviso)
        if rol == 'tool':
            return salida + self._tool_end(run_id, {'nombre': texto.split('\n', 1)[0]}, texto, None, aviso)
        return salida + self._mensaje(run_id, 'system' if rol == 'system' else 'assistant', texto, None, aviso)

    # -- aprobaciones (§4.5) -----------------------------------------------
    def _herramienta_del_permiso(self, run_id: str, argv: Any) -> Herramienta | None:
        abiertas = [h for h in self.herramientas.values() if h.run_id == run_id and h.lado == 'agent']
        argv = tachar(argv)
        for h in reversed(abiertas):
            propio = h.argumentos.get('argv')
            if h.truncada or propio is None or propio == argv:
                return h
        return None

    def permiso(self, campos: dict) -> list[dict] | None:
        """`permiso` v2 → `approval_request`. None si no hay ejecución a la que atribuirlo."""
        proposito = str(campos.get('proposito') or '')
        sub = re.match(r'Subagente (\S+) «', proposito)
        run_id = self.subagentes.get(sub.group(1)) if sub else None
        if not self._activa(run_id):
            run_id = self.raiz
        if not self._abierta(run_id):
            return None                  # sin ejecución, o en cierre: no se pregunta (§4.2)
        argv = campos.get('argv')
        salida: list[dict] = []
        herramienta = self._herramienta_del_permiso(run_id, argv)
        if herramienta is not None and herramienta.truncada:
            # Nadie puede aprobar lo que no ve entero (§4.4): se niega y la herramienta termina en `error`.
            self.motivo_rechazo = 'Los argumentos no caben enteros en el mensaje y no se pueden aprobar.'
            return None
        if herramienta is None:
            args = {'argv': tachar(argv), 'purpose': proposito[:500]}
            inicio = self._msg('tool_start', {'run_id': run_id, 'name': SINTETICA,
                                              'arguments': _json_seguro(args) or {}, 'side': 'agent',
                                              'title': proposito[:200] or 'Permiso',
                                              **self._alfalfa({'synthetic': True})})   # no es del motor
            herramienta = Herramienta(inicio['id'], run_id, SINTETICA, inicio['payload']['arguments'], sintetica=True)
            self.herramientas[inicio['id']] = herramienta
            salida.append(inicio)
        scopes = ['once'] + (['session'] if campos.get('para_sesion') or isinstance(campos.get('sitio'), dict) else [])
        peticion = self._msg('approval_request', {
            'run_id': run_id, 'tool_call_id': herramienta.id, 'name': herramienta.nombre,
            'arguments': herramienta.argumentos, 'digest': digest(herramienta.argumentos),
            'reason': self._razon(campos), 'scopes': scopes,
            **({'expires': en_segundos(self.reloj(), self.caducidad)} if self.caducidad else {}),
            **self._extra_del_permiso(campos)})
        self.aprobaciones[peticion['id']] = Aprobacion(
            peticion['id'], str(campos.get('id')), run_id, herramienta.id, herramienta.nombre,
            peticion['payload']['digest'], scopes,
            self._instante(peticion['payload']['expires']) if 'expires' in peticion['payload'] else None,
            sitio=isinstance(campos.get('sitio'), dict), siempre=bool(campos.get('prefijo')))
        self.permisos[str(campos.get('id'))] = peticion['id']
        return salida + [peticion]

    def _extra_del_permiso(self, campos: dict) -> dict:
        """Lo de v2 que el núcleo no lleva, en `payload.alfalfa`. Los argumentos de una herramienta MCP son del
        servidor, no de AlfalfaCode: van tal cual (sus claves no se traducen)."""
        extra = self._alfalfa({k: campos[k] for k in PERMISO_EXTRA if campos.get(k)})
        mcp = campos.get('mcp')
        if extra and isinstance(mcp, dict) and 'argumentos' in mcp and isinstance(extra['alfalfa'].get('mcp'), dict):
            extra['alfalfa']['mcp']['arguments'] = mcp['argumentos']
        return extra

    @staticmethod
    def _instante(texto: str) -> datetime:
        return datetime.fromisoformat(texto.replace('Z', '+00:00'))

    def _razon(self, campos: dict) -> str:
        motivo = campos.get('motivo') or next(iter(campos.get('motivos') or []), None)
        if campos.get('mcp'):
            return 'external_tool'
        if campos.get('sin_sandbox') and motivo not in RAZON_DE:
            return 'outside_sandbox'
        if motivo in RAZON_DE:
            return RAZON_DE[motivo]
        propia = razon(str(motivo or ''))
        if 'alfalfa' in self.extensiones and re.fullmatch(r'[a-z][a-z0-9_]*', propia):
            return 'alfalfa.' + propia
        return 'policy'

    def aprobacion(self, request_id: str) -> Aprobacion | None:
        return self.aprobaciones.get(request_id)

    def _v2_permiso_resuelto(self, campos: dict) -> list[dict]:
        """Lo resolvió el cliente, la política o el cierre: PACAI ya lo sabe o no lo cuenta (el `expired` es aparte)."""
        peticion = self.permisos.pop(str(campos.get('id')), None)
        a = self.aprobaciones.get(peticion) if peticion else None
        if a is None:
            return []
        salida: list[dict] = []
        if a.estado == 'pending' and campos.get('aprobado'):
            a.estado = 'approved'
        elif a.estado == 'pending':
            # El cliente no contestó: lo negó la interrupción, el fin del turno o el cierre de la sesión.
            salida += self._cancelar_aprobacion(a, motivo=str(campos.get('motivo') or '') or None)
        if campos.get('aprobado') and (campos.get('sesion') or campos.get('sitio') == 'sesion'):
            self.de_sesion.append((a.nombre, a.digest, a.id))
        h = self.herramientas.get(a.tool_call_id)
        if h is not None and h.sintetica:
            return salida + self._cerrar(h.id, 'success' if campos.get('aprobado') else 'error')
        return salida

    def caducar(self, request_id: str) -> list[dict]:
        """El agente declara caducada una petición sin respuesta (§4.5). Nunca antes de `expires`."""
        a = self.aprobaciones.get(request_id)
        if a is None or a.estado != 'pending' or a.expira is None:
            return []                    # sin `expires` nunca hay `expired`
        a.estado = 'expired'
        salida = [self._msg('approval_result', {'decision': 'expired'}, reply_to=request_id)]
        self.permisos.pop(a.v2id, None)
        h = self.herramientas.get(a.tool_call_id)
        if h is not None and h.sintetica:
            salida += self._cerrar(h.id, 'cancelled')
        return salida

    # -- navegador de la app = herramienta del cliente (§4.4) --------------
    def _v2_navegador(self, campos: dict) -> list[dict]:
        ident, accion = str(campos.get('id')), campos.get('accion')
        if accion == 'cancelar_relevo':
            tc = next((t for t, h in self.herramientas.items() if h.v2id == ident), None)
            return self._ext('browser_cancel', {'tool_call_id': tc}) if tc else []
        if NAVEGADOR not in self.herramientas_cliente or not self._abierta(self.raiz):
            if self.al_rechazar_navegador is not None:
                self.al_rechazar_navegador(ident, 'El cliente PACAI no declaró la herramienta «' + NAVEGADOR
                                           + '» o no hay una ejecución activa.')
            return []
        # Los argumentos de la herramienta del cliente, en inglés (`action: "open"`…); el motor sigue hablando v2.
        args = _json_seguro(peticion_del_navegador({k: v for k, v in campos.items()
                                                    if k not in ('version', 'tipo', 'id')})) or {}
        m = self._msg('tool_start', {'run_id': self.raiz, 'name': NAVEGADOR, 'arguments': args, 'side': 'client',
                                     'title': 'Navegador: ' + str(accion)[:100]})
        self.herramientas[m['id']] = Herramienta(m['id'], self.raiz, NAVEGADOR, args, lado='client', v2id=ident)
        return [m]

    def fin_de_herramienta_cliente(self, tc: str) -> Herramienta | None:
        """El cliente cerró su herramienta: se olvida y se devuelve para contestar a v2."""
        h = self.herramientas.get(tc)
        if h is None or h.lado != 'client':
            return None
        return self.herramientas.pop(tc)

    # -- núcleo restante ---------------------------------------------------
    def _v2_plan(self, campos: dict) -> list[dict]:
        pasos = [{'text': str(p.get('paso') or ''), 'status': p.get('estado') if p.get('estado') in
                  ('pending', 'in_progress', 'completed') else 'pending'}
                 for p in campos.get('pasos') or [] if isinstance(p, dict)]
        extra = {'explanation': campos['explicacion']} if campos.get('explicacion') else {}
        if self.trabajo and self._activa(self.raiz):
            return [self._msg('plan_updated', {'run_id': self.raiz, 'steps': pasos, **self._alfalfa(extra)})]
        return self._ext('plan', {'steps': pasos, **extra})

    def _v2_contexto(self, campos: dict) -> list[dict]:
        payload: dict[str, Any] = {}
        if self._activa(self.raiz):
            payload['run_id'] = self.raiz
        if isinstance(campos.get('tokens'), int):
            payload['context_tokens'] = campos['tokens']
        if isinstance(campos.get('limite'), int) and campos['limite'] > 0:
            payload['context_limit'] = campos['limite']
        salida = [self._msg('usage', payload)] if len(payload) > (1 if 'run_id' in payload else 0) else []
        # Con la extensión, el `contexto` entero va siempre en `alfalfa.context`: así el cliente lo tiene en un
        # solo mensaje, traiga o no detalle (sin él, el núcleo ya basta).
        return salida + self._ext('context', {k: v for k, v in campos.items() if k != 'version'})

    def _v2_uso_sesion(self, campos: dict) -> list[dict]:
        payload: dict[str, Any] = {}
        if isinstance(campos.get('coste'), (int, float)) and not isinstance(campos.get('coste'), bool):
            payload['cost'] = {'amount': campos['coste'], 'currency': 'USD'}
        if isinstance(campos.get('tokens'), int):
            payload.update(self._alfalfa({'total_tokens': campos['tokens']}))
        return [self._msg('usage', payload)] if payload else []

    def _v2_error(self, campos: dict) -> list[dict]:
        texto = str(campos.get('texto') or 'Error')
        extra = {k: v for k, v in campos.items() if k not in ('version', 'texto')}
        return [self._msg('error', {'code': CODIGOS_DE_ERROR.get(texto, 'alfalfa.engine_error'), 'message': texto,
                                    **self._alfalfa(extra)})]

    def error(self, codigo: str, mensaje: str, ref: str | None = None) -> dict:
        payload = {'code': codigo, 'message': mensaje[:500]}
        if ref:
            payload['ref'] = ref
        return self._msg('error', payload)

    def _v2_fin(self, campos: dict) -> list[dict]:
        return self.cerrar_todo(campos)
