"""Un servidor y un daemon propios por proyecto; nunca adopta procesos de otros clientes."""
from __future__ import annotations

import asyncio
import fcntl
import hashlib
import json
import os
import re
import shutil
import socket
import stat
import subprocess
import time
from pathlib import Path

from .cuybrowser_http import HttpCuyBrowser


def _private(path):
    path = Path(path)
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError('El hogar propio de CuyBrowser debe ser un directorio privado sin enlaces simbólicos.')
    return path


def _ipc(home, kind='ping', expected=None):
    """IPC de control sin autostart. La parada comprueba el PID en la MISMA conexión."""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
            conn.settimeout(0.5)
            conn.connect(str(home / 'daemon.sock'))
            stream = conn.makefile('rwb')
            stream.write(b'{"id":1,"type":"ping"}\n')
            stream.flush()
            info = json.loads(stream.readline(4096))
            if info.get('type') != 'pong' or not isinstance(info.get('pid'), int):
                raise ValueError('El socket propio de CuyBrowser no responde al protocolo esperado.')
            if kind == 'shutdown' and info['pid'] == expected:
                stream.write(b'{"id":2,"type":"shutdown"}\n')
                stream.flush()
                stream.readline(4096)
            return info
    except (FileNotFoundError, ConnectionRefusedError):
        return None


def _port():
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]


class ProcesoCuyBrowser:
    def __init__(self, profile='alfalfa'):
        if not isinstance(profile, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', profile):
            raise ValueError('El perfil de CuyBrowser admite 1–64 letras ASCII, números, guiones y guiones bajos.')
        self.profile = profile
        self.home = None
        self.staging = None
        self.process = None
        self.pid = None
        self.lock = None
        self.http = None
        self.authorized = False
        self.root = None

    async def start(self, runtime, ask, *, strict=False):
        root = runtime.tools.root.resolve()
        if self.root is not None and root != self.root:
            raise ValueError('Este navegador pertenece a otro proyecto; inicia una sesión nueva para cambiarlo.')
        if self.http is not None:
            if strict and self.http.capabilities.get('strictResources') is not True:
                self.close()
                raise ValueError('CuyBrowser no garantiza el modo estricto de subrecursos; usa WebKit.')
            return self.http
        self.root = root
        key = hashlib.sha256(os.fsencode(root)).hexdigest()[:24]
        self.home = _private(_private(runtime.manager.home / 'cuybrowser') / key)
        self.staging = _private(_private(runtime.manager.home / 'subidas-cuybrowser') / key)
        self.lock = os.open(self.home / 'alfalfa.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        lock_info = os.fstat(self.lock)
        if (not stat.S_ISREG(lock_info.st_mode) or lock_info.st_nlink != 1
                or lock_info.st_uid != os.getuid() or lock_info.st_mode & 0o077):
            os.close(self.lock)
            self.lock = None
            raise ValueError('El bloqueo del navegador debe ser un archivo privado normal sin enlaces duros.')
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(self.lock)
            self.lock = None
            raise ValueError('Otra sesión ocupa el navegador de este proyecto; ciérrala antes de continuar.') from None
        try:
            if await asyncio.to_thread(_ipc, self.home) is not None:
                raise ValueError('Ya hay un daemon en el hogar reservado de este proyecto; no se adoptará ni detendrá.')
            executable = shutil.which('cuybrowser')
            if not executable:
                raise ValueError('No se encuentra cuybrowser en PATH. Instala una versión compatible explícitamente.')
            executable = str(Path(executable).resolve())
            if Path(executable).is_relative_to(root):
                raise ValueError('No se ejecuta un cuybrowser procedente del proyecto; '
                                 'usa una instalación de confianza.')
            if not self.authorized:
                await ask(runtime, [executable, 'serve', '--http'],
                          'Navegador: iniciar CuyBrowser fuera del sandbox con un perfil propio del proyecto ('
                          + self.profile + '). Sus diálogos nativos confirmarán las acciones sensibles.')
                self.authorized = True
            port = _port()
            env = dict(os.environ, CUYBROWSER_HOME=str(self.home))
            self.process = subprocess.Popen([executable, 'serve', '--http', '--port', str(port)], env=env,
                                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                            stderr=subprocess.DEVNULL, start_new_session=True)
            for _ in range(100):
                if self.process.poll() is not None:
                    raise ValueError('El servidor propio de CuyBrowser terminó al arrancar; no se reintentó.')
                info = await asyncio.to_thread(_ipc, self.home)
                if info:
                    self.pid = info['pid']
                    token = self._token()
                    candidate = HttpCuyBrowser(port, token)
                    try:
                        await candidate.discover()
                    except ValueError:
                        await asyncio.sleep(0.1)
                        continue
                    policy = await candidate.check_policy()
                    if (candidate.capabilities.get('exactOrigins') is not True
                            or candidate.capabilities.get('localNetwork') is not True):
                        raise ValueError('Este CuyBrowser no garantiza orígenes exactos antes del contacto, incluso '
                                         'al restaurar perfiles, y bloqueo de red local. Usa una versión compatible; '
                                         'no se abrió Chromium.')
                    if strict and candidate.capabilities.get('strictResources') is not True:
                        raise ValueError('CuyBrowser no garantiza el modo estricto de subrecursos; '
                                         'usa WebKit. No se abrió Chromium.')
                    self.origins(policy, [], [], [])  # Cerrado ANTES de lanzar/restaurar Chromium.
                    await candidate.call('start', {'mode': 'launch', 'profile': self.profile, 'headless': False})
                    self.http = candidate
                    return candidate
                await asyncio.sleep(0.1)
            raise ValueError('CuyBrowser no arrancó dentro de 10 segundos; no se reintentó.')
        except OSError as exc:
            self.close()
            raise ValueError('No se pudo iniciar el servidor propio de CuyBrowser (' + type(exc).__name__
                             + '). Comprueba permisos, longitud de ALFALFA_HOME y loopback.') from None
        except BaseException:
            self.close()
            raise

    def origins(self, policy, allow, deny, local, visit=None):
        """JSON es YAML válido. Sólo se modifica la política del hogar exclusivo de este proyecto."""
        path = self.home / 'policy.yaml'
        info = path.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_uid != os.getuid() or info.st_mode & 0o077):
            raise ValueError('La política propia de CuyBrowser debe ser un archivo normal privado.')
        origins = {'allow': sorted(set(allow)), 'deny': sorted(set(deny))}
        if visit is not None:
            origins['visit'] = visit
        content = dict(policy, origins=origins,
                       local_network={'allow_origins': sorted(set(local))})
        temporary = self.home / 'alfalfa-policy.tmp'
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(fd, 'w') as out:
                json.dump(content, out, ensure_ascii=False)
                out.write('\n')
            if path.lstat() != info:
                raise ValueError('La política de CuyBrowser cambió mientras se preparaba; no se sobrescribió.')
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    def _token(self):
        fd = os.open(self.home / 'token', os.O_RDONLY | os.O_NOFOLLOW)
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size not in (64, 65)
                    or info.st_uid != os.getuid() or info.st_mode & 0o077):
                raise ValueError('El token propio de CuyBrowser debe ser privado y un archivo normal.')
            token = os.read(fd, 129).decode('ascii').strip()
            if not re.fullmatch('[a-fA-F0-9]{64}', token):
                raise ValueError('Token de CuyBrowser inválido.')
            return token
        finally:
            os.close(fd)

    def close(self):
        if self.pid is not None and self.home is not None:
            try:
                _ipc(self.home, 'shutdown', self.pid)
                # bye precede al cierre de Chromium. Mantén el lock para no reabrir el mismo perfil demasiado pronto.
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    info = _ipc(self.home)
                    if info is None or info.get('pid') != self.pid:
                        break
                    time.sleep(0.05)
            except (OSError, ValueError):
                pass  # Nunca matar un PID obtenido de un archivo o de otro daemon.
            self.pid = None
        if self.process is not None:
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=2)
            self.process = None
        self.http = None
        if self.lock is not None:
            os.close(self.lock)
            self.lock = None
