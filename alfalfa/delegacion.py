"""Encargos de texto entre AlfalfaCode y Antigravity, sin herramientas heredadas."""
from __future__ import annotations

import asyncio
import json
import os
import signal
from pathlib import Path
from tempfile import TemporaryDirectory

from .busqueda_sesiones.redaccion import tapar

MAX_PROMPT = 16_000
MAX_OUTPUT = 16_000
TIMEOUT = 90
MOTIVO = 'delegacion_antigravity'


def ejecutable():
    for directory in (Path.home() / '.local/bin', Path.home() / '.antigravity/bin', Path('/opt/homebrew/bin'),
                      Path('/usr/local/bin'), Path('/usr/bin')):
        candidate = directory / 'agy'
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    return None


def encargo(message, secrets=()):
    if not message.strip() or len(message) > MAX_PROMPT:
        raise ValueError(f'El encargo requiere de 1 a {MAX_PROMPT} caracteres.')
    return tapar(message.strip(), secrets)


class _ACP:
    def __init__(self, process):
        self.process = process
        self.ident = 0
        self.session = None
        self.answer = ''

    async def send(self, **data):
        self.process.stdin.write(json.dumps(dict(jsonrpc='2.0', **data)).encode() + b'\n')
        await self.process.stdin.drain()

    async def request(self, method, **params):
        self.ident += 1
        await self.send(id=self.ident, method=method, params=params)
        for _ in range(20_000):
            line = await self.process.stdout.readline()
            if not line:
                raise ValueError('Antigravity cerró la conexión ACP.')
            message = json.loads(line)
            if not isinstance(message, dict) or message.get('jsonrpc') != '2.0':
                raise ValueError('Antigravity envió un mensaje ACP inválido.')
            if 'method' in message:
                await self.receive(message)
            elif type(message.get('id')) is int and message['id'] == self.ident:
                result = message.get('result')
                error = message.get('error')
                if (isinstance(error, dict) and error.get('code') == -32000
                        and error.get('message') == 'Authentication required'):
                    raise ValueError('Inicia sesión con Google en Antigravity antes de delegar un encargo.')
                if not isinstance(result, dict):
                    raise ValueError(f'Antigravity rechazó {method}. Revisa la conexión y la cuenta de Google.')
                return result
        raise ValueError('Antigravity excedió el límite de mensajes ACP.')

    async def receive(self, message):
        method = message['method']
        if 'id' in message:
            if method == 'session/request_permission':
                params = message.get('params')
                options = params.get('options') if isinstance(params, dict) else None
                if not isinstance(options, list):
                    options = []
                reject = next((option['optionId'] for option in options if isinstance(option, dict)
                               and option.get('kind') == 'reject_once' and isinstance(option.get('optionId'), str)),
                              None)
                outcome = {'outcome': 'selected', 'optionId': reject} if reject else {'outcome': 'cancelled'}
                await self.send(id=message['id'], result={'outcome': outcome})
            else:
                await self.send(id=message['id'], error={'code': -32601, 'message': 'Client method unavailable'})
            return
        params = message.get('params')
        if method != 'session/update' or not self.session or not isinstance(params, dict):
            return
        update = params.get('update')
        if params.get('sessionId') != self.session or not isinstance(update, dict):
            return
        content = update.get('content')
        if (update.get('sessionUpdate') == 'agent_message_chunk' and isinstance(content, dict)
                and content.get('type') == 'text' and isinstance(content.get('text'), str)):
            self.answer = (self.answer + content['text'])[:MAX_OUTPUT]

    async def run(self, cwd, message):
        version = await self.request('initialize', protocolVersion=1, clientCapabilities={})
        if version.get('protocolVersion') != 1:
            raise ValueError('Antigravity no admite ACP 1.')
        opened = await self.request('session/new', cwd=cwd, mcpServers=[])
        self.session = opened.get('sessionId')
        if not isinstance(self.session, str) or not self.session:
            raise ValueError('Antigravity no devolvió una sesión ACP válida.')
        modes = opened.get('modes')
        if not isinstance(modes, dict):
            raise ValueError('Antigravity no devolvió los modos de ejecución.')
        if modes.get('currentModeId') != 'plan':
            advertised = modes.get('availableModes') or []
            if not isinstance(advertised, list) or not any(
                isinstance(mode, dict) and mode.get('id') == 'plan' for mode in advertised
            ):
                raise ValueError('Antigravity no ofreció el modo Plan para este encargo.')
            await self.request('session/set_mode', sessionId=self.session, modeId='plan')
        prompt = 'Review this coding task using only the supplied context. Return a text report.\n\n' + message
        result = await self.request('session/prompt', sessionId=self.session, prompt=[{'type': 'text', 'text': prompt}])
        if result.get('stopReason') != 'end_turn' or not self.answer.strip():
            raise ValueError('Antigravity no completó el encargo.')
        return tapar(self.answer)


async def antigravity(executable, message):
    env = {key: os.environ[key] for key in ('HOME', 'USER', 'LOGNAME', 'LANG', 'TMPDIR', 'GEMINI_HOME',
                                            'XDG_CONFIG_HOME', 'XDG_DATA_HOME') if key in os.environ}
    env['PATH'] = '/usr/bin:/bin:/usr/sbin:/sbin'
    with TemporaryDirectory(prefix='alfalfa-antigravity-') as cwd:
        try:
            async with asyncio.timeout(TIMEOUT):
                process = await asyncio.create_subprocess_exec(
                    str(executable), 'acp', '--sandbox', '--include-default-customizations=false', cwd=cwd, env=env,
                    stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                    start_new_session=True, limit=256 * 1024,
                )
                try:
                    return await _ACP(process).run(cwd, message)
                finally:
                    # Cierra el grupo incluso si el líder salió antes que un trabajador.
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    await process.communicate()
        except TimeoutError:
            raise ValueError('El encargo a Antigravity agotó su plazo.') from None
        except OSError:
            raise ValueError('No se pudo iniciar Antigravity CLI.') from None


async def delegar(runtime, message):
    binary = ejecutable()
    if binary is None:
        raise ValueError('No se encontró Antigravity CLI (agy).')
    from .contenido_externo import secretos

    message = encargo(message, secretos(runtime))
    await runtime.approve({
        'argv': ['delegate_antigravity', json.dumps({'message': message}, ensure_ascii=False)],
        'purpose': 'Enviar el texto de este encargo a Antigravity con tu cuenta de Google',
        'motivo': MOTIVO, 'motivos': [MOTIVO],
    })
    return {'text': await antigravity(binary, message)}


async def consultar(model, message):
    """Una inferencia con un proveedor de AlfalfaCode, sin acceso al arnés de herramientas."""
    import httpx

    from . import proveedores

    provider, name = proveedores.para_modelo(model)
    if not provider.configurado():
        raise ValueError('El proveedor elegido no está configurado en AlfalfaCode.')
    secrets = [value for value in provider.secretos() if isinstance(value, str) and len(value) >= 8]
    message = encargo(message, secrets)
    try:
        async with asyncio.timeout(TIMEOUT):
            async with provider.cliente(httpx.Timeout(TIMEOUT, connect=5)) as client:
                response = await client.post('/api/chat', json={
                    'model': name,
                    'messages': [
                        {'role': 'system', 'content': 'Answer the delegated task using only its supplied text.'},
                        {'role': 'user', 'content': message},
                    ],
                    'stream': False,
                    'options': {'num_predict': 2048},
                })
                response.raise_for_status()
                data = response.json()
                answer = data['message']['content']
    except (httpx.HTTPError, TimeoutError, ValueError, KeyError, TypeError):
        raise ValueError('El modelo de AlfalfaCode no completó el encargo.') from None
    if not isinstance(answer, str) or not answer.strip():
        raise ValueError('El modelo de AlfalfaCode no devolvió texto.')
    return tapar(answer[:MAX_OUTPUT], secrets)


def servir(model):
    from mcp.server.fastmcp import FastMCP

    from . import equipo

    policy = equipo.cargar(Path.cwd())
    server = FastMCP('AlfalfaCode delegation')
    lock = asyncio.Lock()
    remaining = 3

    @server.tool()
    async def delegate_alfalfa(message: str) -> str:
        """Consult the selected AlfalfaCode model on a self-contained coding task. Send only the necessary text."""
        nonlocal remaining
        if policy:
            raise ValueError('La delegación no está disponible en proyectos con política de equipo.')
        if lock.locked() or remaining <= 0:
            raise ValueError('La conexión sólo permite tres consultas, una a la vez.')
        async with lock:
            remaining -= 1
            return await consultar(model, message)

    server.run(transport='stdio')
