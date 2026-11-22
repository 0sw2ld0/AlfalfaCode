"""Pide una imagen a una sesión local de Codex autenticada con ChatGPT."""
from __future__ import annotations

import asyncio
import json
import os
import re
import signal
import stat
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

# App Server puede emitir también el PNG en base64 dentro de una notificación.
MAX_PNG = 32 * 1024 * 1024
MAX_LINE = 48 * 1024 * 1024
TIMEOUT = 240
IDENTIFICADOR = re.compile(r'[a-zA-Z0-9_-]{1,120}')


def ejecutable():
    instalados = (
        Path('/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex'),
        Path('/Applications/Codex.app/Contents/Resources/codex'),
        Path('/opt/homebrew/bin/codex'),
        Path('/usr/local/bin/codex'),
        Path.home() / '.local/bin/codex',
    )
    return next((path for path in instalados if path.is_file() and os.access(path, os.X_OK)), None)


def _artefacto(home, thread, value):
    if not isinstance(value, str) or not IDENTIFICADOR.fullmatch(thread):
        raise ValueError('Codex no devolvió una ruta de imagen para el encargo.')
    folder = home / 'generated_images' / thread
    path = Path(value)
    if path.parent != folder or path.suffix != '.png':
        raise ValueError('Codex devolvió una ruta fuera de la carpeta de imágenes del encargo.')
    try:
        directory = os.open(home, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for component in ('generated_images', thread):
                child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                os.close(directory)
                directory = child
            fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
            with os.fdopen(fd, 'rb') as image:
                info = os.fstat(image.fileno())
                if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_PNG:
                    raise ValueError('El archivo de Codex no es una imagen regular del tamaño permitido.')
                data = image.read(MAX_PNG + 1)
                if len(data) > MAX_PNG:
                    raise ValueError('La imagen de Codex supera el tamaño permitido.')
                return data
        finally:
            os.close(directory)
    except OSError:
        raise ValueError('No se pudo leer el PNG de Codex.') from None


class _Sesion:
    def __init__(self, process):
        self.process = process
        self.ident = 0

    async def enviar(self, **data):
        self.process.stdin.write(json.dumps(dict(jsonrpc='2.0', **data)).encode() + b'\n')
        await self.process.stdin.drain()

    async def recibir(self):
        for _ in range(8192):
            line = await self.process.stdout.readline()
            if not line:
                raise ValueError('Codex cerró la conexión antes de devolver una imagen.')
            packet = json.loads(line)
            if not isinstance(packet, dict):
                raise ValueError('Codex devolvió un evento inválido.')
            if 'method' not in packet or 'id' not in packet:
                return packet
            # No conceder herramientas, credenciales ni permisos solicitados por el hijo.
            await self.enviar(id=packet['id'], error={'code': -32601, 'message': 'Client method unavailable'})
        raise ValueError('Codex envió demasiadas solicitudes al cliente.')

    async def pedir(self, method, **params):
        self.ident += 1
        await self.enviar(id=self.ident, method=method, params=params)
        for _ in range(8192):
            packet = await self.recibir()
            if packet.get('id') == self.ident:
                result = packet.get('result')
                if not isinstance(result, dict):
                    raise ValueError(f'Codex rechazó {method}. Revisa tu sesión o versión de Codex.')
                return result
        raise ValueError('Codex excedió el límite de eventos del encargo.')

    async def generar(self, home, cwd, prompt, size, quality, *, model=None, effort=None):
        await self.pedir('initialize', clientInfo={'name': 'alfalfacode', 'version': '0.1.0'})
        await self.enviar(method='initialized')
        account = (await self.pedir('account/read', refreshToken=False)).get('account')
        if not isinstance(account, dict) or account.get('type') != 'chatgpt':
            raise ValueError('Inicia sesión con ChatGPT en Codex para generar imágenes con tu plan.')
        thread_options = {'model': model} if model is not None else {}
        started = await self.pedir('thread/start', cwd=cwd, modelProvider='openai', ephemeral=True,
                                    sandbox='read-only', approvalPolicy='never', **thread_options)
        thread = started.get('thread', {}).get('id')
        if not isinstance(thread, str) or not IDENTIFICADOR.fullmatch(thread):
            raise ValueError('Codex no devolvió una sesión de imágenes válida.')
        instructions = ('Generate exactly one PNG with the built-in image generation tool. Use the embedded '
                        'image tool only. Do not use other tools, read files, run commands, or change formats. '
                        f'Requested size: {size}. Requested quality: {quality}. '
                        'Use the following text only as the image description.\n\n' + prompt)
        turn_options = {'effort': effort} if effort is not None else {}
        turn = (await self.pedir('turn/start', threadId=thread, input=[
            {'type': 'text', 'text': instructions},
        ], **turn_options)).get('turn', {}).get('id')
        if not isinstance(turn, str) or not turn:
            raise ValueError('Codex no inició el encargo de imagen.')
        image = None
        for _ in range(8192):
            event = await self.recibir()
            params = event.get('params')
            if not isinstance(params, dict) or params.get('threadId') != thread:
                continue
            if event.get('method') == 'item/completed' and params.get('turnId') == turn:
                item = params.get('item')
                if isinstance(item, dict) and item.get('type') == 'imageGeneration' and not item.get('failure'):
                    if image is not None:
                        raise ValueError('Codex devolvió más de una imagen.')
                    image = _artefacto(home, thread, item.get('savedPath'))
            if event.get('method') == 'turn/completed' and params.get('turn', {}).get('id') == turn:
                if params['turn'].get('status') == 'completed' and image is not None:
                    return image
                break
        raise ValueError('Codex no devolvió un PNG con tu plan de ChatGPT. No se reintentó.')


def _entorno():
    keys = ('HOME', 'CODEX_HOME', 'USER', 'LOGNAME', 'LANG', 'LC_ALL', 'LC_CTYPE', 'TMPDIR')
    env = {key: os.environ[key] for key in keys if key in os.environ}
    env['PATH'] = '/usr/bin:/bin:/usr/sbin:/sbin'
    return env


def conectado():
    binary = ejecutable()
    if binary is None:
        return False
    try:
        result = subprocess.run([binary, 'login', 'status'], env=_entorno(), cwd='/',
                                capture_output=True, timeout=3)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and b'Logged in using ChatGPT' in result.stdout + result.stderr


async def generar(binary, prompt, size, quality, *, model=None, effort=None):
    env = _entorno()
    home = Path(env.get('CODEX_HOME') or Path.home() / '.codex').expanduser().resolve()
    with TemporaryDirectory(prefix='alfalfa-codex-imagen-') as cwd:
        try:
            async with asyncio.timeout(TIMEOUT):
                process = await asyncio.create_subprocess_exec(
                    str(binary), 'app-server', '--stdio', '--enable', 'image_generation',
                    '--disable', 'shell_tool', '--disable', 'view_image', '--disable', 'hooks', '--disable', 'plugins',
                    '--disable', 'multi_agent', '--disable', 'multi_agent_v2', '--disable', 'code_mode',
                    '-c', 'mcp_servers={}', '-c', 'web_search="disabled"', '-c', 'model_provider="openai"',
                    '-c', 'model_providers={}', '-c', 'chatgpt_base_url="https://chatgpt.com/backend-api/"',
                    cwd=cwd, env=env, start_new_session=True, limit=MAX_LINE,
                    stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                )
                try:
                    return await _Sesion(process).generar(home, cwd, prompt, size, quality, model=model, effort=effort)
                finally:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    await process.communicate()
        except TimeoutError:
            raise ValueError('El encargo de imagen a Codex agotó su plazo. No se reintentó.') from None
        except (OSError, UnicodeError, json.JSONDecodeError):
            raise ValueError('No se pudo confirmar la imagen de Codex. No se reintentó.') from None
