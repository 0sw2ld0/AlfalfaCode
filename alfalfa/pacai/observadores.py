"""Observadores de una sesión `--pacai` en marcha: otros clientes que la miran en vivo sin participar.

El proceso que tiene el cerrojo de la sesión abre un socket Unix en su carpeta privada (`<sesiones>/<id>/vivo.sock`,
en 0600 dentro de una carpeta 0700) que habla PACAI 0.3 en JSONL, como stdio. Quien se conecta abre su propia sesión
PACAI (su `session` y sus `seq`) con `hello.payload.observe: true` y recibe:

1. `hello`, `session_started` (con `observe: true`) y un `snapshot` con `reply_to` a su `hello`: las ejecuciones
   activas, las aprobaciones pendientes y, en `last_messages`, lo necesario para seguir: el `run_started` de cada
   ejecución activa, cada `tool_start` abierto, el mensaje a medias y el último de cada estado (uso, plan, contexto,
   presupuesto…). Con la extensión, `payload.alfalfa` lleva el id de la sesión de AlfalfaCode y `history_until`:
   hasta qué hora (`time` de sus filas) vale la transcripción en disco; lo posterior le llega en vivo.
2. Después, copia de cada mensaje que el agente manda al dueño (el cliente de stdio) con el mismo `id`, `type`,
   `time`, `reply_to` y `payload`, y el `session` y la `seq` del observador: los `run_id`, `message_id`,
   `tool_call_id` y `approval_id` siguen valiendo. No se copian el saludo del dueño, los errores que contestan a sus
   mensajes (`ref`) ni sus herramientas del cliente (`side: client`), que son cosa suya.
3. Cuando el dueño contesta un permiso, el agente se lo cuenta con un `approval_result` propio con `relayed: true`
   (la decisión del dueño, `approved` o `denied`), para que retire la tarjeta.

Sólo el dueño contesta permisos, escribe o interrumpe. Lo que el observador mande aparte de `heartbeat`, `error` y
`close` recibe `error` `read_only` y no llega al motor. El observador nunca frena al motor: si no lee y se le
acumulan más de `MAX_PENDIENTE` bytes, se le corta la conexión.
"""
from __future__ import annotations

import asyncio
import json
import os
import socket
import stat
import sys
from pathlib import Path
from typing import Any, Callable, Iterable

from .esquema import validar
from .sobre import Emisor, ahora
from .tipos import PROFILE_OF, VERSION

__all__ = ['Mirador', 'NOMBRE_SOCKET', 'ruta_del_socket', 'ultima_hora', 'MAX_OBSERVADORES', 'MAX_PENDIENTE']

NOMBRE_SOCKET = 'vivo.sock'
MAX_OBSERVADORES = 8
MAX_PENDIENTE = 16 * 1024 * 1024        # bytes sin leer que se le aguantan a un observador
MAX_LINEA = 1024 * 1024
ESPERA_SALUDO = 10.0                    # segundos para mandar el `hello` tras conectarse
# El mayor `sun_path` de macOS (104 bytes con el nulo final); Linux admite 108. Más largo, no hay socket.
MAX_RUTA = 103
PERFILES = ('work',)
EXTENSIONES = ('alfalfa',)
# Estados de los que basta el último: van en `last_messages` del `snapshot`.
ESTADOS = frozenset({'alfalfa.session', 'alfalfa.budget', 'alfalfa.processes', 'alfalfa.awake', 'alfalfa.quota',
                     'alfalfa.preparing', 'alfalfa.code_review', 'alfalfa.jobs', 'alfalfa.context',
                     'alfalfa.subagents', 'alfalfa.plan'})
# Del saludo del dueño: es de su sesión, no se copia.
DEL_SALUDO = frozenset({'hello', 'session_started'})
# Lo único que un observador puede mandar tras su `hello`.
DEL_OBSERVADOR = frozenset({'heartbeat', 'error', 'close'})


def ruta_del_socket(carpeta: Path) -> Path:
    return Path(carpeta) / NOMBRE_SOCKET


def ultima_hora(ruta: Path) -> float:
    """La hora (`time`) de la última fila completa de una transcripción (`<id>.jsonl`); 0 si no hay ninguna."""
    try:
        with open(ruta, 'rb') as archivo:
            archivo.seek(0, os.SEEK_END)
            tamano = archivo.tell()
            archivo.seek(max(0, tamano - 256 * 1024))
            datos = archivo.read()
    except OSError:
        return 0.0
    lineas = datos.split(b'\n')
    if not datos.endswith(b'\n'):
        lineas = lineas[:-1]                 # la última aún a medias
    for linea in reversed(lineas):
        try:
            fila = json.loads(linea)
        except ValueError:
            continue
        hora = fila.get('time') if isinstance(fila, dict) else None
        if isinstance(hora, (int, float)) and not isinstance(hora, bool):
            return float(hora)
    return 0.0


class Observador:
    """Una conexión de observador: su sesión PACAI (la del `hello`), su `seq` y lo que negoció."""

    def __init__(self, writer: asyncio.StreamWriter, reloj: Callable):
        self.writer = writer
        self.emisor = Emisor('agent', '', reloj=reloj)
        self.saludado = False
        self.cerrado = False
        self.perfiles: set[str] = set()
        self.extensiones: set[str] = set()
        self.client_seq = 0

    def ve(self, tipo: str) -> bool:
        """¿Este tipo de mensaje cabe en lo que negoció? (§4.1: perfiles y extensiones activos)."""
        if tipo in PROFILE_OF:
            return PROFILE_OF[tipo] in self.perfiles
        if '.' in tipo:
            return tipo.split('.', 1)[0] in self.extensiones
        return True

    def suyo(self, m: dict) -> dict:
        """Un mensaje del dueño con el `session` del observador (lo que va dentro de su `snapshot`)."""
        return dict(m, session=self.emisor.sesion)

    def copia(self, m: dict) -> None:
        """Un mensaje del dueño en la sesión del observador: su `session` y su siguiente `seq`."""
        self.emisor.seq += 1
        self.escribir(dict(m, session=self.emisor.sesion, seq=self.emisor.seq))

    def propio(self, tipo: str, payload: dict, reply_to: str | None = None) -> None:
        self.escribir(self.emisor.mensaje(tipo, payload, reply_to=reply_to))

    def error(self, codigo: str, mensaje: str, ref: str | None = None) -> None:
        self.propio('error', {'code': codigo, 'message': mensaje[:500], **({'ref': ref} if ref else {})})

    def escribir(self, m: dict) -> None:
        if self.cerrado:
            return
        try:
            linea = json.dumps(m, ensure_ascii=False, separators=(',', ':'), default=str) + '\n'
            self.writer.write(linea.encode('utf-8'))
            pendiente = self.writer.transport.get_write_buffer_size()
        except (OSError, RuntimeError, AttributeError):
            self.cortar()
            return
        if pendiente > MAX_PENDIENTE:
            # No lee: se le corta antes de que la memoria del motor crezca sin límite. Puede volver a conectarse.
            self.cortar()

    def cortar(self) -> None:
        if self.cerrado:
            return
        self.cerrado = True
        try:
            self.writer.transport.abort()
        except (OSError, RuntimeError, AttributeError):
            pass

    def cerrar(self) -> None:
        """Cierra tras mandar lo que quede en el búfer."""
        if self.cerrado:
            return
        self.cerrado = True
        try:
            self.writer.close()
        except (OSError, RuntimeError):
            pass


class Mirador:
    """El socket de los observadores de una sesión y lo mínimo que hace falta recordar para su `snapshot`.

    `copiar` recibe cada mensaje que el agente manda al dueño; `decision` cada respuesta del dueño a un permiso;
    `herramienta_cliente_cerrada` el `tool_end` del dueño de una herramienta suya. `retenidos` da los eventos v2 que
    el transporte aún guarda sin mandar (antes del saludo del dueño o mientras se cierra un turno): sus filas ya están
    en la transcripción, así que el corte del historial va justo antes del primero."""

    def __init__(self, carpeta: Path, sesion_alfalfa: str, *, retenidos: Callable[[], Iterable] = tuple,
                 reloj: Callable = ahora):
        self.ruta = ruta_del_socket(carpeta)
        self.transcripcion = Path(carpeta) / (sesion_alfalfa + '.jsonl')
        self.sesion_alfalfa = sesion_alfalfa
        self.retenidos = retenidos
        self.reloj = reloj
        self.observadores: set[Observador] = set()
        self.ejecuciones: dict[str, dict] = {}          # run_id → su `run_started`, en orden
        self.herramientas: dict[str, dict] = {}         # tool_call_id → `tool_start` abierto
        self.aprobaciones: dict[str, dict] = {}         # id → `approval_request` pendiente
        self.mensajes: dict[str, list[dict]] = {}       # message_id → `message_start` y sus `message_delta`
        self.estados: dict[Any, dict] = {}              # el último de cada estado
        self.del_cliente: set[str] = set()              # herramientas del cliente (del dueño): no se copian
        self.servidor: asyncio.AbstractServer | None = None
        self._sock: socket.socket | None = None
        self._inodo: int | None = None
        self._arranque: asyncio.Task | None = None
        self._conexiones: set[asyncio.Task] = set()

    # -- socket ------------------------------------------------------------
    def abrir(self) -> bool:
        """Crea el socket (al volver ya existe) y empieza a aceptar en el bucle en marcha. False si no se pudo:
        ruta demasiado larga, otra cosa con ese nombre o sin bucle. La sesión sigue igual, sin observadores."""
        ruta = str(self.ruta)
        if len(os.fsencode(ruta)) > MAX_RUTA:
            return False
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return False
        try:
            if stat.S_ISSOCK(os.lstat(ruta).st_mode):
                os.unlink(ruta)              # de un proceso anterior que murió: el cerrojo ya es de éste
            else:
                return False                 # otra cosa con ese nombre: no se toca
        except FileNotFoundError:
            pass
        except OSError:
            return False
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.bind(ruta)
            os.chmod(ruta, 0o600)            # la carpeta ya es 0700: nadie más llega antes del chmod
            self._inodo = os.lstat(ruta).st_ino
            sock.listen(MAX_OBSERVADORES)
            sock.setblocking(False)
        except OSError:
            sock.close()
            self._borrar()
            return False
        self._sock = sock
        self._arranque = loop.create_task(self._servir(sock))
        return True

    async def _servir(self, sock: socket.socket) -> None:
        try:
            self.servidor = await asyncio.start_unix_server(self._conexion, sock=sock, limit=MAX_LINEA)
        except OSError:
            sock.close()
            self._borrar()

    def _conexion(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        tarea = asyncio.ensure_future(self._atender(reader, writer))
        self._conexiones.add(tarea)
        tarea.add_done_callback(self._conexiones.discard)

    def _borrar(self) -> None:
        """Borra el socket si sigue siendo el de este proceso."""
        try:
            if self._inodo is not None and os.lstat(self.ruta).st_ino == self._inodo:
                os.unlink(self.ruta)
        except OSError:
            pass
        self._inodo = None

    async def cerrar(self) -> None:
        """Al terminar la sesión: cada observador recibe lo que quede (el `close` del agente ya va copiado), se
        deja de aceptar y se borra el socket."""
        for obs in list(self.observadores):
            obs.cerrar()
        self.observadores.clear()
        if self._arranque is not None and not self._arranque.done():
            self._arranque.cancel()
        if self.servidor is not None:
            self.servidor.close()
        elif self._sock is not None:
            self._sock.close()
        self._borrar()
        if self._conexiones:
            await asyncio.wait(list(self._conexiones), timeout=2)
            for tarea in list(self._conexiones):
                tarea.cancel()

    # -- cada observador ---------------------------------------------------
    async def _atender(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        obs = Observador(writer, self.reloj)
        try:
            if len(self.observadores) >= MAX_OBSERVADORES:
                obs.error('alfalfa.too_many_observers', 'Ya hay ' + str(MAX_OBSERVADORES) + ' observadores.')
                return
            while not obs.cerrado:
                try:
                    linea = await (reader.readline() if obs.saludado
                                   else asyncio.wait_for(reader.readline(), ESPERA_SALUDO))
                except ValueError:
                    obs.error('message_too_large', 'Línea demasiado larga.')
                    return
                if not linea:
                    return                                   # EOF: se fue
                self._del_observador(obs, linea)
        except (asyncio.TimeoutError, ConnectionError, OSError):
            pass
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - un observador roto no tumba la sesión
            import traceback
            traceback.print_exc(file=sys.stderr)
        finally:
            self.observadores.discard(obs)
            obs.cerrar()

    def _del_observador(self, obs: Observador, linea: bytes) -> None:
        try:
            m = json.loads(linea.decode('utf-8'))
        except (ValueError, UnicodeError):
            return obs.error('invalid_message', 'La línea no es JSON en UTF-8.')
        if not isinstance(m, dict):
            return obs.error('invalid_message', 'Un mensaje PACAI es un objeto JSON.')
        ref = m.get('id') if isinstance(m.get('id'), str) else None
        if not obs.saludado:
            return self._saludo(obs, m, ref)
        errores = validar(m)
        if errores:
            return obs.error('invalid_message', errores[0], ref)
        seq = m['seq']
        if seq != obs.client_seq + 1:
            obs.error('seq_unrecoverable' if seq > obs.client_seq else 'invalid_message', 'seq fuera de orden.', ref)
            if seq > obs.client_seq:
                obs.cerrar()
            return
        obs.client_seq = seq
        tipo = m['type']
        if m['sender'] != 'client' or m['session'] != obs.emisor.sesion:
            return obs.error('invalid_message', 'Mensaje de otro emisor o de otra sesión.', ref)
        if tipo == 'close':
            return obs.cerrar()
        if tipo in DEL_OBSERVADOR:
            return
        if tipo == 'hello':
            return obs.error('invalid_message', 'Una sesión sólo se negocia una vez.', ref)
        obs.error('read_only', 'Un observador sólo mira: ' + tipo + ' sólo lo envía quien abrió la sesión.', ref)

    def _saludo(self, obs: Observador, m: dict, ref: str | None) -> None:
        tipo = m.get('type')
        if tipo in ('heartbeat', 'error'):
            return
        if tipo == 'close':
            return obs.cerrar()
        p = m.get('payload') if isinstance(m.get('payload'), dict) else {}
        versiones = p.get('versions')
        if tipo == 'hello' and isinstance(versiones, list) and VERSION not in versiones:
            if isinstance(m.get('session'), str):
                obs.emisor.sesion = m['session']
            obs.error('version_incompatible', 'Sólo se admite PACAI ' + VERSION + '.', ref)
            return obs.cerrar()
        errores = validar(m)
        if errores:
            return obs.error('invalid_message', errores[0], ref)
        if tipo != 'hello' or m['sender'] != 'client':
            return obs.error('invalid_message', str(tipo) + ' antes del hello del cliente.', ref)
        obs.emisor.sesion, obs.client_seq = m['session'], m['seq']
        if p.get('observe') is not True:
            obs.error('invalid_message', 'Este socket sólo admite observadores: el hello lleva observe: true. '
                      'La sesión la controla quien la abrió.', ref)
            return obs.cerrar()
        if 'resume_after_seq' in p:
            obs.error('retention_expired', 'Este agente no retiene sesiones de observador: conéctate de nuevo.', ref)
            return obs.cerrar()
        obs.perfiles = {x for x in p.get('profiles', []) if x in PERFILES}
        obs.extensiones = {x for x in p.get('extensions', []) if x in EXTENSIONES}
        self._presentar(obs, m['id'])

    def _presentar(self, obs: Observador, hola: str) -> None:
        """Saludo, `session_started` y `snapshot`, y desde ahí la copia: todo seguido, sin ceder el bucle, para que
        no se pierda ni se repita nada entre la foto y lo que viene después."""
        from .. import __version__
        agente = {'name': 'alfalfa', 'version': __version__}
        perfiles, extensiones = sorted(obs.perfiles), sorted(obs.extensiones)
        obs.propio('hello', {'versions': [VERSION], 'profiles': list(PERFILES), 'extensions': list(EXTENSIONES),
                             'implementation': agente})
        obs.propio('session_started', {'version': VERSION, 'profiles': perfiles, 'extensions': extensiones,
                                       'agent': agente, 'observe': True}, reply_to=hola)
        obs.propio('snapshot', self._foto(obs), reply_to=hola)
        obs.saludado = True
        self.observadores.add(obs)

    def _foto(self, obs: Observador) -> dict:
        runs = []
        for run_id, inicio in self.ejecuciones.items():
            madre = inicio['payload'].get('parent_run_id')
            runs.append({'run_id': run_id, 'status': 'active', **({'parent_run_id': madre} if madre else {})})
        ultimos = list(self.ejecuciones.values()) + list(self.herramientas.values()) + \
            [m for trozos in self.mensajes.values() for m in trozos] + list(self.estados.values())
        ultimos = sorted((m for m in ultimos if obs.ve(m['type'])), key=lambda m: m['seq'])
        foto = {'runs': runs, 'pending_approvals': [obs.suyo(m) for m in self.aprobaciones.values()],
                'last_messages': [obs.suyo(m) for m in ultimos]}
        if 'alfalfa' in obs.extensiones:
            foto['alfalfa'] = {'session_id': self.sesion_alfalfa, 'history_until': self.historial_hasta()}
        return foto

    def historial_hasta(self) -> float:
        """Hasta qué hora vale la transcripción en disco. Lo que el transporte aún retiene ya tiene su fila escrita
        pero no ha salido: el corte va justo antes del primero; si no retiene nada, en la última fila."""
        pendientes = [c.get('hora') for tipo, c in self.retenidos()
                      if tipo == 'evento' and isinstance(c.get('hora'), (int, float))]
        return min(pendientes) - 1e-6 if pendientes else ultima_hora(self.transcripcion)

    # -- lo que pasa en la sesión ------------------------------------------
    def copiar(self, m: dict) -> None:
        """Un mensaje que el agente acaba de mandar al dueño."""
        tipo, p = m.get('type'), m.get('payload') or {}
        if tipo in DEL_SALUDO or (tipo == 'error' and 'ref' in p):
            return
        if tipo == 'tool_start' and p.get('side') == 'client':
            self.del_cliente.add(m['id'])
            return
        if tipo == 'tool_end' and p.get('tool_call_id') in self.del_cliente:
            self.del_cliente.discard(p['tool_call_id'])
            return
        self._anotar(tipo, m, p)
        for obs in list(self.observadores):
            if obs.ve(tipo):
                obs.copia(m)

    def decision(self, peticion: str, decision: str, alcance: str | None = None, propio: dict | None = None) -> None:
        """El dueño contestó un permiso: cada observador lo sabe por un `approval_result` del agente con
        `relayed: true` (§6.1 de la propuesta)."""
        if self.aprobaciones.pop(peticion, None) is None:
            return
        payload: dict[str, Any] = {'decision': decision, 'relayed': True}
        if decision == 'approved' and alcance:
            payload['scope'] = alcance
        for obs in list(self.observadores):
            extra = {'alfalfa': propio} if propio and 'alfalfa' in obs.extensiones else {}
            obs.propio('approval_result', dict(payload, **extra), reply_to=peticion)

    def herramienta_cliente_cerrada(self, tool_call_id: str) -> None:
        self.del_cliente.discard(tool_call_id)

    def _anotar(self, tipo: str, m: dict, p: dict) -> None:
        if tipo == 'run_started':
            self.ejecuciones[m['id']] = m
        elif tipo == 'run_finished':
            self._olvidar_ejecucion(p.get('run_id'))
        elif tipo == 'tool_start':
            self.herramientas[m['id']] = m
        elif tipo == 'tool_end':
            tc = p.get('tool_call_id')
            self.herramientas.pop(tc, None)
            for ident in [i for i, a in self.aprobaciones.items() if a['payload'].get('tool_call_id') == tc]:
                del self.aprobaciones[ident]
        elif tipo == 'approval_request':
            self.aprobaciones[m['id']] = m
        elif tipo == 'approval_result':
            self.aprobaciones.pop(m.get('reply_to'), None)
        elif tipo == 'message_start':
            self.mensajes[m['id']] = [m]
        elif tipo == 'message_delta' and p.get('message_id') in self.mensajes:
            self.mensajes[p['message_id']].append(m)
        elif tipo == 'message_end':
            self.mensajes.pop(p.get('message_id'), None)
        elif tipo == 'usage':
            self.estados[('usage', p.get('run_id'), 'context_tokens' in p)] = m
        elif tipo in ('plan_updated', 'diff_updated'):
            self.estados[(tipo, p.get('run_id'))] = m
        elif tipo in ESTADOS:
            self.estados[tipo] = m

    def _olvidar_ejecucion(self, run_id: Any) -> None:
        self.ejecuciones.pop(run_id, None)
        for tc in [t for t, h in self.herramientas.items() if h['payload'].get('run_id') == run_id]:
            del self.herramientas[tc]
        for ident in [i for i, a in self.aprobaciones.items() if a['payload'].get('run_id') == run_id]:
            del self.aprobaciones[ident]
        for mid in [i for i, trozos in self.mensajes.items() if trozos[0]['payload'].get('run_id') == run_id]:
            del self.mensajes[mid]
        for clave in [k for k in self.estados if isinstance(k, tuple) and k[1] == run_id]:
            del self.estados[clave]
