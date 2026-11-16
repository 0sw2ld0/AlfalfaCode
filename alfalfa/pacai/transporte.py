"""`alfalfa --pacai`: el `Transport` de `--stdio` v2 hablando PACAI 0.3 por stdio (§5.1).

El motor sigue siendo el mismo (`stdio.run`): este transporte recibe sus eventos v2 y los traduce con `Traductor`, y
traduce a órdenes v2 lo que llega del cliente. stdout lleva sólo mensajes PACAI, uno por línea; los registros van
a stderr.

Lo que PACAI exige y v2 no hacía:

- saludo y negociación antes de nada: lo que el motor emite mientras tanto espera;
- una sola ejecución raíz activa: un `input` `new_run` que llega con una en curso espera a que termine;
- aprobaciones con `expires`: sin respuesta a tiempo, el propio agente declara `expired` y no se ejecuta;
- EOF o `close` del cliente, y la interrupción, niegan lo pendiente (eso ya lo hace `Transport.stop`).
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
from ..stdio import MAX_ID_EN_COLA, MAX_MENSAJE, Transport
from .esquema import validar
from .nombres import de_la_orden
from .sobre import Emisor, ahora, nueva_sesion
from .tipos import MESSAGE_TYPES, PROFILE_OF, SENDER_OF, VERSION
from .traductor import CADUCIDAD_APROBACION, Traductor

__all__ = ['TransportePacai', 'PERFILES', 'EXTENSIONES', 'COMANDOS', 'caducidad_de', 'ENTORNO_CADUCIDAD']

NOMBRE = 'alfalfa'
PERFILES = ('work',)
EXTENSIONES = ('alfalfa',)
MAX_BYTES = 1024 * 1024
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
    'jobs_command': 'encargos', 'stop_process': 'detener_proceso',
}


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

    # -- conexión con el motor ---------------------------------------------
    def conectar(self, manager) -> None:
        self.manager = manager

    def turno_empieza(self, prompt) -> None:
        """El motor va a empezar un turno: es la ejecución raíz que abre el `input` que lo trajo."""
        if not self.empezado or self.cerrado or self.t is None:
            return
        entrada = getattr(prompt, 'id', None) or self._ultima_entrada
        if entrada is None:
            return
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
        """Con el perfil `work`, el punto de partida del diff de la ejecución con git (por si no hay instantáneas)."""
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
        if self._cerrando is not None:
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
            self._enviar_todo(self.t.evento(tipo, campos))
        self._olvidar_resueltas()

    def _enviar_todo(self, mensajes: list[dict]) -> None:
        for m in mensajes:
            self._enviar(m)

    def _enviar(self, m: dict) -> None:
        if getattr(self, 'salida_rota', False):
            return
        try:
            linea = json.dumps(m, ensure_ascii=False, separators=(',', ':'), default=str)
            if len(linea.encode('utf-8')) > self.max_bytes:
                m = self._acortar(m)
                linea = json.dumps(m, ensure_ascii=False, separators=(',', ':'), default=str)
            print(linea, file=self.output, flush=True)
        except (BrokenPipeError, ValueError, OSError):
            self.salida_rota = True
            return
        if m['type'] == 'approval_request':
            self._vigilar_caducidad(m['id'])

    def _acortar(self, m: dict) -> dict:
        """Recorta el texto más largo hasta que el mensaje cabe en `max_message_bytes` (§4.8); si ni así, un error."""
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
            if len(json.dumps(m, ensure_ascii=False, separators=(',', ':'), default=str).encode()) <= self.max_bytes:
                return m
        # No cabe ni recortado: sale un error con su misma `seq` (un hueco en la numeración sería otro error).
        aviso = {k: v for k, v in m.items() if k != 'reply_to'}
        aviso.update(type='error', payload={'code': 'message_too_large',
                                            'message': 'Un mensaje (' + m['type'] + ') superó max_message_bytes.'})
        return aviso

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
        self.t = Traductor(self.emisor, perfiles=perfiles, extensiones=extensiones, herramientas_cliente=herramientas,
                           caducidad=self.caducidad, reloj=self.reloj, max_argumentos=max_argumentos)
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

    def _texto_de(self, m: dict) -> str | None:
        """El texto de los bloques de un `input`; None (y un error ya enviado) si no se puede."""
        bloques = m['payload']['blocks']
        if any(b.get('type') != 'text' for b in bloques):
            self._error('invalid_message', 'Este agente sólo admite bloques de texto en un input.', m['id'])
            return None
        texto = '\n\n'.join(b['text'] for b in bloques if b.get('text'))
        if not texto.strip() or len(texto) > MAX_MENSAJE or len(m['id']) > MAX_ID_EN_COLA:
            self._error('invalid_message', 'El input debe ser texto no vacío de hasta ' + str(MAX_MENSAJE)
                        + ' caracteres, con un id de hasta ' + str(MAX_ID_EN_COLA) + '.', m['id'])
            return None
        return texto

    def _entregar(self, ident: str, texto: str, redirigir: bool = False) -> None:
        self._ocupado = True
        self._ultima_entrada = ident
        super().receive(json.dumps({'tipo': 'mensaje', 'texto': texto, 'id': ident}))
        if redirigir:
            super().receive('{"tipo":"redirigir"}')

    def _liberar(self) -> None:
        """Termina la ejecución raíz: pasa la siguiente entrada en cola, si la hay."""
        self._ocupado = False
        if self._cola and not self.stopped:
            ident, texto = self._cola.popleft()
            self._entregar(ident, texto)

    def _c_input(self, m):
        texto = self._texto_de(m)
        if texto is None:
            return
        p = m['payload']
        if p.get('mode') == 'steer':
            run_id = p.get('run_id')
            if run_id == self.t.raiz and self.t.raiz is not None:
                return self._entregar(m['id'], texto, redirigir=True)
            ejecucion = self.t.ejecuciones.get(run_id)
            if ejecucion is not None and ejecucion.activa and ejecucion.agente is not None:
                return self._al_subagente(ejecucion.agente, texto)
            if ejecucion is None:
                return self._error('unknown_run', 'No existe esa ejecución.', m['id'])
            # Ya terminó: se trata como new_run (§4.2).
        if self._ocupado or self._cerrando is not None or self._cola:
            self._cola.append((m['id'], texto))
        else:
            self._entregar(m['id'], texto)

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
        orden: dict[str, Any] = {'tipo': 'permiso', 'id': a.v2id, 'aprobado': decision == 'approved'}
        if decision == 'approved' and alcance == 'session':
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
        """`navegador_respuesta` de v2 a partir del `tool_end` del cliente: un bloque `structured` lleva el cuerpo."""
        bloques = p.get('output') or []
        datos = next((b['data'] for b in bloques if b.get('type') == 'structured'), None)
        texto = '\n'.join(b['text'] for b in bloques if b.get('type') == 'text' and b.get('text'))
        if p['status'] == 'success':
            if isinstance(datos, dict):
                return {'ok': True, **datos}
            return {'ok': True, 'resultado': {'texto': texto} if texto else {}}
        error = p.get('summary') or texto or ('cancelada' if p['status'] == 'cancelled' else 'error')
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
        super().receive(json.dumps(dict(de_la_orden(p), tipo=v2)))

    def _quitar_de_cola(self, ident) -> None:
        """Quitar una entrada que aún espera su ejecución: en la cola de PACAI o, ya entregada, en la del motor."""
        for n, (propio, _) in enumerate(self._cola):
            if propio == ident:
                del self._cola[n]
                return self.emit('quitado_de_cola', id=ident, quitado=True)
        super().receive(json.dumps({'tipo': 'quitar_de_cola', 'id': ident}))

    def _enviar_ahora(self, ident) -> None:
        for n, (propio, texto) in enumerate(self._cola):
            if propio == ident:
                del self._cola[n]
                self._entregar(propio, texto)
                return super().receive(json.dumps({'tipo': 'enviar_ahora', 'id': ident}))
        super().receive(json.dumps({'tipo': 'enviar_ahora', 'id': ident}))
