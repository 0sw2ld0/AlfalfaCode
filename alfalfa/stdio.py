"""Transporte JSONL v2. Un lector atiende permisos, mensajes y cancelación concurrentemente."""
import asyncio
import json
import os
import re
import signal
import sqlite3
import sys
import time
from contextlib import redirect_stdout
from pathlib import Path
from uuid import uuid4

from .equipo import ErrorDeEquipo
from .permisos.terminal import solo_lectura

VERSION = 2
MAX_MENSAJE = 20000
# El texto de un mensaje que sólo trae imágenes (también lo usa la app de Mac).
SOLO_IMAGENES = 'Mira la imagen adjunta.'
SOLO_ARCHIVOS = 'Lee el archivo adjunto.'
# Campos opcionales del evento `permiso` (política on-request); los clientes v2
# anteriores los ignoran.
PERMISO_EXTRA = ('motivo', 'motivos', 'justificacion', 'regla', 'intento', 'reintento_de', 'red',
                 'sin_sandbox', 'fallo_sandbox', 'mcp', 'app', 'cwd', 'timeout_seconds', 'para_sesion',
                 # Guardar memoria o una habilidad aprendida (motivo «aprendizaje»): lo que cambia.
                 'diff', 'sitio',
                 # «No volver a preguntar por órdenes que empiecen por …» (permisos/bajo_demanda.py).
                 'prefijo')


# Largo máximo del id que la app da a un mensaje en cola.
MAX_ID_EN_COLA = 100


class EnCola(str):
    """Un mensaje en cola con el id que le dio la app (`{"tipo":"mensaje","id":…}`). Sigue
    siendo su texto: el resto del motor lo trata igual que a un mensaje sin id."""

    def __new__(cls, texto, ident):
        mensaje = super().__new__(cls, texto)
        mensaje.id = ident
        return mensaje


# Lo que devuelve next_message cuando toca compactar entre turnos.
COMPACTAR = object()
# … y cuando toca «Seguir en una sesión nueva» (aviso de tarifa).
CONTINUAR = object()


class Transport:
    def __init__(self, output):
        self.output = output
        self.pending = {}
        self.worker = None
        self.stopped = False
        self.interrupted = False
        # Mensajes aceptados que el motor aún no ha incorporado a su historial.
        self.inbox = []
        # Ids de todos los mensajes con id aceptados: el que ya no está en `inbox` entró.
        self.ids_vistos = set()
        self.wake = asyncio.Event()
        # Credenciales declaradas para servidores MCP (mcp_cliente): se redactan igual.
        self.secretos = set()
        # {"tipo":"detener_proceso","id":"p1"|"todos"}: lo conecta run() con el gestor.
        self.stop_process = None
        # {"tipo":"mantener_despierto","activo":bool}: el interruptor de Ajustes, ya.
        self.keep_awake = None
        # {"tipo":"diagnosticos","activo":bool}: el de «Ver los errores al editar», desde la siguiente edición.
        self.diagnostics = None
        # {"tipo":"diagnosticos_swift","activo":bool}: el de «Diagnósticos de Swift», desde la siguiente edición.
        self.swift_diagnostics = None
        # {"tipo":"firma","activo":bool}: el de «Firmar los commits», desde el siguiente git commit.
        self.signature = None
        # Permisos que admiten «en esta sesión» (`para_sesion`).
        self.for_session = set()
        self.for_site = set()
        # Permisos que admiten «No volver a preguntar» (`prefijo`).
        self.for_always = set()
        # {"tipo":"compactar"} con un turno en marcha: el motor compacta antes de su
        # siguiente inferencia. True, o el texto de las instrucciones del usuario.
        self.compact_pending = False
        # Sin turno en marcha se compacta ya (como /compact de Claude Code): el bucle
        # lo recoge en next_message. Mismo valor que compact_pending.
        self.compact_now = False
        # {"tipo":"contexto","modo":"ahorro"|"completo"}: lo conecta run() con el gestor. Vale ya.
        self.context_mode = None
        # Ajustes ▸ Presupuesto: cambia los topes de la sesión en marcha.
        self.budget = None
        # {"tipo":"continuar_en_nueva"}: entre turnos compacta y abre una sesión nueva con el
        # resumen; con un turno en marcha espera a que termine (no corta una herramienta a medias).
        self.continue_new = False
        # Lo marca TerminalManager.run mientras dura un turno.
        self.in_turn = False
        # «Redirigir»: el modelo deja la respuesta que estaba pensando y lee ya los mensajes.
        self.redireccion = asyncio.Event()
        # «Enviar ahora» corta el turno en marcha sin cerrar la sesión.
        self.turno_cortado = False
        self.compacting = False
        # Modelo y esfuerzo pedidos por el cliente para el próximo turno.
        self.pending_settings = {}
        # Peticiones al navegador de la app (alfalfa/navegador.py) que esperan respuesta.
        self.navegador_pendientes = {}
        # {"tipo":"aprendizaje","id":…,"aplicar":bool}: la tarjeta de una propuesta de la revisión.
        self.learning = None
        # El repaso de aprendizaje del último turno, que corre después de `turno_terminado`.
        self.repaso = None
        self.revisar_codigo = None
        self.revision_codigo = None
        self.exportar_prueba = None
        self.exportacion = None
        self.encargos = None
        self.cola_encargos = None

    def clean(self, value):
        from . import proveedores
        if isinstance(value, str):
            for other in sorted(set(self.secretos) | set(proveedores.secretos()), key=len, reverse=True):
                value = value.replace(other, '[REDACTADO]')
            return value
        if isinstance(value, dict):
            return {k: self.clean(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self.clean(v) for v in value]
        return value

    def emit(self, tipo, **fields):
        if getattr(self, 'salida_rota', False):
            return
        try:
            print(json.dumps(self.clean(dict(version=VERSION, tipo=tipo, **fields)), ensure_ascii=False),
                  file=self.output, flush=True)
        except (BrokenPipeError, ValueError, OSError):
            # La app ya no lee (murió o cerró el pipe): no hay a quién contarlo. Antes la excepción
            # cortaba el cierre a medias y el motor quedaba huérfano.
            self.salida_rota = True

    def resolve(self, ident, approved, reason, session=False, site=None, always=False):
        """`session`: el usuario aprobó para el resto de la sesión (sólo si la petición lo ofrecía).
        `always`: «No volver a preguntar por órdenes que empiecen por …» (sólo con `prefijo`)."""
        future = self.pending.pop(ident)
        self.emit('permiso_resuelto', id=ident, aprobado=approved, motivo=reason,
                  **({'siempre': True} if approved and always else {'sesion': True} if approved and session else {}),
                  **({'sitio': site} if approved and site else {}))
        if not future.done():
            future.origen_equipo = {'Respuesta del cliente': 'cliente_stdio', 'solo_lectura': 'solo_lectura',
                                    'Sesión interrumpida': 'interrumpido'}.get(reason, 'sin_respuesta')
            future.set_result(site if approved and site else 'siempre' if approved and always
                              else 'sesion' if approved and session else approved)

    @property
    def exportando(self):
        return self.exportacion is not None and not self.exportacion.done()

    def pedir_exportacion(self, ident, ruta):
        if self.exportar_prueba is None:
            self.emit('error', texto='La propuesta ya no está disponible en el motor de esta sesión.')
        elif self.exportando:
            self.emit('error', texto='Ya se está guardando una prueba de navegador.')
        else:
            self.exportacion = asyncio.create_task(self._exportar(ident, ruta))

    async def _exportar(self, ident, ruta):
        try:
            guardada = await self.exportar_prueba(ident, ruta)
        except ValueError as exc:
            self.emit('error', texto=str(exc))
        except OSError:
            self.emit('error', texto='No se pudo crear la prueba dentro de tests/e2e del proyecto.')
        else:
            self.emit('evento', rol='system', hora=time.time(), texto=f'Prueba guardada en {guardada}. No se ejecutó.')

    async def cancelar_exportacion(self):
        tarea, self.exportacion = self.exportacion, None
        if tarea is not None and not tarea.done():
            tarea.cancel()
            await asyncio.gather(tarea, return_exceptions=True)

    def stop(self):
        if self.stopped:
            return
        self.stopped = True
        if self.cola_encargos is not None and self.cola_encargos.activa:
            self.cola_encargos.worker.cancel()
        if self.revision_codigo is not None and not self.revision_codigo.done():
            self.revision_codigo.cancel()
        if self.exportando:
            self.exportacion.cancel()
        self.wake.set()
        for ident in list(self.pending):
            self.resolve(ident, False, 'Sesión interrumpida')
        if self.worker and not self.worker.done():
            self.interrupted = True
            self.worker.cancel()

    def compact(self, instructions=None):
        """Pedido de compactar: ya si no hay turno; si lo hay, antes de la siguiente respuesta."""
        from .compactacion import MAX_INSTRUCCIONES
        if instructions is not None and (not isinstance(instructions, str) or len(instructions) > MAX_INSTRUCCIONES):
            raise ValueError()
        pedido = instructions.strip() if isinstance(instructions, str) and instructions.strip() else True
        if self.compacting or self.compact_now:
            self.emit('evento', rol='system', hora=time.time(), texto='Ya se está compactando la conversación.')
        elif self.in_turn:
            self.compact_pending = pedido
            self.emit('evento', rol='system', hora=time.time(),
                      texto='Compactación pedida: se hará antes de la siguiente respuesta del modelo.')
        else:
            self.compact_now = pedido
            self.wake.set()

    def take_compact(self):
        pending, self.compact_pending = self.compact_pending, False
        return pending

    def interrumpir_turno(self):
        """Corta el turno en marcha, como Esc en Codex y Claude Code; la sesión sigue abierta. Entre
        turnos corta el repaso de aprendizaje, si está en marcha."""
        if self.cola_encargos is not None and self.cola_encargos.activa:
            asyncio.create_task(self.cola_encargos.detener())
            return True
        if not self.in_turn and self.exportando:
            self.exportacion.cancel()
            return True
        if not self.in_turn and self.revision_codigo is not None and not self.revision_codigo.done():
            self.revision_codigo.cancel()
            return True
        if not self.in_turn and self.repaso is not None and not self.repaso.done():
            self.repaso.cancel()
            return True
        if self.stopped or not self.in_turn or not self.worker or self.worker.done():
            return False
        self.turno_cortado = True
        for ident in list(self.pending):
            self.resolve(ident, False, 'Turno interrumpido')
        self.worker.cancel()
        return True

    def enviar_ahora(self, text, ident=None):
        """«Enviar ahora» de un mensaje en cola: corta el turno y el mensaje abre el siguiente.
        Con `ident` (el id de la app) va justo ese mensaje; si ya no está en la cola (el modelo
        ya lo leyó) no se corta nada ni se vuelve a enviar."""
        if ident is not None:
            if not isinstance(ident, str) or not ident or len(ident) > MAX_ID_EN_COLA:
                raise ValueError()
            n = next((n for n, m in enumerate(self.inbox) if getattr(m, 'id', None) == ident), None)
            if n is None:
                return
            self.inbox.insert(0, self.inbox.pop(n))
            self.interrumpir_turno()
            self.wake.set()
            return
        if not isinstance(text, str) or not text.strip():
            raise ValueError()
        if text in self.inbox:
            # Con varios en cola, éste pasa delante.
            self.inbox.remove(text)
            self.inbox.insert(0, text)
        if self.inbox:
            # Si ya se entregó dentro del turno no hay nada que cortar por él.
            self.interrumpir_turno()
            self.wake.set()

    def redirigir(self):
        """«Redirigir» (steer de Codex): el modelo lee ya los mensajes en cola, sin cortar el turno."""
        if self.in_turn and self.inbox:
            self.redireccion.set()

    def quitar_de_cola(self, ident):
        """Quitar (o devolver al cuadro para editar) un mensaje en cola. Sólo si aún no entró en
        el contexto del modelo; la respuesta `quitado_de_cola` dice si se quitó."""
        if not isinstance(ident, str) or not ident or len(ident) > MAX_ID_EN_COLA:
            raise ValueError()
        for n, mensaje in enumerate(self.inbox):
            if getattr(mensaje, 'id', None) == ident:
                del self.inbox[n]
                if not self.inbox:
                    # Un «Redirigir» pedido por él ya no tiene nada que meter en el turno.
                    self.redireccion.clear()
                self.emit('quitado_de_cola', id=ident, quitado=True)
                return
        # `entregado`: el modelo ya lo tiene; `desconocido`: nunca llegó a la cola.
        self.emit('quitado_de_cola', id=ident, quitado=False,
                  motivo='entregado' if ident in self.ids_vistos else 'desconocido')

    def take_messages(self):
        messages, self.inbox = self.inbox, []
        return messages

    async def next_message(self):
        """Espera el mensaje que abre un turno nuevo; None si la sesión se cierra.

        Una compactación pedida entre turnos sale antes que los mensajes, como COMPACTAR
        con sus instrucciones: (COMPACTAR, texto o None)."""
        while not self.stopped:
            if self.continue_new:
                self.continue_new = False
                return (CONTINUAR, None)
            if self.compact_now:
                pedido, self.compact_now = self.compact_now, False
                return (COMPACTAR, pedido if isinstance(pedido, str) else None)
            if self.inbox:
                return self.inbox.pop(0)
            self.wake.clear()
            await self.wake.wait()
        return None

    def message(self, text, images=(), ident=None, files=()):
        """Un mensaje del usuario. `images`: rutas absolutas que se adjuntan como en Codex;
        `files`: las de PDF, texto, código, Word o Excel (alfalfa.adjuntos).
        `ident`: el id que le da la app para poder quitarlo de la cola; sin él, como siempre."""
        from . import adjuntos, imagenes
        if ident is not None and not (isinstance(ident, str) and 0 < len(ident) <= MAX_ID_EN_COLA):
            self.emit('error', texto='`id` tiene que ser texto de hasta 100 caracteres.')
            return
        if images and not (isinstance(images, list) and all(isinstance(r, str) for r in images)):
            self.emit('error', texto='`imagenes` tiene que ser una lista de rutas.')
            return
        if files and not (isinstance(files, list) and all(isinstance(r, str) for r in files)):
            self.emit('error', texto='`archivos` tiene que ser una lista de rutas.')
            return
        if (images or files) and isinstance(text, str) and not text.strip():
            text = SOLO_IMAGENES if images else SOLO_ARCHIVOS
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_MENSAJE:
            self.emit('error', texto='El mensaje debe ser texto no vacío de hasta 20000 caracteres.')
            return
        try:
            text = adjuntos.con_etiquetas(imagenes.con_etiquetas(text, list(images)), list(files))
        except (imagenes.ImagenInvalida, adjuntos.ArchivoInvalido) as exc:
            self.emit('error', texto=str(exc))
            return
        if self.stopped:
            self.emit('error', texto='La sesión se está cerrando; el mensaje no se entregó.')
        else:
            if ident is not None:
                text = EnCola(text, ident)
                self.ids_vistos.add(ident)
            self.inbox.append(text)
            self.wake.set()

    def settings(self, model, effort, mode=None):
        """Modelo y esfuerzo para los turnos siguientes, como /model en Codex. El turno en
        marcha sigue con los suyos; el cambio se aplica al empezar el próximo."""
        from .terminal import EFFORTS
        if model is not None and (not isinstance(model, str) or not re.fullmatch(r'[\w.:/-]{1,200}', model)):
            raise ValueError()
        if effort is not None and effort not in EFFORTS:
            raise ValueError()
        from .permisos.modos import MODOS
        if mode is not None and mode not in MODOS:
            raise ValueError()
        if model is None and effort is None and mode is None:
            raise ValueError()
        self.pending_settings.update({k: v for k, v in (('model', model), ('effort', effort), ('modo', mode))
                                      if v is not None})

    async def pedir_navegador(self, accion, campos, tiempo):
        """Una petición al navegador propio de la app; devuelve su `navegador_respuesta`."""
        ident = uuid4().hex
        future = asyncio.get_running_loop().create_future()
        self.navegador_pendientes[ident] = future
        self.emit('navegador', id=ident, accion=accion, **campos)
        try:
            return await asyncio.wait_for(future, tiempo)
        except asyncio.TimeoutError:
            raise ValueError('El navegador de la app no respondió a tiempo.') from None
        finally:
            self.navegador_pendientes.pop(ident, None)
            if accion == 'relevo':
                # Correlacionado: timeout, interrupción y respuestas tardías no dejan la app bloqueada.
                self.emit('navegador', id=ident, accion='cancelar_relevo')

    def take_settings(self):
        cambios, self.pending_settings = self.pending_settings, {}
        return cambios

    def receive(self, line):
        try:
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError()
            if value.get('tipo') == 'cancelar':
                self.stop()
            elif value.get('tipo') == 'mensaje':
                self.message(value.get('texto'), value.get('imagenes') or [], value.get('id'),
                             value.get('archivos') or [])
            elif value.get('tipo') == 'enviar_ahora':
                self.enviar_ahora(value.get('texto'), value.get('id'))
            elif value.get('tipo') == 'redirigir':
                self.redirigir()
            elif value.get('tipo') == 'quitar_de_cola':
                self.quitar_de_cola(value.get('id'))
            elif value.get('tipo') == 'interrumpir':
                self.interrumpir_turno()
            elif value.get('tipo') == 'ajustes':
                self.settings(value.get('modelo'), value.get('esfuerzo'), value.get('modo'))
            elif value.get('tipo') == 'compactar':
                self.compact(value.get('instrucciones'))
            elif value.get('tipo') == 'contexto':
                from .compactacion import MODOS_CONTEXTO
                if value.get('modo') not in MODOS_CONTEXTO or self.context_mode is None:
                    raise ValueError()
                self.context_mode(value['modo'])
            elif value.get('tipo') == 'continuar_en_nueva':
                if self.compacting:
                    self.emit('evento', rol='system', hora=time.time(),
                              texto='Espera a que termine la compactación en curso.')
                elif not self.continue_new:
                    self.continue_new = True
                    if self.in_turn:
                        # Cortarlo dejaría a medias la herramienta que esté en marcha: se espera.
                        self.emit('evento', rol='system', hora=time.time(),
                                  texto='Seguiré en una sesión nueva cuando termine este turno; '
                                        'detenlo si no quieres esperar.')
                    self.wake.set()
            elif value.get('tipo') == 'mantener_despierto':
                activo = value.get('activo')
                if not isinstance(activo, bool) or self.keep_awake is None:
                    raise ValueError()
                self.keep_awake(activo)
            elif value.get('tipo') == 'presupuesto':
                # Ajustes de la app: {sesion, mes} en dólares; null o 0 quitan el tope, sin la clave no cambia.
                if self.budget is None or not any(k in value for k in ('sesion', 'mes')):
                    raise ValueError()
                from .presupuesto import importe
                self.budget(**{k: importe(value[k]) for k in ('sesion', 'mes') if k in value})
            elif value.get('tipo') == 'diagnosticos':
                activo = value.get('activo')
                if not isinstance(activo, bool) or self.diagnostics is None:
                    raise ValueError()
                self.diagnostics(activo)
            elif value.get('tipo') == 'diagnosticos_swift':
                activo = value.get('activo')
                if not isinstance(activo, bool) or self.swift_diagnostics is None:
                    raise ValueError()
                self.swift_diagnostics(activo)
            elif value.get('tipo') == 'firma':
                activo = value.get('activo')
                if not isinstance(activo, bool) or self.signature is None:
                    raise ValueError()
                self.signature(activo)
            elif value.get('tipo') == 'aprendizaje':
                ident, aplicar = value.get('id'), value.get('aplicar')
                if not isinstance(ident, str) or not isinstance(aplicar, bool) or self.learning is None:
                    raise ValueError()
                self.learning(ident, aplicar)
            elif value.get('tipo') == 'revisar_codigo':
                ident, modelo = value.get('turno'), value.get('modelo')
                if not isinstance(ident, str) or not isinstance(modelo, str) or self.revisar_codigo is None:
                    raise ValueError()
                self.revisar_codigo(ident, modelo)
            elif value.get('tipo') == 'cancelar_revision_codigo':
                if self.revision_codigo is not None and not self.revision_codigo.done():
                    self.revision_codigo.cancel()
            elif value.get('tipo') == 'exportar_prueba_navegador':
                ident, ruta = value.get('id'), value.get('ruta')
                if (not isinstance(ident, str) or not re.fullmatch(r'[0-9a-f]{32}', ident)
                        or not isinstance(ruta, str) or len(ruta) > 100):
                    raise ValueError()
                self.pedir_exportacion(ident, ruta)
            elif value.get('tipo') == 'encargos':
                if self.encargos is None:
                    raise ValueError()
                self.encargos(value)
            elif value.get('tipo') == 'detener_proceso':
                ident = value.get('id')
                if not isinstance(ident, str) or self.stop_process is None:
                    raise ValueError()
                self.stop_process(ident)
            elif value.get('tipo') == 'navegador_respuesta':
                # Una respuesta que llega tarde (la petición ya caducó) se descarta sin ruido.
                future = self.navegador_pendientes.get(value.get('id'))
                if future is not None and not future.done():
                    future.set_result(value)
            elif value.get('tipo') == 'permiso':
                ident = value.get('id')
                if not isinstance(ident, str) or ident not in self.pending:
                    raise ValueError()
                approved = value.get('aprobado') is True
                site = value.get('sitio')
                if site is not None and (ident not in self.for_site or site not in ('una_vez', 'sesion', 'proyecto')):
                    raise ValueError()
                session = value.get('sesion') is True and ident in self.for_session
                always = value.get('siempre') is True and ident in self.for_always
                if value.get('siempre') is True and not always:
                    raise ValueError()
                self.for_session.discard(ident)
                self.for_site.discard(ident)
                self.for_always.discard(ident)
                self.resolve(ident, approved, 'Respuesta del cliente', session=session, site=site, always=always)
            else:
                raise ValueError()
        except (ValueError, TypeError):
            self.emit('error', texto='Mensaje inválido o id de permiso desconocido o repetido.')

    def approval(self, root, auto):
        transport = self

        class Approval:
            denied = 0

            async def __call__(self, args):
                ident = uuid4().hex
                unsandboxed = args.get('sin_sandbox')
                extra = {key: args[key] for key in PERMISO_EXTRA if args.get(key)}
                transport.emit('permiso', id=ident, argv=args['argv'], proposito=args['purpose'], **extra)
                future = asyncio.get_running_loop().create_future()
                transport.pending[ident] = future
                if args.get('para_sesion'):
                    transport.for_session.add(ident)
                if args.get('prefijo'):
                    transport.for_always.add(ident)
                if isinstance(args.get('sitio'), dict):
                    transport.for_site.add(ident)
                if transport.stopped:
                    transport.resolve(ident, False, 'Sesión interrumpida')
                # Las excepciones on-request (motivo) siempre esperan al cliente.
                elif auto and not unsandboxed and not args.get('motivo') and solo_lectura(
                        args['argv'], root, Path(root) / args['cwd']
                        if isinstance(args.get('cwd'), str) and args['cwd'] not in ('', '.') else None):
                    transport.resolve(ident, True, 'solo_lectura')
                try:
                    approved = await future
                    from .auditoria_equipo import marcar_respuesta
                    marcar_respuesta(getattr(future, 'origen_equipo', 'sin_respuesta'))
                finally:
                    transport.for_session.discard(ident)
                    transport.for_site.discard(ident)
                    transport.for_always.discard(ident)
                    if ident in transport.pending:
                        transport.resolve(ident, False, 'Sesión interrumpida' if transport.stopped
                                          else 'Sin respuesta a tiempo')
                if not approved:
                    self.denied += 1
                    raise ValueError('Comando rechazado; no se ejecutó.')
                return approved if approved in ('una_vez', 'sesion', 'proyecto', 'siempre') else None

        return Approval()


# Segundos para cerrar bien tras el fin de stdin, una señal o perder al padre; después se
# sale a la fuerza (la sesión queda «interrumpida», reanudable).
GRACIA_CIERRE = 3.0
# Ya fuera del turno, lo que puede tardar cerrar bien (procesos en segundo plano, MCP,
# diagnósticos) antes de salir a la fuerza.
GRACIA_LIMPIEZA = 15.0


class CierreSeguro:
    """Que el motor nunca quede huérfano.

    Pasó con la app cerrada: el motor esperaba un permiso, no le llegó el fin de stdin (otro
    proceso conservaba el pipe abierto) e ignoró SIGINT y SIGTERM porque `stop()` ya se había
    llamado y la cancelación del turno no acababa. Sólo murió con SIGKILL, con la sesión en
    `running`. Ahora:

    - el fin de stdin, la primera señal o perder al padre paran la sesión como siempre (los
      permisos pendientes se niegan) y programan una salida forzada a los `gracia` segundos;
    - una segunda señal sale ya;
    - la salida forzada guarda la sesión como `interrupted` si seguía en marcha y termina
      el proceso aunque algo se haya quedado colgado.
    """

    def __init__(self, transport, sesion, gracia=GRACIA_CIERRE, padre=None, limpieza=GRACIA_LIMPIEZA):
        self.transport = transport
        self.sesion = sesion  # función: el TerminalManager o None
        self.gracia = gracia
        self.limpieza = limpieza
        self.padre = os.getppid() if padre is None else padre
        self.parando = False
        self.forzado = False
        self._plazo = None
        self._hilo = None

    def parar(self):
        if self.parando:
            self.transport.stop()
            return
        self.parando = True
        self._programar(self.gracia)
        # Después de programar la salida forzada: si parar falla a medias, se sale igual.
        self.transport.stop()

    def _programar(self, segundos):
        import threading
        self.terminado()
        try:
            self._plazo = asyncio.get_running_loop().call_later(segundos, self.forzar)
        except RuntimeError:
            self._plazo = None
        # Por si el bucle se queda bloqueado: un hilo aparte, un poco más tarde.
        self._hilo = threading.Timer(segundos * 2, self.forzar)
        self._hilo.daemon = True
        self._hilo.start()

    def limpiando(self):
        """El turno ya cedió: cerrar bien puede tardar algo más (procesos, MCP) sin forzar nada."""
        if self.parando and not self.forzado:
            self._programar(self.limpieza)

    def senal(self):
        if self.parando:
            self.forzar()
        else:
            self.parar()

    async def vigilar_padre(self, cada=1.0):
        """Si la app muere sin cerrar stdin, el padre pasa a ser launchd: se para igual."""
        while True:
            await asyncio.sleep(cada)
            if os.getppid() != self.padre:
                self.parar()
                return

    def terminado(self):
        """El cierre normal acabó a tiempo: nada que forzar."""
        if self._plazo is not None:
            self._plazo.cancel()
        if self._hilo is not None:
            self._hilo.cancel()

    def forzar(self):
        if self.forzado:
            return
        self.forzado = True
        manager = self.sesion()
        try:
            if manager is not None:
                if manager.task.get('status') in (None, 'created', 'running'):
                    manager.task.update(status='interrupted',
                                        summary='Sesión interrumpida al cerrarse la app; puedes reanudarla.')
                manager.save()
                manager.close()
        except Exception:  # noqa: BLE001 - se sale igual: lo importante es no quedar huérfano
            pass
        try:
            self.transport.emit('fin', estado='interrupted', resumen='Cierre forzado del motor.', codigo_salida=130)
        except Exception:  # noqa: BLE001 - stdout puede estar cerrado
            pass
        os._exit(130)


async def run(args, transport):
    from .terminal import TerminalManager, diagnostics, keep_awake, signature, swift_diagnostics
    manager = None
    reader_task = None
    pipe = None
    vigia = None
    code = 1
    loop = asyncio.get_running_loop()
    cierre = CierreSeguro(transport, lambda: manager)
    try:
        from .terminal import resolve_fork
        resolve_fork(args)
        cola = getattr(args, 'cola_encargos', None)
        esperar = ((bool(getattr(args, 'esperar', False)) and bool(args.reanudar))
                   or cola is not None) and not args.encargo
        manager = TerminalManager(args.encargo or ('Cola de encargos' if cola is not None else None),
                                  resume=args.reanudar, model=args.modelo,
                                  effort=args.esfuerzo, auto=args.auto, transport=transport,
                                  read_only=getattr(args, 'solo_lectura', False),
                                  compact_limit=getattr(args, 'compactar_en', None), mode=getattr(args, 'modo', None),
                                  wait=esperar, parent=getattr(args, 'padre', None), keep_awake=keep_awake(args),
                                  navegador=bool(getattr(args, 'navegador', False)),
                                  motor_navegador=getattr(args, 'motor_navegador', None),
                                  perfil_cuybrowser=getattr(args, 'perfil_cuybrowser', 'alfalfa'),
                                  origen_cuybrowser=getattr(args, 'origen_cuybrowser', None),
                                  budget=getattr(args, 'presupuesto', None),
                                  monthly_budget=getattr(args, 'presupuesto_mes', None),
                                  diagnosticos=diagnostics(args), firma=signature(args),
                                  diagnosticos_swift=swift_diagnostics(args))
        if getattr(args, 'titulo', None) and not args.reanudar:
            manager.save()
            marcar(manager.task['id'], 'titulo', args.titulo)
        if getattr(args, 'contexto', None):
            manager.elegir_contexto(args.contexto)
        if esperar:
            code = 0  # Abrir y cerrar sin turno no es un fallo.
        transport.emit('sesion', id=manager.task['id'], cwd=manager.task['cwd'],
                       modelo=manager.task['model'], esfuerzo=manager.task['effort'], modo=manager.task['modo'],
                       contexto=manager.task.get('contexto_modo') or 'ahorro',
                       # Lo ya gastado: al reanudar, el panel lo enseña sin esperar al primer turno.
                       tokens=manager.task.get('total_tokens') or 0,
                       **{k: v for k, v in gasto(manager.task).items() if k == 'coste'},
                       **({'bifurcada_de': manager.task['bifurcada_de']} if manager.task.get('bifurcada_de') else {}),
                       **({'continuada_de': manager.task['continuada_de']['sesion']}
                          if isinstance(manager.task.get('continuada_de'), dict) else {}))
        manager.anunciar_equipo()
        # Los topes y lo gastado en la sesión y en el mes, para el panel y los Ajustes.
        topes = manager.presupuesto.para_el_cliente()
        if topes:
            transport.emit('presupuesto', **topes)
        # Al reanudar, la app vuelve a enseñar el plan del agente.
        if isinstance(manager.task.get('plan_pasos'), list) and manager.task['plan_pasos']:
            transport.emit('plan', pasos=[{'paso': p.get('step', ''), 'estado': p.get('status', 'pending')}
                                          for p in manager.task['plan_pasos'] if isinstance(p, dict)])
        # Al reanudar, el panel enseña ya el último contexto medido.
        if isinstance(manager.task.get('context_tokens'), int) and isinstance(manager.task.get('context_limit'), int):
            from .terminal import desglose_de
            transport.emit('contexto', tokens=manager.task['context_tokens'], limite=manager.task['context_limit'],
                           **desglose_de(manager.task))
        reader = asyncio.StreamReader(limit=1024 * 1024)
        pipe, _ = await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), sys.stdin)

        async def consume():
            while True:
                try:
                    line = await reader.readline()
                except ValueError:
                    transport.emit('error', texto='Línea demasiado larga.')
                    continue
                if not line:
                    cierre.parar()
                    return
                try:
                    transport.receive(line.decode('utf-8'))
                except UnicodeError:
                    transport.emit('error', texto='Se requiere UTF-8.')

        def detener_proceso(ident):
            async def detener():
                try:
                    if ident == 'todos':
                        await manager.fondo.detener_todos()
                    else:
                        await manager.fondo.detener(ident)
                except ValueError as exc:
                    transport.emit('error', texto=str(exc))
            asyncio.ensure_future(detener())

        transport.stop_process = detener_proceso
        transport.keep_awake = lambda activo: manager.cola_encargos.cambiar('despierto', activo)
        transport.signature = lambda activo: manager.cola_encargos.cambiar('firma', activo)
        # En solo lectura no hay diagnósticos, pero el interruptor de la app se acepta igual.
        transport.diagnostics = lambda activo: manager.cola_encargos.cambiar('diagnosticos', activo)
        transport.swift_diagnostics = lambda activo: manager.cola_encargos.cambiar('diagnosticos', activo,
                                                                                    metodo='cambiar_swift')

        def cambiar_contexto(modo):
            try:
                manager.cambiar_contexto(modo)
            except ValueError as exc:
                transport.emit('error', texto=str(exc))

        transport.context_mode = cambiar_contexto
        transport.budget = manager.cambiar_presupuesto
        transport.learning = manager.aprendizaje.resolver
        transport.cola_encargos = manager.cola_encargos
        if manager.exportacion_navegador is not None:
            transport.exportar_prueba = manager.exportacion_navegador.guardar

        def encargos(valor):
            accion = valor.get('accion')
            if accion in ('preparar', 'iniciar') and (transport.in_turn or transport.compacting or transport.inbox
                    or transport.exportando
                    or transport.revision_codigo is not None and not transport.revision_codigo.done()):
                transport.emit('error', texto='Espera a que termine el trabajo en marcha antes de iniciar la cola.')
                return
            try:
                if accion == 'preparar':
                    manager.aplicar_ajustes(transport.take_settings())
                    manager.cola_encargos.preparar(valor.get('encargos'), valor.get('modo', 'serie'),
                                                    valor.get('paralelos', 2))
                elif accion == 'iniciar':
                    manager.cola_encargos.iniciar()
                elif accion == 'pausar':
                    manager.cola_encargos.pausar()
                elif accion == 'detener':
                    asyncio.create_task(manager.cola_encargos.detener())
                elif accion == 'cancelar':
                    manager.cola_encargos.cancelar()
                else:
                    raise ValueError('Acción de cola desconocida.')
            except ValueError as exc:
                transport.emit('error', texto=str(exc))

        transport.encargos = encargos
        manager.cola_encargos.avisar()
        if cola:
            from .encargos import leer_archivo
            manager.cola_encargos.preparar(*leer_archivo(cola))
            manager.cola_encargos.iniciar()

        def pedir_revision(ident, modelo):
            if (transport.in_turn or transport.compacting or transport.inbox
                    or manager.cola_encargos.activa or transport.exportando
                    or transport.revision_codigo is not None and not transport.revision_codigo.done()):
                transport.emit('error', texto='Espera a que termine el trabajo en marcha antes de revisar el cierre.')
                return

            async def revisar():
                await _cancelar_repaso(transport)
                try:
                    await manager.revision_codigo.ejecutar(ident, modelo)
                except ValueError as exc:
                    transport.emit('error', texto=str(exc))

            transport.revision_codigo = asyncio.create_task(revisar())

        transport.revisar_codigo = pedir_revision
        manager.revision_codigo.reabrir()
        # Las propuestas de aprendizaje de esta sesión que siguen esperando vuelven como tarjeta.
        manager.aprendizaje.reabrir()
        reader_task = asyncio.create_task(consume())
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, cierre.senal)
        vigia = asyncio.create_task(cierre.vigilar_padre())
        # Con --esperar la sesión se abre sin turno: el primero lo trae un mensaje.
        prompt = await transport.next_message() if esperar else (args.encargo or 'Continúa el encargo original.')
        # El encargo inicial ya lo pinta la app; lo que llega después se le devuelve.
        eco = esperar
        # Cada turno termina con turno_terminado; la sesión sigue abierta y un
        # mensaje abre el siguiente. Cancelar o EOF sin turno en curso cierran
        # con el estado del último turno; con turno en curso lo interrumpen.
        while prompt is not None and not transport.stopped:
            # Llegó otro mensaje (o una compactación): el repaso del turno anterior se deja, no se espera.
            await _cancelar_repaso(transport)
            await _cancelar_revision(transport)
            await transport.cancelar_exportacion()
            await manager.cola_encargos.detener()
            if isinstance(prompt, tuple) and prompt[0] is CONTINUAR:
                transport.compacting = True
                transport.worker = asyncio.create_task(manager.continuar_en_nueva())
                try:
                    nueva = await transport.worker
                except asyncio.CancelledError:
                    nueva = None
                finally:
                    transport.compacting = False
                if transport.interrupted:
                    code = 130
                    break
                if nueva:
                    # La app abre la nueva; esta se cierra lista para reanudarse.
                    transport.emit('sesion_cambiada', id=nueva, continuada_de=manager.task['id'])
                    code = 0
                    break
                prompt = await transport.next_message()
                continue
            if isinstance(prompt, tuple) and prompt[0] is COMPACTAR:
                transport.compacting = True
                transport.worker = asyncio.create_task(manager.compact_now(prompt[1]))
                try:
                    await transport.worker
                except asyncio.CancelledError:
                    pass
                finally:
                    transport.compacting = False
                if transport.interrupted:
                    code = 130
                    break
                prompt = await transport.next_message()
                continue
            manager.approval.denied = 0
            cambios = transport.take_settings()
            if cambios:
                try:
                    manager.aplicar_ajustes(cambios)
                except ErrorDeEquipo as exc:
                    transport.emit('error', texto=str(exc))
                manager.save()
                transport.emit('sesion', id=manager.task['id'], cwd=manager.task['cwd'],
                               modelo=manager.task['model'], esfuerzo=manager.task['effort'], modo=manager.task['modo'],
                               contexto=manager.task.get('contexto_modo') or 'ahorro')
            # Imágenes para un modelo que no las ve: se rechaza el mensaje sin empezar el turno.
            # La app lo quita de la conversación y lo devuelve al cuadro (`rechazado`).
            rechazo = await manager.sin_vision(prompt)
            if rechazo:
                from .adjuntos import rutas_etiquetadas as archivos_etiquetados
                from .imagenes import rutas_etiquetadas
                rechazado = {'texto': _texto_escrito(prompt), 'imagenes': rutas_etiquetadas(prompt)}
                if archivos_etiquetados(prompt):
                    rechazado['archivos'] = archivos_etiquetados(prompt)  # vuelven al cuadro con las imágenes
                transport.emit('error', texto=rechazo[0], motivo='sin_vision', rechazado=rechazado,
                               con_vision=[{'modelo': ident, 'nombre': nombre} for ident, nombre in rechazo[1]])
                if not eco:
                    code = 0  # el encargo inicial rechazado no es un fallo; un turno anterior manda
                eco = True
                prompt = await transport.next_message()
                continue
            transport.turno_cortado = False
            transport.worker = asyncio.create_task(manager.run(prompt, eco=eco))
            try:
                code = await transport.worker
            except asyncio.CancelledError:
                code = 130
            eco = True
            if transport.interrupted:
                code = 130
                manager.task.update(status='interrupted', summary='Sesión interrumpida; puedes reanudarla.')
                break
            estado = manager.task.get('status', 'failed')
            if transport.turno_cortado:
                # Sólo se cortó el turno («Enviar ahora»): la sesión sigue lista para el siguiente.
                manager.task.update(status='interrupted', summary='Turno interrumpido por el usuario.')
                estado, code = 'cortado', 1
                manager.save()
            ultimo = (manager.task.get('turnos') or [{}])[-1]
            transport.emit('turno_terminado', estado=estado,
                           resumen=manager.task.get('summary', ''), codigo_salida=code,
                           tokens=manager.task.get('total_tokens', 0),
                           **({'turno': ultimo['id']} if ultimo.get('id') else {}),
                           **gasto(manager.task, ultimo),
                           **({'cierre': manager.task['cierre']} if manager.task.get('cierre') else {}))
            topes = manager.presupuesto.para_el_cliente()
            if topes:
                transport.emit('presupuesto', **topes)
            if estado in ('completed', 'blocked'):
                # Aparte del turno: Detener no lo corta, y el turno ya está cerrado para la app.
                transport.repaso = asyncio.create_task(manager.repasar())
            # Un «compactar» que llegó tras la última respuesta del turno se hace ya.
            if transport.compact_pending:
                transport.compact_now, transport.compact_pending = transport.compact_pending, False
            prompt = await transport.next_message()
    except ErrorDeEquipo as exc:
        code = 1
        transport.emit('error', texto=str(exc))
        if manager:
            manager.task.update(status='failed', summary=str(exc))
    except Exception:
        code = 1
        transport.emit('error', texto='No se pudo iniciar o ejecutar la sesión.')
        if manager:
            manager.task.update(status='failed', summary='Fallo de ejecución')
    finally:
        cierre.limpiando()
        await _cancelar_revision(transport)
        await transport.cancelar_exportacion()
        if transport.repaso is not None and not transport.repaso.done():
            # La sesión se cierra: el repaso se deja (el turno ya estaba cerrado).
            transport.repaso.cancel()
            await asyncio.gather(transport.repaso, return_exceptions=True)
        if vigia is not None:
            vigia.cancel()
            await asyncio.gather(vigia, return_exceptions=True)
        if reader_task:
            reader_task.cancel()
            await asyncio.gather(reader_task, return_exceptions=True)
        if pipe:
            pipe.close()
        transport.stop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(sig)
        task = manager.task if manager else {}
        if manager:
            await manager.cola_encargos.detener()
            # Como Codex al salir: los procesos en segundo plano se cierran con la sesión.
            await manager.fondo.detener_todos()
            await manager.close_mcp()
            await manager.cerrar_diagnosticos()
            manager.save()
            manager.close()
        if code == 1:
            transport.emit('error', texto='La sesión no terminó correctamente.')
        transport.emit('fin', estado=task.get('status', 'failed'), resumen=task.get('summary', ''),
                       codigo_salida=code, tokens=task.get('total_tokens', 0), **gasto(task))
    cierre.terminado()
    return code


async def _cancelar_revision(transport):
    revision, transport.revision_codigo = transport.revision_codigo, None
    if revision is not None and not revision.done():
        revision.cancel()
        await asyncio.gather(revision, return_exceptions=True)


async def _cancelar_repaso(transport):
    repaso, transport.repaso = transport.repaso, None
    if repaso is not None and not repaso.done():
        repaso.cancel()
        await asyncio.gather(repaso, return_exceptions=True)


def gasto(task, turno=None):
    """`coste` de la sesión en USD (estimado con los precios de models.dev) y, de un turno,
    `tokens_turno` y `coste_turno`. Sin precio conocido no aparece ningún coste."""
    datos = {}
    if isinstance(task.get('coste_usd'), (int, float)) and not isinstance(task.get('coste_usd'), bool):
        datos['coste'] = round(task['coste_usd'], 6)
    if isinstance(turno, dict):
        if isinstance(turno.get('tokens'), int):
            datos['tokens_turno'] = turno['tokens']
        if isinstance(turno.get('coste'), (int, float)) and not isinstance(turno.get('coste'), bool):
            datos['coste_turno'] = round(turno['coste'], 6)
    from .presupuesto import resumen_cache
    cache = resumen_cache(task)
    if cache:
        datos['cache'] = cache
    return datos


def _sin_nacer(directory):
    """Sólo el `lock`: una sesión que arrancaba y no llegó a guardar su task.json (cortada a medias,
    o abierta ahora mismo). No es una sesión rota y no tiene nada que enseñar."""
    try:
        return os.listdir(directory) in ([], ['lock'])
    except OSError:
        return False


def sessions():
    base = Path(os.environ.get('ALFALFA_HOME') or Path.home() / '.alfalfa') / 'sesiones'
    rows = []
    if base.is_dir() and not base.is_symlink():
        for directory in base.iterdir():
            if not directory.is_dir() or directory.is_symlink():
                continue
            if _sin_nacer(directory):
                continue
            try:
                path = directory / 'task.json'
                if path.is_symlink():
                    raise ValueError()
                task = json.loads(path.read_text())
                row = dict(id=directory.name, encargo=_sin_etiquetas(task['objective']), cwd=task['cwd'],
                           estado=task['status'], modelo=task['model'], esfuerzo=task['effort'],
                           modo=task.get('modo') or 'auto',
                           fecha=task.get('updated_at', path.stat().st_mtime), tokens=task.get('total_tokens', 0),
                           turnos=len(task.get('turnos') or []), **gasto(task))
                if task.get('bifurcada_de'):
                    row['bifurcada_de'] = task['bifurcada_de']
                if task.get('padre'):
                    row['padre'] = task['padre']
                if task['id'] != directory.name or not isinstance(row['fecha'], (int, float)):
                    raise ValueError()
                row.update(_marcas(directory))
            except (OSError, ValueError, KeyError, TypeError):
                row = dict(id=directory.name, encargo=None, cwd=None, estado='corrupta',
                           modelo=None, esfuerzo=None, fecha=0, tokens=0)
            rows.append(row)
    return sorted(rows, key=lambda row: (row['fecha'], row['id']), reverse=True)


# Lo que la app de Mac cambia de una sesión sin tocar su task.json, que el motor
# reescribe mientras trabaja: título propio, fijada y archivada.
MARCAS = {'titulo': str, 'fijada': bool, 'archivada': bool}


def _texto_escrito(texto):
    """Lo que escribió el usuario: sin etiquetas de imagen o de archivo ni el relleno de un mensaje
    que sólo traía adjuntos (la app pintó su burbuja sin texto y lo devuelve al cuadro tal cual)."""
    from .adjuntos import sin_etiquetas
    limpio = sin_etiquetas(texto)
    return '' if limpio in (SOLO_IMAGENES, SOLO_ARCHIVOS) else limpio


def _sin_etiquetas(texto):
    from .adjuntos import rutas_etiquetadas, sin_etiquetas
    if not isinstance(texto, str):
        return texto
    return sin_etiquetas(texto) or ('Archivo adjunto' if rutas_etiquetadas(texto) else 'Imagen adjunta')


def _marcas(directory):
    path = directory / 'marcas.json'
    try:
        if path.is_symlink():
            return {}
        datos = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in datos.items() if k in MARCAS and isinstance(v, MARCAS[k])}


def _carpeta_de_sesion(ident):
    from .terminal import home_dir
    if not isinstance(ident, str) or not ident or '/' in ident or ident.startswith('.'):
        raise ValueError('Id de sesión inválido.')
    path = home_dir() / 'sesiones' / ident
    if not path.is_dir() or path.is_symlink():
        raise ValueError('No existe esa sesión en el hogar de AlfalfaCode.')
    return path


def _en_ejecucion(path):
    import fcntl
    try:
        fd = os.open(path / 'lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    except OSError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    except OSError:
        return True
    finally:
        os.close(fd)


def marcar(ident, campo, valor):
    """`--marcar ID CAMPO VALOR`: título (texto; vacío lo quita), fijada o archivada (sí/no)."""
    if campo not in MARCAS:
        raise ValueError('Campo desconocido: usa titulo, fijada o archivada.')
    path = _carpeta_de_sesion(ident)
    if MARCAS[campo] is bool:
        if valor not in ('si', 'sí', 'no', 'true', 'false', '1', '0'):
            raise ValueError('Usa sí o no.')
        valor = valor in ('si', 'sí', 'true', '1')
    else:
        valor = ' '.join(valor.split())[:200]
    marcas = _marcas(path)
    if valor in ('', False):
        marcas.pop(campo, None)
    else:
        marcas[campo] = valor
    from .sesiones import _escribir
    _escribir(path / 'marcas.json', json.dumps(marcas, ensure_ascii=False))
    return marcas


def eliminar(ident):
    """`--eliminar-sesion ID`: la mueve a la Papelera de macOS, de donde se puede recuperar."""
    path = _carpeta_de_sesion(ident)
    if _en_ejecucion(path):
        raise ValueError('La sesión está en ejecución; detenla antes de eliminarla.')
    papelera = Path.home() / '.Trash'
    papelera.mkdir(exist_ok=True)
    destino = papelera / ('alfalfa-' + ident)
    n = 1
    while destino.exists():
        n += 1
        destino = papelera / f'alfalfa-{ident}-{n}'
    path.rename(destino)
    return destino


def gestionar(args):
    """Órdenes de la app para una sesión guardada; responde una línea JSON."""
    transport = Transport(sys.stdout)
    try:
        if args.marcar:
            ident, campo, valor = args.marcar
            transport.emit('sesion_cambiada', id=ident, **{'marcas': marcar(ident, campo, valor)})
        elif args.eliminar_sesion:
            destino = eliminar(args.eliminar_sesion)
            try:
                from .busqueda_sesiones import quitar
                quitar(args.eliminar_sesion)
            except (OSError, ValueError, sqlite3.Error):
                pass  # la próxima actualización del índice la quita igual: ya no está en disco
            transport.emit('sesion_cambiada', id=args.eliminar_sesion, eliminada=True, papelera=str(destino))
        else:
            from . import sesiones as registro
            from .terminal import home_dir
            nueva = registro.bifurcar(home_dir() / 'sesiones', args.solo_bifurcar, args.turno)
            transport.emit('sesion_cambiada', id=nueva, bifurcada_de=args.solo_bifurcar)
        return 0
    except (OSError, ValueError) as exc:
        transport.emit('error', texto=str(exc) or 'No se pudo cambiar la sesión.')
        return 1


def turns(ident):
    from . import sesiones as registro
    from .terminal import home_dir
    if not isinstance(ident, str) or not ident or '/' in ident or ident.startswith('.'):
        raise ValueError()
    path = home_dir() / 'sesiones' / ident / 'task.json'
    if path.is_symlink():
        raise ValueError()
    return registro.turnos(json.loads(path.read_text()))


MAX_HISTORIAL = 600
_RUIDO = tuple(n + ' transporte: intento' for n in ('Ollama', 'OpenAI', 'Anthropic', 'Alfalfa'))


def history(ident):
    """Conversación guardada de una sesión para pintarla al abrirla: el mensaje de cada
    turno, sus eventos y el cierre del último. Los reintentos de transporte no se enseñan."""
    path = _carpeta_de_sesion(ident)
    task = json.loads((path / 'task.json').read_text())
    turnos = [t for t in task.get('turnos', []) if isinstance(t, dict) and t.get('id')]
    por_turno, sueltos = {}, []
    log = path / (task['id'] + '.jsonl')
    if log.is_file() and not log.is_symlink():
        for linea in log.read_text(errors='replace').splitlines():
            try:
                row = json.loads(linea)
            except ValueError:
                continue
            if not isinstance(row, dict) or not isinstance(row.get('text'), str):
                continue
            if row.get('role') == 'system' and row['text'].startswith(_RUIDO):
                continue
            evento = dict(rol=row.get('role', 'system'), texto=row['text'][:20000], hora=row.get('time', 0))
            if isinstance(row.get('compactacion'), dict):
                if row['compactacion'].get('estado') == 'en_curso':
                    continue  # Sólo interesa cómo acabó.
                evento['compactacion'] = row['compactacion']
            if row.get('turno'):
                evento['turno'] = row['turno']
            if isinstance(row.get('herramienta'), dict):
                evento['herramienta'] = row['herramienta']
            if isinstance(row.get('deshacer'), dict):
                # Deshacer o rehacer el código: la app pinta su marca con «Rehacer».
                evento['deshacer'] = row['deshacer']
            if isinstance(row.get('tarifa'), dict):
                # El aviso de tarifa: la app lo pinta ya contestado (sin botones vivos).
                evento['tarifa'] = row['tarifa']
            por_turno.setdefault(row['turno'], []).append(evento) if row.get('turno') else sueltos.append(evento)
    eventos = []
    if task.get('objective') and (not turnos or sueltos):
        eventos.append(dict(rol='user', texto=task['objective'], hora=task.get('created_at', 0)))
    eventos += sueltos
    for turno in turnos:
        propios = por_turno.get(turno['id'], [])
        # El turno sólo guarda el principio del mensaje: si la transcripción lo tiene entero, vale ese.
        if not (propios and propios[0]['rol'] == 'user'):
            eventos.append(dict(rol='user', texto=turno.get('mensaje', ''), hora=turno.get('inicio', 0),
                                turno=turno['id']))
        eventos += propios
    cierre = turnos[-1].get('cierre') if turnos else None
    extra = {'cierre': cierre} if isinstance(cierre, dict) else {}
    if task.get('cola_encargos'):
        extra['encargos'] = task['cola_encargos']
    if isinstance(task.get('context_tokens'), int) and isinstance(task.get('context_limit'), int):
        from .terminal import desglose_de
        extra['contexto'] = dict(tokens=task['context_tokens'], limite=task['context_limit'], **desglose_de(task))
    ultimo = turnos[-1] if turnos else None
    return dict(eventos=eventos[-MAX_HISTORIAL:], recortado=len(eventos) > MAX_HISTORIAL,
                tokens=task.get('total_tokens') or 0, **gasto(task, ultimo), **extra)


def check_command(argv):
    """`alfalfa --comprobar-orden ARGV`: lo que deciden las reglas, como `codex execpolicy check`."""
    from . import equipo, reglas
    from .terminal import home_dir
    home = home_dir()
    transport = Transport(sys.stdout)
    if not argv:
        transport.emit('error', texto='Indica la orden que quieres comprobar.')
        return 1
    try:
        personales = reglas.desde_hogar(home)
        politica = equipo.cargar(Path.cwd())
        evaluation = (politica.combinar(personales) if politica else personales).evaluar(list(argv))
    except (reglas.ReglasInvalidas, ErrorDeEquipo) as exc:
        transport.emit('error', texto=str(exc))
        return 1
    transport.emit('reglas', orden=list(argv), **evaluation.como_dict())
    return 0


def entry(args):
    transport = Transport(sys.stdout)
    if getattr(args, 'complementos', False):
        from .complementos import CargadorComplementos
        from .terminal import home_dir
        base = home_dir()
        informe = CargadorComplementos(base, Path.cwd()).cargar().informe()
        # Transport pone `version` y `tipo` en el sobre v2: el informe va entero
        # bajo `complementos` y no repite la clave `version`.
        transport.emit('complementos', complementos=informe['complementos'],
                       diagnosticos=informe['diagnosticos'], ejecucion=informe['ejecucion'],
                       esquema=informe['version'])
        return 0
    if getattr(args, 'turnos', None):
        try:
            transport.emit('turnos', sesion=args.turnos, turnos=turns(args.turnos))
            return 0
        except Exception:
            transport.emit('error', texto='No se pudieron leer los turnos de esa sesión.')
            return 1
    if getattr(args, 'historial', None):
        try:
            datos = history(args.historial)
            cola = datos.pop('encargos', None)
            transport.emit('historial', sesion=args.historial, **datos)
            if cola:
                transport.emit('encargos', cola=cola)
            return 0
        except Exception:
            transport.emit('error', texto='No se pudo leer el historial de esa sesión.')
            return 1
    if args.sesiones or args.modelos:
        try:
            if args.sesiones:
                transport.emit('sesiones', sesiones=sessions())
            else:
                from . import proveedores
                modelos, fallos = proveedores.listar_modelos()
                if not modelos and fallos:
                    raise proveedores.ErrorDeProveedor(' '.join(fallos.values()))
                # `fallos` sólo aparece si un proveedor no respondió: los que sí, se listan.
                transport.emit('modelos', modelos=modelos, **({'fallos': fallos} if fallos else {}))
            return 0
        except Exception:
            transport.emit('error', texto='No se pudo consultar Ollama o las sesiones de AlfalfaCode.')
            return 1
    class Diagnostics:
        def write(self, value):
            return sys.stderr.write(transport.clean(value))

        def flush(self):
            sys.stderr.flush()

    with redirect_stdout(Diagnostics()):
        return asyncio.run(run(args, transport))
