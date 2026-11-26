"""`alfalfa --pacai`: el `Transport` de `--stdio` v2 hablando PACAI 0.3 por stdio (§5.1).

El motor sigue siendo el mismo (`stdio.run`): este transporte recibe sus eventos v2 y los traduce con `Traductor`, y
traduce a órdenes v2 lo que llega del cliente. stdout lleva sólo mensajes PACAI, uno por línea; los registros van
a stderr.

Lo que PACAI exige y v2 no hacía:

- saludo y negociación antes de nada (lo que el cliente encadena tras su `hello` vale; lo que el motor emite
  mientras tanto espera);
- una sola ejecución raíz activa: un `input` `new_run` que llega con una en curso espera a que termine;
- aprobaciones sin plazo por defecto; con `--caducidad-aprobacion`, pasado `expires` el agente declara `expired`;
- EOF o `close` del cliente, y la interrupción, niegan lo pendiente (eso ya lo hace `Transport.stop`), y el
  traductor lo cuenta con `approval_result` `cancelled`; las herramientas del cliente abiertas se cancelan;
- el trabajo que pide permisos fuera de un turno (guardar una prueba del navegador, una cola de encargos) necesita una
  ejecución: el cliente lo pide con un `input` de un bloque `structured` (`TRABAJOS`) y la ejecución dura lo que él.

Con la extensión `alfalfa`, el `payload.alfalfa` de un `input` admite `redirect: false` (un `steer` que entra en el
turno en curso sin cortar la respuesta, como un `mensaje` de v2), `echo: false` (el turno que abre no devuelve el
mensaje, como el encargo de la orden) y `continue: true` (reanudar sin mensaje, como `--reanudar` sin encargo).
"""
from __future__ import annotations

import asyncio
import copy
import json
import os
import sys
import traceback
from collections import deque
from datetime import datetime
from typing import Any, Callable

from .. import __version__
from ..stdio import CONTINUAR_ENCARGO, MAX_ID_EN_COLA, MAX_MENSAJE, Transport
from .esquema import validar
from .nombres import de_la_orden, respuesta_del_navegador
from .sobre import MAX_BYTES, Emisor, ahora, bytes_de_linea, codificar, nueva_sesion, prefijo_que_cabe
from .tipos import MESSAGE_TYPES, PROFILE_OF, SENDER_OF, VERSION
from .traductor import CADUCIDAD_APROBACION, Traductor
from .ui import ESQUEMA_ACCION, Catalogo, accion_para_el_modelo, errores_de

ARCHIVO_VISTAS = 'pacai-vistas.json'   # en la carpeta de la sesión

__all__ = ['TransportePacai', 'PERFILES', 'EXTENSIONES', 'COMANDOS', 'caducidad_de', 'ENTORNO_CADUCIDAD']

NOMBRE = 'alfalfa'
PERFILES = ('work', 'ui')
EXTENSIONES = ('alfalfa',)
# Variable de entorno con los segundos que espera una aprobación (por defecto no caduca: `CADUCIDAD_APROBACION`).
ENTORNO_CADUCIDAD = 'ALFALFA_CADUCIDAD_APROBACION'
CADUCIDAD_MAXIMA = 365 * 24 * 3600
# Órdenes del cliente propias de AlfalfaCode: tipo de la extensión → `tipo` de v2. Sus campos van en inglés
# (`nombres.ORDEN` los devuelve a v2).
COMANDOS = {
    'set_settings': 'ajustes', 'compact': 'compactar', 'set_context_mode': 'contexto',
    'continue_in_new_session': 'continuar_en_nueva', 'set_keep_awake': 'mantener_despierto',
    'set_budget': 'presupuesto', 'set_diagnostics': 'diagnosticos', 'set_swift_diagnostics': 'diagnosticos_swift',
    'set_signing': 'firma', 'resolve_learning': 'aprendizaje', 'review_code': 'revisar_codigo',
    'cancel_code_review': 'cancelar_revision_codigo', 'export_browser_test': 'exportar_prueba_navegador',
    'jobs_command': 'encargos', 'stop_process': 'detener_proceso', 'redirect': 'redirigir',
}
# Lo que el cliente pide con un `input` de un bloque `structured` (`schema` → orden de v2): trabajo fuera de un turno
# que puede pedir permisos. Si el motor lo pone en marcha, abre una ejecución raíz que dura lo que él.
TRABAJOS = {'alfalfa.export_browser_test': 'exportar_prueba_navegador', 'alfalfa.jobs_command': 'encargos'}


def caducidad_de(valor: Any = None) -> float | None:
    """Los segundos que espera una aprobación: `valor` (la opción), si no `ALFALFA_CADUCIDAD_APROBACION`; sin
    ninguno, None (no caduca). Un número positivo de segundos, como mucho un año; lo demás es un error que lo dice."""
    origen = '--caducidad-aprobacion'
    if valor is None:
        valor, origen = os.environ.get(ENTORNO_CADUCIDAD), ENTORNO_CADUCIDAD
    if valor is None or (isinstance(valor, str) and not valor.strip()):
        return CADUCIDAD_APROBACION
    try:
        segundos = float(valor)
    except (TypeError, ValueError):
        segundos = -1.0
    if not 0 < segundos <= CADUCIDAD_MAXIMA:
        raise ValueError(origen + ' necesita un número de segundos mayor que 0 y de hasta '
                         + str(CADUCIDAD_MAXIMA) + ' (un año); llegó «' + str(valor)[:40] + '».')
    return segundos


_DEL_ENTORNO = object()


class TransportePacai(Transport):
    def __init__(self, output, *, caducidad: Any = _DEL_ENTORNO, reloj: Callable[[], datetime] = ahora):
        super().__init__(output)
        self.caducidad = caducidad_de() if caducidad is _DEL_ENTORNO else caducidad
        self.reloj = reloj
        self.emisor = Emisor('agent', nueva_sesion(), reloj=reloj)
        self.t: Traductor | None = None
        self.empezado = False
        self.cerrado = False
        self.sin_sesion = False                          # la negociación falló: lo que llegue se descarta
        self.manager = None
        self.max_bytes = MAX_BYTES
        self.client_seq = 0
        self.vistos: dict[str, dict] = {}
        self._retenidos: list[tuple[str, dict]] = []     # eventos v2 que esperan al saludo o al cierre de un turno
        self._cerrando: asyncio.Future | None = None
        self._diferidos: dict[str, dict] = {}            # permisos v2 aún sin soltar (¿los resuelve la política?)
        self._temporizadores: dict[str, asyncio.TimerHandle] = {}
        self._ocupado = False                            # hay una entrada en manos del motor hasta que cierra su turno
        self._cola: deque[tuple[str, str]] = deque()     # `new_run` que esperan a la ejecución en curso
        self._ultima_entrada: str | None = None
        self._argumentos: tuple[str, Any] | None = None   # los de la herramienta cuyo evento de inicio viene ahora
        self._partida = None                             # diff_git.PuntoDePartida de la ejecución en curso
        self._trabajo: asyncio.Future | None = None      # el trabajo (`TRABAJOS`) cuya ejecución está abierta
        self._siguiente: str | None = None               # la entrada del turno que espera a que se cierre el anterior
        self._archivo_vistas = None                      # Path de las pantallas guardadas de la sesión (perfil ui)
        self._vistas_guardadas = 0                       # Vistas.revisiones() de lo último que se guardó

    # -- conexión con el motor ---------------------------------------------
    def conectar(self, manager) -> None:
        self.manager = manager

    def turno_empieza(self, prompt) -> None:
        """El motor va a empezar un turno: es la ejecución raíz que abre el `input` que lo trajo."""
        if not self.empezado or self.cerrado or self.t is None:
            return
        if self._trabajo is not None:
            self._fin_del_trabajo(self._trabajo)        # el motor lo paró para atender el mensaje (como en v2)
        entrada = getattr(prompt, 'id', None) or self._ultima_entrada
        if entrada is None:
            return
        if self._cerrando is not None:
            # El turno anterior aún se está cerrando (su diff): éste se abre después, en `antes_del_turno`. Pasa
            # cuando el motor ya tenía el mensaje (un `steer` que no llegó a leer, «Enviar ahora»).
            self._siguiente = entrada
            return
        self._abrir_turno(entrada)

    def _abrir_turno(self, entrada: str) -> None:
        self._ocupado = True
        self._enviar_todo(self.t.empezar_turno(entrada))

    def argumentos_herramienta(self, nombre, argumentos) -> None:
        self._argumentos = (nombre, argumentos)

    def _con_argumentos(self, tipo: str, campos: dict) -> dict:
        """El evento de inicio de una herramienta se lleva sus argumentos estructurados (`_argumentos`), que el
        motor dio justo antes; cualquier otro evento los descarta."""
        if tipo not in ('evento', 'subagente_evento') or self._argumentos is None:
            return campos
        (nombre, argumentos), self._argumentos = self._argumentos, None
        datos = campos.get('herramienta')
        es_inicio = (isinstance(datos, dict) and datos.get('fase') == 'inicio' and datos.get('nombre') == nombre
                     if tipo == 'evento'
                     else str(campos.get('texto') or '').startswith('Inicio ' + nombre + ' · '))
        return dict(campos, _argumentos=argumentos) if es_inicio and campos.get('rol') == 'tool' else campos

    async def antes_del_turno(self) -> None:
        """Con el perfil `work`, el punto de partida del diff de la ejecución con git (por si no hay instantáneas).

        Antes, si el turno anterior aún se estaba cerrando, se espera: su `run_finished` sale antes que el
        `run_started` de éste (§4.2) y su diff no se calcula con el punto de partida del nuevo."""
        if self._cerrando is not None:
            await asyncio.shield(self._cerrando)
        if self._siguiente is not None and not self.cerrado:
            entrada, self._siguiente = self._siguiente, None
            self._abrir_turno(entrada)
        self._olvidar_partida()
        if self.t is None or not self.t.trabajo or self.manager is None:
            return
        cwd = (getattr(self.manager, 'task', None) or {}).get('cwd')
        if not cwd:
            return
        from .diff_git import PuntoDePartida
        try:
            self._partida = await asyncio.to_thread(PuntoDePartida.tomar, cwd)
        except Exception:  # noqa: BLE001 - sin punto de partida sólo falta el diff
            traceback.print_exc(file=sys.stderr)

    def _olvidar_partida(self) -> None:
        if self._partida is not None:
            self._partida.olvidar()
            self._partida = None

    # -- salida: eventos v2 → mensajes PACAI -------------------------------
    def emit(self, tipo, **campos):
        if getattr(self, 'salida_rota', False) or self.cerrado:
            return
        campos = self._con_argumentos(tipo, self.clean(campos))
        if not self.empezado:
            if tipo == 'fin':
                self._fin_sin_saludo(campos)
            else:
                self._retenidos.append((tipo, campos))
            return
        if self._cerrando is not None and tipo != 'quitado_de_cola':
            # La respuesta a «Quitar» no es de la ejecución que se cierra: sale ya, para que la app no se quede en
            # «quitando…» mientras se calcula el diff del turno.
            self._retenidos.append((tipo, campos))
            return
        self._procesar(tipo, campos)

    def _procesar(self, tipo: str, campos: dict) -> None:
        if tipo == 'permiso':
            self._diferir_permiso(campos)
        elif tipo == 'permiso_resuelto' and str(campos.get('id')) in self._diferidos:
            # La política lo resolvió en el acto (sólo lectura, sesión cerrándose): el cliente nunca llegó a verlo.
            del self._diferidos[str(campos['id'])]
        elif tipo == 'turno_terminado':
            self._cerrando = asyncio.ensure_future(self._cerrar_turno(campos))
        elif tipo == 'error' and campos.get('motivo') == 'sin_vision':
            self._enviar_todo(self.t.evento(tipo, campos))
            self._liberar()                  # el turno no llegó a empezar: la siguiente entrada puede pasar
        elif tipo == 'fin':
            self._enviar_todo(self.t.evento(tipo, campos))
            self._cerrar_sesion()
        else:
            if tipo == 'sesion':
                self._abrir_vistas(campos.get('id'))
            self._enviar_todo(self.t.evento(tipo, campos))
        self._guardar_vistas()
        self._olvidar_resueltas()

    # -- perfil ui: las pantallas se guardan con la sesión -----------------
    def _abrir_vistas(self, ident: Any) -> None:
        """Al saber qué sesión es: recupera sus pantallas, para que al reanudarla sigan su revisión. Si la sesión
        cambia de id (continuada en otra), las que ya hay pasan a guardarse en la nueva."""
        if self.t is None or self.t.vistas is None or not isinstance(ident, str) or not ident \
                or '/' in ident or ident.startswith('.'):
            return
        from ..terminal import home_dir
        carpeta = home_dir() / 'sesiones' / ident
        if not carpeta.is_dir() or carpeta.is_symlink():
            return
        archivo = carpeta / ARCHIVO_VISTAS
        if archivo == self._archivo_vistas:
            return
        self._archivo_vistas = archivo
        if not self.t.vistas.vistas:
            try:
                self.t.vistas.cargar(json.loads(archivo.read_text(encoding='utf-8')))
            except (OSError, ValueError):
                pass                                     # sin pantallas guardadas (o ilegibles): se empieza de cero
        self._vistas_guardadas = self.t.vistas.revisiones()

    def _guardar_vistas(self) -> None:
        vistas = self.t.vistas if self.t is not None else None
        if vistas is None or self._archivo_vistas is None or vistas.revisiones() == self._vistas_guardadas:
            return
        temporal = self._archivo_vistas.with_suffix('.tmp')
        try:
            fd = os.open(temporal, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(vistas.a_datos(), f, ensure_ascii=False)
            os.replace(temporal, self._archivo_vistas)
            self._vistas_guardadas = vistas.revisiones()
        except OSError:
            pass                                         # sin disco sigue la sesión; sólo se pierde al reanudar

    def _enviar_todo(self, mensajes: list[dict]) -> None:
        for m in mensajes:
            self._enviar(m)

    def _enviar(self, m: dict) -> None:
        if getattr(self, 'salida_rota', False):
            return
        try:
            linea = codificar(m)
            if bytes_de_linea(linea) > self.max_bytes:
                m = self._acortar(m)
                linea = codificar(m)
            print(linea, file=self.output, flush=True)
        except (BrokenPipeError, ValueError, OSError):
            self.salida_rota = True
            return
        if m['type'] == 'approval_request':
            self._vigilar_caducidad(m['id'])

    def _acortar(self, m: dict) -> dict:
        """Recorta el texto más largo hasta que el mensaje cabe en `max_message_bytes` (§4.8); si ni así, un error."""
        if m['type'] in ('close', 'run_finished', 'alfalfa.closing'):
            cierre = self._acortar_cierre(m)
            if bytes_de_linea(codificar(cierre)) <= self.max_bytes:
                return cierre
        m = copy.deepcopy(m)              # el original lo guarda el traductor (el digest va con sus argumentos)
        for _ in range(60):
            cadenas: list[tuple[Any, Any]] = []

            def buscar(valor):
                for clave, hijo in (valor.items() if isinstance(valor, dict) else enumerate(valor)):
                    if isinstance(hijo, str) and len(hijo) > 1000 and clave not in ('id', 'type', 'digest'):
                        cadenas.append((valor, clave))
                    elif isinstance(hijo, (dict, list)):
                        buscar(hijo)
            buscar(m['payload'])
            if not cadenas:
                break
            contenedor, clave = max(cadenas, key=lambda par: len(par[0][par[1]]))
            contenedor[clave] = contenedor[clave][:len(contenedor[clave]) // 2] + '… [recortado]'
            if bytes_de_linea(codificar(m)) <= self.max_bytes:
                return m
        # No cabe ni recortado: sale un error con su misma `seq` (un hueco en la numeración sería otro error).
        aviso = {k: v for k, v in m.items() if k != 'reply_to'}
        aviso.update(type='error', payload={'code': 'message_too_large',
                                            'message': 'Un mensaje (' + m['type'] + ') superó max_message_bytes.'})
        return aviso

    def _acortar_cierre(self, m: dict) -> dict:
        """Conserva el resultado y su correlación; el texto ocupa sólo el espacio restante."""
        original = m['payload']
        metadatos = ('status', 'exit_code', 'tokens', 'turn', 'cost', 'turn_tokens', 'turn_cost')
        if m['type'] == 'close':
            campo = 'reason'
            extra = original.get('alfalfa', {})
            payload = {'alfalfa': {k: extra[k] for k in metadatos if k in extra}} if extra else {}
        elif m['type'] == 'run_finished':
            campo = 'detail'
            payload = {k: original[k] for k in ('run_id', 'outcome')}
        else:
            campo = 'summary'
            payload = {k: original[k] for k in ('run_id', *metadatos) if k in original}
        corto = dict(m, payload=payload)
        if campo in original:
            payload[campo] = ''
            espacio = self.max_bytes - bytes_de_linea(codificar(corto))
            payload[campo] = prefijo_que_cabe(original[campo], espacio)
        return corto

    # -- permisos y su caducidad (§4.5) ------------------------------------
    def _diferir_permiso(self, campos: dict) -> None:
        ident = str(campos.get('id'))
        self._diferidos[ident] = campos
        try:
            asyncio.get_running_loop().call_soon(self._soltar_permiso, ident)
        except RuntimeError:
            self._soltar_permiso(ident)

    def _soltar_permiso(self, ident: str) -> None:
        campos = self._diferidos.pop(ident, None)
        if campos is None or self.cerrado:
            return
        self.t.motivo_rechazo = None
        mensajes = self.t.permiso(campos)
        if mensajes is None:
            # Sin ejecución a la que atribuirlo (p. ej. guardar una prueba del navegador entre turnos), en cierre o
            # con los argumentos truncados: se niega.
            if ident in self.pending:
                self.resolve(ident, False, self.t.motivo_rechazo or 'Sin ejecución activa')
            return
        self._enviar_todo(mensajes)

    def _vigilar_caducidad(self, request_id: str) -> None:
        a = self.t.aprobacion(request_id)
        if a is None or a.expira is None:
            return                                       # sin `expires` no hay temporizador
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        espera = max(0.0, (a.expira - self.reloj()).total_seconds()) + 0.05
        self._temporizadores[request_id] = loop.call_later(espera, self._caducar, request_id)

    def _olvidar_resueltas(self) -> None:
        """Una petición ya resuelta (por el cliente, la interrupción, el cierre o el fin de su ejecución) no espera
        a su `expires`: su temporizador no debe seguir vivo."""
        for request_id in list(self._temporizadores):
            a = self.t.aprobacion(request_id) if self.t else None
            if a is None or a.estado != 'pending':
                self._temporizadores.pop(request_id).cancel()

    def _caducar(self, request_id: str) -> None:
        self._temporizadores.pop(request_id, None)
        a = self.t.aprobacion(request_id) if self.t else None
        if a is None or a.estado != 'pending' or self.cerrado or a.expira is None:
            return
        if self.reloj() < a.expira:                      # nunca antes de `expires`
            self._vigilar_caducidad(request_id)
            return
        self._enviar_todo(self.t.caducar(request_id))
        if a.v2id in self.pending:
            self.resolve(a.v2id, False, 'Sin respuesta a tiempo')

    # -- cierre de un turno ------------------------------------------------
    async def _cerrar_turno(self, campos: dict) -> None:
        try:
            diff = await asyncio.to_thread(self._diff_del_turno) if self.t.trabajo and self.t.hay_cambios else None
            if not diff and self.t.trabajo and self._partida is not None:
                diff = await asyncio.to_thread(self._partida.diff)        # sin instantáneas: el de git
            self._enviar_todo(self.t.cerrar_turno(campos, diff))
        except Exception:  # noqa: BLE001 - la ejecución se cierra pase lo que pase (§4.2)
            traceback.print_exc(file=sys.stderr)
            self._enviar_todo(self.t.cerrar_turno(campos))
        finally:
            self._olvidar_partida()
            self._cerrando = None
            self._liberar()
            retenidos, self._retenidos = self._retenidos, []
            for tipo, resto in retenidos:
                self.emit(tipo, **resto)

    def _diff_del_turno(self) -> str | None:
        """El diff unificado de lo que cambió el turno (el mismo que usa la revisión de código)."""
        try:
            from ..revision_codigo import diff_del_turno
            turnos = self.manager.task.get('turnos') or []
            return diff_del_turno(self.manager, turnos[-1])[0] if turnos else None
        except Exception:  # noqa: BLE001 - sin instantáneas o con demasiados archivos: no hay diff, no es un fallo
            return None

    def _cerrar_sesion(self) -> None:
        self.cerrado = True
        self._olvidar_partida()
        for temporizador in self._temporizadores.values():
            temporizador.cancel()
        self._temporizadores.clear()

    def _fin_sin_saludo(self, campos: dict) -> None:
        """El motor acabó sin que el cliente saludara: lo que se pueda contar (errores) y el cierre."""
        previo = Traductor(self.emisor, perfiles=(), extensiones=())
        for tipo, resto in self._retenidos:
            if tipo == 'error':
                self._enviar_todo(previo.evento('error', resto))
        self._retenidos.clear()
        self._enviar_todo(previo.evento('fin', campos))
        self._cerrar_sesion()

    # -- entrada: mensajes PACAI del cliente → órdenes v2 ------------------
    def _error(self, codigo: str, mensaje: str, ref: str | None = None) -> None:
        previo = self.t or Traductor(self.emisor)
        self._enviar(previo.error(codigo, mensaje, ref))

    def receive(self, line):
        if self.cerrado or self.sin_sesion:
            return
        try:
            m = json.loads(line)
        except ValueError:
            return self._error('invalid_message', 'La línea no es JSON.')
        if not isinstance(m, dict):
            return self._error('invalid_message', 'Un mensaje PACAI es un objeto JSON.')
        if self._sin_version_comun(m):
            return
        ref = m.get('id') if isinstance(m.get('id'), str) and len(m['id']) >= 8 else None
        previo = self.vistos.get(ref) if ref else None
        if previo is not None:
            if previo != m:
                self._error('invalid_message', 'Un id repetido con otro contenido.', ref)
            return                                        # retransmisión: se descarta (§2)
        seq = m.get('seq')
        if isinstance(seq, int) and not isinstance(seq, bool):
            # La `seq` cuenta todo lo que el cliente envía, también lo que luego se rechaza.
            if seq > self.client_seq + 1:
                self._error('seq_unrecoverable', 'Faltan mensajes del cliente.', ref)
                return self.stop()
            if seq <= self.client_seq:
                return self._error('invalid_message', 'seq repetida o hacia atrás.', ref)
            self.client_seq = seq
        errores = validar(m)
        if errores:
            return self._error('invalid_message', errores[0], ref)
        self.vistos[m['id']] = m
        if len(self.vistos) > 2000:
            self.vistos.pop(next(iter(self.vistos)))
        if m['sender'] != 'client':
            return self._error('invalid_message', 'Un mensaje del agente no puede venir del cliente.', ref)
        tipo = m['type']
        lado = SENDER_OF.get(tipo)
        if tipo in MESSAGE_TYPES and lado not in (None, 'client'):
            return self._error('invalid_message', tipo + ' sólo lo envía el agente.', ref)
        if not self.empezado and tipo not in ('hello', 'error', 'close', 'heartbeat'):
            # Antes de su `hello` el cliente sólo puede mandar error, close y heartbeat (§4.1). Lo que encadena justo
            # después del `hello` sí vale: el `hello` se negocia en el acto y lo siguiente ya llega con sesión.
            return self._error('invalid_message', tipo + ' antes del hello del cliente.', ref)
        if self.empezado and m['session'] != self.emisor.sesion:
            return self._error('invalid_message', 'La sesión no es la de este proceso.', ref)
        manejador = getattr(self, '_c_' + tipo, None) if tipo in MESSAGE_TYPES else None
        if manejador is not None:
            return manejador(m)
        if tipo in PROFILE_OF:
            return self._error('not_negotiated', 'El perfil ' + PROFILE_OF[tipo] + ' no está activo.', ref)
        if '.' in tipo:
            return self._extension(m)
        return self._error('invalid_message', 'Tipo desconocido: ' + tipo, ref)

    def _sin_version_comun(self, m: dict) -> bool:
        """Un `hello` de un cliente que no ofrece esta versión (p. ej. uno de la 0.2, con su sobre `"pacai": "0.2"`
        que el esquema de la 0.3 no admite) recibe `version_incompatible` y se cierra (§4.1), no `invalid_message`."""
        versiones = m.get('payload', {}).get('versions') if isinstance(m.get('payload'), dict) else None
        if (self.empezado or m.get('type') != 'hello' or m.get('sender') != 'client'
                or not isinstance(versiones, list) or VERSION in versiones):
            return False
        if isinstance(m.get('session'), str) and len(m['session']) >= 16:
            self.emisor.sesion = m['session']
        ref = m.get('id') if isinstance(m.get('id'), str) and len(m['id']) >= 8 else None
        self._error('version_incompatible', 'Sólo se admite PACAI ' + VERSION + '.', ref)
        self._negociacion_fallida()
        return True

    def _negociacion_fallida(self) -> None:
        """Sin sesión no se contesta nada más: lo encadenado tras el `hello` se descarta sin respuesta (§4.1)."""
        self.sin_sesion = True
        self.stop()

    def _c_heartbeat(self, m):
        pass

    def _c_error(self, m):
        pass                                              # el cliente avisa de algo suyo: no cierra la sesión

    def _c_close(self, m):
        self.stop()

    def _c_hello(self, m):
        p = m['payload']
        if self.empezado:
            return self._error('invalid_message', 'Una sesión sólo se negocia una vez.', m['id'])
        self.emisor.sesion = m['session']
        if 'resume_after_seq' in p:
            # La reanudación en red queda fuera: este proceso no retiene una sesión anterior.
            self._error('retention_expired', 'Este agente no retiene sesiones para reanudar.', m['id'])
            return self._negociacion_fallida()
        perfiles = [x for x in p.get('profiles', []) if x in PERFILES]
        extensiones = [x for x in p.get('extensions', []) if x in EXTENSIONES]
        limite = (p.get('limits') or {}).get('max_message_bytes')
        if isinstance(limite, int):
            self.max_bytes = min(limite, MAX_BYTES * 16)
        # Los argumentos van en el `tool_start` y otra vez en su `approval_request`, con el resto del mensaje:
        # una cuarta parte del máximo deja sitio a todo lo demás.
        max_argumentos = max(1024, self.max_bytes // 4)
        herramientas = [h['name'] for h in p.get('client_tools', []) if isinstance(h, dict) and h.get('name')]
        catalogo = None
        if 'ui' in perfiles:
            # Perfil `ui`: el catálogo llega en `render.ui`; sin él vale el base. Uno inválido deja la sesión sin `ui`.
            declaracion = (p.get('render') or {}).get('ui')
            fallos = errores_de('declaration', declaracion) if declaracion is not None else []
            if fallos:
                perfiles.remove('ui')
                self._error('invalid_message', 'render.ui no es válido; la sesión sigue sin el perfil ui: '
                            + '; '.join(fallos[:3]), m['id'])
            else:
                catalogo = Catalogo.de(declaracion)
        self.t = Traductor(self.emisor, perfiles=perfiles, extensiones=extensiones, herramientas_cliente=herramientas,
                           caducidad=self.caducidad, reloj=self.reloj, max_argumentos=max_argumentos,
                           max_bytes=self.max_bytes, ui=catalogo)
        self.t.al_rechazar_navegador = self._rechazar_navegador
        agente = {'name': NOMBRE, 'version': __version__}
        self._enviar(self.emisor.mensaje('hello', {'versions': [VERSION], 'profiles': list(PERFILES),
                                                   'extensions': list(EXTENSIONES), 'implementation': agente}))
        self._enviar(self.emisor.mensaje('session_started', {'version': VERSION, 'profiles': perfiles,
                                                             'extensions': extensiones, 'agent': agente},
                                         reply_to=m['id']))
        self.empezado = True
        retenidos, self._retenidos = self._retenidos, []
        for tipo, campos in retenidos:
            self.emit(tipo, **campos)

    def _propio(self, payload: dict) -> dict:
        """El `payload.alfalfa` de un mensaje del cliente, si se negoció la extensión; si no, nada."""
        datos = payload.get('alfalfa')
        return datos if isinstance(datos, dict) and self.t is not None and 'alfalfa' in self.t.extensiones else {}

    @staticmethod
    def _ruta_de(bloque: dict) -> str | None:
        """La ruta de un bloque `file` con `url` `file://` y ruta absoluta; None si no la tiene."""
        from urllib.parse import unquote, urlsplit
        url = bloque.get('url')
        if not isinstance(url, str) or not url.startswith('file://'):
            return None
        partes = urlsplit(url)
        ruta = unquote(partes.path)
        return ruta if partes.netloc in ('', 'localhost') and os.path.isabs(ruta) else None

    def _entrada_de(self, m: dict) -> tuple[str, list[str], list[str]] | None:
        """El texto, las imágenes y los archivos de los bloques de un `input`; None (y un error ya enviado) si no se
        puede. Un bloque `file` es una ruta del Mac (`url` `file://`): con `media_type` `image/*` va como imagen y si
        no, como archivo (PDF, texto, código…), igual que `imagenes` y `archivos` de v2."""
        textos, imagenes, archivos = [], [], []
        for bloque in m['payload']['blocks']:
            if bloque.get('type') == 'text':
                textos.append(bloque['text'])
            elif bloque.get('type') == 'file' and self._ruta_de(bloque) is not None:
                destino = imagenes if str(bloque.get('media_type') or '').startswith('image/') else archivos
                destino.append(self._ruta_de(bloque))
            else:
                self._error('invalid_message', 'Este agente sólo admite bloques text y bloques file con una url '
                            'file:// absoluta en un input.', m['id'])
                return None
        texto = '\n\n'.join(t for t in textos if t)
        # Con adjuntos el texto puede ir vacío: el motor pone «Mira la imagen adjunta.» o «Lee el archivo adjunto.».
        if (not texto.strip() and not (imagenes or archivos)) or len(texto) > MAX_MENSAJE \
                or len(m['id']) > MAX_ID_EN_COLA:
            self._error('invalid_message', 'El input debe ser texto no vacío (o llevar adjuntos) de hasta '
                        + str(MAX_MENSAJE) + ' caracteres, con un id de hasta ' + str(MAX_ID_EN_COLA) + '.', m['id'])
            return None
        return texto, imagenes, archivos

    def _entregar(self, ident: str, texto: str, imagenes=(), archivos=(), redirigir: bool = False,
                  eco: bool = True) -> None:
        previo = self._ocupado
        self._ocupado = True
        self._ultima_entrada = ident
        orden: dict[str, Any] = {'tipo': 'mensaje', 'texto': texto, 'id': ident}
        if imagenes:
            orden['imagenes'] = list(imagenes)
        if archivos:
            orden['archivos'] = list(archivos)
        super().receive(json.dumps(orden))
        entregado = next((x for x in self.inbox if getattr(x, 'id', None) == ident), None)
        if entregado is None:
            self._ocupado = previo           # el motor no lo admitió (ya dijo por qué): no hay ejecución que esperar
            return
        if not eco:
            entregado.sin_eco = True
        if redirigir:
            super().receive('{"tipo":"redirigir"}')

    def _liberar(self) -> None:
        """Termina la ejecución raíz: pasa la siguiente entrada en cola, si la hay."""
        self._ocupado = False
        if self._cola and not self.stopped:
            ident, (texto, imagenes, archivos, eco) = self._cola.popleft()
            self._entregar(ident, texto, imagenes, archivos, eco=eco)

    def _c_input(self, m):
        p = m['payload']
        propio = self._propio(p)
        if any(b.get('type') == 'structured' and b.get('schema') == ESQUEMA_ACCION for b in p['blocks']):
            m = self._accion_de_pantalla(m)
            if m is None:
                return
            p = m['payload']
        if any(b.get('type') == 'structured' for b in p['blocks']):
            return self._pedir_trabajo(m)
        if propio.get('continue') is True:
            if p['blocks']:
                return self._error('invalid_message', 'Un input con alfalfa.continue no lleva bloques.', m['id'])
            entrada, eco = (CONTINUAR_ENCARGO, [], []), False
        else:
            entrada, eco = self._entrada_de(m), propio.get('echo') is not False
            if entrada is None:
                return
        texto, imagenes, archivos = entrada
        if p.get('mode') == 'steer':
            run_id = p.get('run_id')
            if run_id == self.t.raiz and self.t.raiz is not None:
                # Por defecto corta la respuesta en curso para leerlo ya («Redirigir»); con `redirect: false` entra en
                # la siguiente inferencia del turno, como un `mensaje` de v2 con un turno en marcha.
                return self._entregar(m['id'], texto, imagenes, archivos, redirigir=propio.get('redirect') is not False,
                                      eco=eco)
            ejecucion = self.t.ejecuciones.get(run_id)
            if ejecucion is not None and ejecucion.activa and ejecucion.agente is not None:
                return self._al_subagente(ejecucion.agente, texto)
            if ejecucion is None:
                return self._error('unknown_run', 'No existe esa ejecución.', m['id'])
            # Ya terminó: se trata como new_run (§4.2).
        if (self._ocupado and self._trabajo is None) or self._cerrando is not None or self._cola:
            self._cola.append((m['id'], (texto, imagenes, archivos, eco)))
        else:
            # Con un trabajo en marcha el mensaje pasa: el motor lo para antes de abrir el turno, como en v2.
            self._entregar(m['id'], texto, imagenes, archivos, eco=eco)

    # -- perfil `ui` ---------------------------------------------------------
    def manual_ui(self, web: bool = False) -> str:
        """La parte del prompt de sistema con el catálogo del cliente; vacía sin el perfil `ui`. `web`: el motor le
        ofrece al modelo web_search y read_web."""
        vistas = self.t.vistas if self.t is not None else None
        return vistas.catalogo.manual(web=web) if vistas is not None else ''

    def _accion_de_pantalla(self, m: dict) -> dict | None:
        """Un `input` con `pacai.ui.action/1`: el mismo `input` con un solo bloque de texto para el modelo (None y un
        error enviado si no vale)."""
        if self.t.vistas is None:
            return self._error('not_negotiated', 'Las acciones de pantalla necesitan el perfil ui.', m['id'])
        bloques = m['payload']['blocks']
        acciones = [b for b in bloques if b.get('type') == 'structured']
        if len(acciones) != 1 or any(b.get('type') != 'text' for b in bloques if b not in acciones):
            return self._error('invalid_message', 'Una acción de pantalla va en un solo bloque structured, con bloques '
                               'text opcionales.', m['id'])
        fallos = errores_de('action', acciones[0].get('data'))
        if fallos:
            return self._error('invalid_message', 'Acción de pantalla inválida: ' + '; '.join(fallos[:3]), m['id'])
        data = acciones[0]['data']
        vistas = self.t.vistas
        pantalla = vistas.resumen_lang(data['view']) if vistas.catalogo.lang and data['view'] in vistas.vistas else ''
        texto = accion_para_el_modelo([b['text'] for b in bloques if b.get('type') == 'text'], data,
                                      vistas.rev(data['view']), pantalla)
        return dict(m, payload=dict(m['payload'], blocks=[{'type': 'text', 'text': texto}]))

    # -- trabajo fuera de un turno (`TRABAJOS`) ------------------------------
    def _pedir_trabajo(self, m: dict) -> None:
        """Un `input` con un bloque `structured` de `TRABAJOS`: la orden llega al motor y, si pone el trabajo en marcha
        sin otra ejecución abierta, ese `input` abre la ejecución en la que pide sus permisos."""
        bloques = m['payload']['blocks']
        esquema = bloques[0].get('schema') if len(bloques) == 1 else None
        if esquema not in TRABAJOS or 'alfalfa' not in self.t.extensiones:
            return self._error('invalid_message', 'Un input con un bloque structured sólo vale para '
                               + ', '.join(sorted(TRABAJOS)) + ', solo y con la extensión alfalfa.', m['id'])
        try:
            orden = de_la_orden(bloques[0]['data'], esquema.partition('.')[2])
        except ValueError as exc:
            return self._error('invalid_message', str(exc), m['id'])
        libre = not self._ocupado and self._cerrando is None and self.t.raiz is None
        super().receive(json.dumps(dict(orden, tipo=TRABAJOS[esquema])))
        tarea = (self.exportacion if esquema == 'alfalfa.export_browser_test'
                 else getattr(self.cola_encargos, 'worker', None))
        if not libre or tarea is None or tarea.done():
            return                                         # no empezó nada (el motor ya dijo por qué), o no cabe
        self._enviar_todo(self.t.empezar_turno(m['id']))
        self._ocupado, self._trabajo = True, tarea
        tarea.add_done_callback(self._fin_del_trabajo)

    def _fin_del_trabajo(self, tarea: asyncio.Future) -> None:
        if self._trabajo is not tarea:
            return
        self._trabajo = None
        if not self.cerrado and self.t is not None:
            fallo = None if tarea.cancelled() or not tarea.done() else tarea.exception()
            resultado = 'interrupted' if tarea.cancelled() or not tarea.done() else 'failed' if fallo else 'success'
            self._enviar_todo(self.t.terminar_trabajo(resultado, str(fallo or '') or None))
            self._olvidar_resueltas()
        self._liberar()

    def _al_subagente(self, agente: str, texto: str) -> None:
        if self.manager is None:
            return
        self.en_segundo_plano(self.manager.subagentes.enviar({'agent': agente, 'message': texto, 'background': True}),
                              'Enviar al subagente')

    def _c_interrupt(self, m):
        run_id = m['payload']['run_id']
        ejecucion = self.t.ejecuciones.get(run_id)
        if ejecucion is None or not ejecucion.activa:
            return self._error('unknown_run', 'Esa ejecución no está en curso.', m['id'])
        if ejecucion.agente is None:
            return super().receive('{"tipo":"interrumpir"}')
        if self.manager is not None:
            self.en_segundo_plano(self.manager.subagentes.cerrar({'agent': ejecucion.agente}), 'Detener el subagente')

    def _c_approval_result(self, m):
        p, ref = m['payload'], m['reply_to']
        propio = self._propio(p).get('scope')
        a = self.t.aprobacion(ref)
        if a is None or (a.estado not in ('pending', 'expired', 'cancelled')):
            return self._error('unknown_approval', 'No hay una petición pendiente con ese reply_to.', m['id'])
        if a.estado in ('expired', 'cancelled') or (a.expira is not None and self.reloj() >= a.expira):
            return                                        # tarde: no cuenta como aprobación (§4.5)
        decision, alcance = p['decision'], p.get('scope')
        if decision not in ('approved', 'denied'):
            return self._error('invalid_message', 'Sólo el agente declara expired.', m['id'])
        if decision == 'approved' and alcance not in a.scopes:
            return self._error('invalid_message', 'El alcance ' + str(alcance) + ' no se ofreció.', m['id'])
        # Lo que v2 tiene y el núcleo no, en `payload.alfalfa.scope`: «No volver a preguntar» (`always`, si la
        # petición traía `prefix`) y el permiso de un sitio para todo el proyecto (`project`).
        if decision == 'approved' and propio is not None and not ((propio == 'always' and a.siempre)
                                                                  or (propio == 'project' and a.sitio)):
            return self._error('invalid_message', 'El alcance alfalfa.scope ' + str(propio)[:40] + ' no se ofreció.',
                               m['id'])
        orden: dict[str, Any] = {'tipo': 'permiso', 'id': a.v2id, 'aprobado': decision == 'approved'}
        if decision == 'approved' and propio == 'always':
            orden['siempre'] = True
        elif decision == 'approved' and propio == 'project':
            orden['sitio'] = 'proyecto'
        elif decision == 'approved' and alcance == 'session':
            orden['sitio' if a.sitio else 'sesion'] = 'sesion' if a.sitio else True
        elif decision == 'approved' and a.sitio:
            orden['sitio'] = 'una_vez'
        a.estado = 'approved' if decision == 'approved' else 'denied'
        self._olvidar_resueltas()
        super().receive(json.dumps(orden))

    def _c_tool_end(self, m):
        p = m['payload']
        tc = p['tool_call_id']
        if tc in self.t.clientes_cancelados:
            return                     # el agente ya la canceló: lo que llega tarde se ignora sin error (§4.4)
        if m.get('reply_to') != tc:
            return self._error('invalid_message', 'El tool_end del cliente responde con reply_to al tool_start.',
                               m['id'])
        h = self.t.fin_de_herramienta_cliente(tc)
        if h is None:
            return self._error('unknown_tool_call', 'No hay una herramienta del cliente abierta con ese id.', m['id'])
        respuesta = self._respuesta_del_navegador(p)
        futuro = self.navegador_pendientes.get(h.v2id)
        if futuro is not None and not futuro.done():     # una respuesta tardía (ya caducó) se descarta sin ruido
            futuro.set_result({'tipo': 'navegador_respuesta', 'id': h.v2id, **respuesta})

    @staticmethod
    def _respuesta_del_navegador(p: dict) -> dict:
        """`navegador_respuesta` de v2 a partir del `tool_end` del cliente: un bloque `structured` lleva el cuerpo.

        También con `status: error`: la app contesta así cuando espera un permiso de la persona (`permission`:
        formulario, contraseña, descarga o sitio) o cuando falla con resultado parcial, y el motor los necesita."""
        bloques = p.get('output') or []
        datos = next((respuesta_del_navegador(b['data']) for b in bloques if b.get('type') == 'structured'), None)
        texto = '\n'.join(b['text'] for b in bloques if b.get('type') == 'text' and b.get('text'))
        error = p.get('summary') or texto or ('cancelada' if p['status'] == 'cancelled' else 'error')
        if isinstance(datos, dict):
            respuesta = {**datos, 'ok': p['status'] == 'success'}
            if not respuesta['ok'] and 'error' not in respuesta and 'permiso' not in respuesta:
                respuesta['error'] = str(error)[:1000]
            return respuesta
        if p['status'] == 'success':
            return {'ok': True, 'resultado': {'texto': texto} if texto else {}}
        return {'ok': False, 'error': str(error)[:1000]}

    def _rechazar_navegador(self, ident: str, texto: str) -> None:
        futuro = self.navegador_pendientes.get(ident)
        if futuro is not None and not futuro.done():
            futuro.set_result({'tipo': 'navegador_respuesta', 'id': ident, 'ok': False, 'error': texto})

    def _extension(self, m: dict) -> None:
        espacio, _, nombre = m['type'].partition('.')
        if espacio not in self.t.extensiones:
            return self._error('not_negotiated', 'La extensión ' + espacio + ' no está activa.', m['id'])
        if espacio != 'alfalfa':
            return self._error('invalid_message', 'Extensión desconocida: ' + espacio, m['id'])
        p = m['payload']
        if nombre == 'dequeue':
            return self._quitar_de_cola(p.get('id'))
        if nombre == 'send_now':
            return self._enviar_ahora(p.get('id'))
        v2 = COMANDOS.get(nombre)
        if v2 is None:
            return self._error('invalid_message', 'Orden desconocida: ' + m['type'], m['id'])
        try:
            orden = de_la_orden(p, nombre)
        except ValueError as exc:
            return self._error('invalid_message', str(exc), m['id'])
        super().receive(json.dumps(dict(orden, tipo=v2)))

    def _quitar_de_cola(self, ident) -> None:
        """Quitar una entrada que aún espera su ejecución: en la cola de PACAI o, ya entregada, en la del motor."""
        for n, (propio, _) in enumerate(self._cola):
            if propio == ident:
                del self._cola[n]
                return self.emit('quitado_de_cola', id=ident, quitado=True)
        entregada = self._en_el_motor(ident)
        super().receive(json.dumps({'tipo': 'quitar_de_cola', 'id': ident}))
        if entregada and not self._en_el_motor(ident) and not self.inbox and self._sin_ejecucion():
            # Se entregó para abrir una ejecución que ya no empezará (se quitó mientras el motor compactaba, por
            # ejemplo): sin esto la entrada seguía «ocupando» el motor y todo lo que llegaba después esperaba en
            # la cola para siempre.
            self._liberar()

    def _en_el_motor(self, ident) -> bool:
        return any(getattr(mensaje, 'id', None) == ident for mensaje in self.inbox)

    def _sin_ejecucion(self) -> bool:
        """Ni ejecución raíz abierta, ni trabajo, ni un turno cerrándose o esperando a abrirse."""
        return (self.t is not None and self.t.raiz is None and self._trabajo is None and self._cerrando is None
                and self._siguiente is None)

    def _enviar_ahora(self, ident) -> None:
        for n, (propio, (texto, imagenes, archivos, eco)) in enumerate(self._cola):
            if propio == ident:
                del self._cola[n]
                self._entregar(propio, texto, imagenes, archivos, eco=eco)
                return super().receive(json.dumps({'tipo': 'enviar_ahora', 'id': ident}))
        super().receive(json.dumps({'tipo': 'enviar_ahora', 'id': ident}))
