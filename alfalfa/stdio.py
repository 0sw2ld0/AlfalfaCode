"""Transporte JSONL v2. Un lector atiende permisos, mensajes y cancelación concurrentemente."""
import asyncio
import json
import os
import re
import signal
import sys
import time
from contextlib import redirect_stdout
from pathlib import Path
from uuid import uuid4

from .permisos.terminal import solo_lectura

VERSION = 2
MAX_MENSAJE = 20000
# Campos opcionales del evento `permiso` (política on-request); los clientes v2
# anteriores los ignoran.
PERMISO_EXTRA = ('motivo', 'motivos', 'justificacion', 'regla', 'intento', 'reintento_de', 'red',
                 'sin_sandbox', 'fallo_sandbox', 'mcp', 'app', 'cwd', 'timeout_seconds', 'para_sesion')


# Lo que devuelve next_message cuando toca compactar entre turnos.
COMPACTAR = object()


class Transport:
    def __init__(self, output):
        self.output = output
        self.pending = {}
        self.worker = None
        self.stopped = False
        self.interrupted = False
        # Mensajes aceptados que el motor aún no ha incorporado a su historial.
        self.inbox = []
        self.wake = asyncio.Event()
        # Credenciales declaradas para servidores MCP (mcp_cliente): se redactan igual.
        self.secretos = set()
        # {"tipo":"detener_proceso","id":"p1"|"todos"}: lo conecta run() con el gestor.
        self.stop_process = None
        # {"tipo":"mantener_despierto","activo":bool}: el interruptor de Ajustes, ya.
        self.keep_awake = None
        # Permisos que admiten «en esta sesión» (`para_sesion`).
        self.for_session = set()
        # {"tipo":"compactar"} con un turno en marcha: el motor compacta antes de su
        # siguiente inferencia. True, o el texto de las instrucciones del usuario.
        self.compact_pending = False
        # Sin turno en marcha se compacta ya (como /compact de Claude Code): el bucle
        # lo recoge en next_message. Mismo valor que compact_pending.
        self.compact_now = False
        # Lo marca TerminalManager.run mientras dura un turno.
        self.in_turn = False
        # «Redirigir»: el modelo deja la respuesta que estaba pensando y lee ya los mensajes.
        self.redireccion = asyncio.Event()
        # «Enviar ahora» corta el turno en marcha sin cerrar la sesión.
        self.turno_cortado = False
        self.compacting = False
        # Modelo y esfuerzo pedidos por el cliente para el próximo turno.
        self.pending_settings = {}

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
        print(json.dumps(self.clean(dict(version=VERSION, tipo=tipo, **fields)), ensure_ascii=False),
              file=self.output, flush=True)

    def resolve(self, ident, approved, reason, session=False):
        """`session`: el usuario aprobó para el resto de la sesión (sólo si la petición lo ofrecía)."""
        future = self.pending.pop(ident)
        self.emit('permiso_resuelto', id=ident, aprobado=approved, motivo=reason,
                  **({'sesion': True} if approved and session else {}))
        if not future.done():
            future.set_result('sesion' if approved and session else approved)

    def stop(self):
        if self.stopped:
            return
        self.stopped = True
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
        """Corta el turno en marcha, como Esc en Codex y Claude Code; la sesión sigue abierta."""
        if self.stopped or not self.in_turn or not self.worker or self.worker.done():
            return False
        self.turno_cortado = True
        for ident in list(self.pending):
            self.resolve(ident, False, 'Turno interrumpido')
        self.worker.cancel()
        return True

    def enviar_ahora(self, text):
        """«Enviar ahora» de un mensaje en cola: corta el turno y el mensaje abre el siguiente."""
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

    def take_messages(self):
        messages, self.inbox = self.inbox, []
        return messages

    async def next_message(self):
        """Espera el mensaje que abre un turno nuevo; None si la sesión se cierra.

        Una compactación pedida entre turnos sale antes que los mensajes, como COMPACTAR
        con sus instrucciones: (COMPACTAR, texto o None)."""
        while not self.stopped:
            if self.compact_now:
                pedido, self.compact_now = self.compact_now, False
                return (COMPACTAR, pedido if isinstance(pedido, str) else None)
            if self.inbox:
                return self.inbox.pop(0)
            self.wake.clear()
            await self.wake.wait()
        return None

    def message(self, text, images=()):
        """Un mensaje del usuario. `images`: rutas absolutas que se adjuntan como en Codex."""
        from . import imagenes
        if images and not (isinstance(images, list) and all(isinstance(r, str) for r in images)):
            self.emit('error', texto='`imagenes` tiene que ser una lista de rutas.')
            return
        if images and isinstance(text, str) and not text.strip():
            text = 'Mira la imagen adjunta.'
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_MENSAJE:
            self.emit('error', texto='El mensaje debe ser texto no vacío de hasta 20000 caracteres.')
            return
        try:
            text = imagenes.con_etiquetas(text, list(images))
        except imagenes.ImagenInvalida as exc:
            self.emit('error', texto=str(exc))
            return
        if self.stopped:
            self.emit('error', texto='La sesión se está cerrando; el mensaje no se entregó.')
        else:
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
                self.message(value.get('texto'), value.get('imagenes') or [])
            elif value.get('tipo') == 'enviar_ahora':
                self.enviar_ahora(value.get('texto'))
            elif value.get('tipo') == 'redirigir':
                self.redirigir()
            elif value.get('tipo') == 'interrumpir':
                self.interrumpir_turno()
            elif value.get('tipo') == 'ajustes':
                self.settings(value.get('modelo'), value.get('esfuerzo'), value.get('modo'))
            elif value.get('tipo') == 'compactar':
                self.compact(value.get('instrucciones'))
            elif value.get('tipo') == 'mantener_despierto':
                activo = value.get('activo')
                if not isinstance(activo, bool) or self.keep_awake is None:
                    raise ValueError()
                self.keep_awake(activo)
            elif value.get('tipo') == 'detener_proceso':
                ident = value.get('id')
                if not isinstance(ident, str) or self.stop_process is None:
                    raise ValueError()
                self.stop_process(ident)
            elif value.get('tipo') == 'permiso':
                ident = value.get('id')
                if not isinstance(ident, str) or ident not in self.pending:
                    raise ValueError()
                approved = value.get('aprobado') is True
                session = value.get('sesion') is True and ident in self.for_session
                self.for_session.discard(ident)
                self.resolve(ident, approved, 'Respuesta del cliente', session=session)
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
                if transport.stopped:
                    transport.resolve(ident, False, 'Sesión interrumpida')
                # Las excepciones on-request (motivo) siempre esperan al cliente.
                elif auto and not unsandboxed and not args.get('motivo') and solo_lectura(
                        args['argv'], root, Path(root) / args['cwd']
                        if isinstance(args.get('cwd'), str) and args['cwd'] not in ('', '.') else None):
                    transport.resolve(ident, True, 'solo_lectura')
                try:
                    approved = await future
                finally:
                    transport.for_session.discard(ident)
                    if ident in transport.pending:
                        transport.resolve(ident, False, 'Sesión interrumpida' if transport.stopped
                                          else 'Sin respuesta a tiempo')
                if not approved:
                    self.denied += 1
                    raise ValueError('Comando rechazado; no se ejecutó.')
                return approved if approved == 'sesion' else None

        return Approval()


async def run(args, transport):
    from .terminal import TerminalManager, keep_awake
    manager = None
    reader_task = None
    pipe = None
    code = 1
    loop = asyncio.get_running_loop()
    try:
        from .terminal import resolve_fork
        resolve_fork(args)
        esperar = bool(getattr(args, 'esperar', False)) and bool(args.reanudar) and not args.encargo
        manager = TerminalManager(args.encargo, resume=args.reanudar, model=args.modelo,
                                  effort=args.esfuerzo, auto=args.auto, transport=transport,
                                  read_only=getattr(args, 'solo_lectura', False),
                                  compact_limit=getattr(args, 'compactar_en', None), mode=getattr(args, 'modo', None),
                                  wait=esperar, parent=getattr(args, 'padre', None), keep_awake=keep_awake(args))
        if getattr(args, 'titulo', None) and not args.reanudar:
            manager.save()
            marcar(manager.task['id'], 'titulo', args.titulo)
        if esperar:
            code = 0  # Abrir y cerrar sin turno no es un fallo.
        transport.emit('sesion', id=manager.task['id'], cwd=manager.task['cwd'],
                       modelo=manager.task['model'], esfuerzo=manager.task['effort'], modo=manager.task['modo'],
                       **({'bifurcada_de': manager.task['bifurcada_de']} if manager.task.get('bifurcada_de') else {}))
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
                    transport.stop()
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
        transport.keep_awake = manager.despierto.cambiar
        reader_task = asyncio.create_task(consume())
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, transport.stop)
        # Con --esperar la sesión se abre sin turno: el primero lo trae un mensaje.
        prompt = await transport.next_message() if esperar else (args.encargo or 'Continúa el encargo original.')
        # El encargo inicial ya lo pinta la app; lo que llega después se le devuelve.
        eco = esperar
        # Cada turno termina con turno_terminado; la sesión sigue abierta y un
        # mensaje abre el siguiente. Cancelar o EOF sin turno en curso cierran
        # con el estado del último turno; con turno en curso lo interrumpen.
        while prompt is not None and not transport.stopped:
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
                manager.task.update(cambios)
                manager.save()
                transport.emit('sesion', id=manager.task['id'], cwd=manager.task['cwd'],
                               modelo=manager.task['model'], esfuerzo=manager.task['effort'], modo=manager.task['modo'])
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
            # Un «compactar» que llegó tras la última respuesta del turno se hace ya.
            if transport.compact_pending:
                transport.compact_now, transport.compact_pending = transport.compact_pending, False
            prompt = await transport.next_message()
    except Exception:
        code = 1
        transport.emit('error', texto='No se pudo iniciar o ejecutar la sesión.')
        if manager:
            manager.task.update(status='failed', summary='Fallo de ejecución')
    finally:
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
            # Como Codex al salir: los procesos en segundo plano se cierran con la sesión.
            await manager.fondo.detener_todos()
            await manager.close_mcp()
            manager.save()
            manager.close()
        if code == 1:
            transport.emit('error', texto='La sesión no terminó correctamente.')
        transport.emit('fin', estado=task.get('status', 'failed'), resumen=task.get('summary', ''),
                       codigo_salida=code, tokens=task.get('total_tokens', 0), **gasto(task))
    return code


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
    return datos


def sessions():
    base = Path(os.environ.get('ALFALFA_HOME') or Path.home() / '.alfalfa') / 'sesiones'
    rows = []
    if base.is_dir() and not base.is_symlink():
        for directory in base.iterdir():
            if not directory.is_dir() or directory.is_symlink():
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


def _sin_etiquetas(texto):
    from .imagenes import CIERRE, ETIQUETA
    if not isinstance(texto, str):
        return texto
    limpio = ETIQUETA.sub('', texto).replace(CIERRE, '').strip()
    return limpio or 'Imagen adjunta'


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
_RUIDO = tuple(n + ' transporte: intento' for n in ('Ollama', 'OpenAI', 'Alfalfa'))


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
    if isinstance(task.get('context_tokens'), int) and isinstance(task.get('context_limit'), int):
        from .terminal import desglose_de
        extra['contexto'] = dict(tokens=task['context_tokens'], limite=task['context_limit'], **desglose_de(task))
    ultimo = turnos[-1] if turnos else None
    return dict(eventos=eventos[-MAX_HISTORIAL:], recortado=len(eventos) > MAX_HISTORIAL,
                tokens=task.get('total_tokens') or 0, **gasto(task, ultimo), **extra)


def check_command(argv):
    """`alfalfa --comprobar-orden ARGV`: lo que deciden las reglas, como `codex execpolicy check`."""
    from . import reglas
    from .terminal import home_dir
    home = home_dir()
    transport = Transport(sys.stdout)
    if not argv:
        transport.emit('error', texto='Indica la orden que quieres comprobar.')
        return 1
    try:
        evaluation = reglas.desde_hogar(home).evaluar(list(argv))
    except reglas.ReglasInvalidas as exc:
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
            transport.emit('historial', sesion=args.historial, **history(args.historial))
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
