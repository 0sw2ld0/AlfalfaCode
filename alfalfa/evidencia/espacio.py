"""Local observations, never model-supplied attestations.

Git is queried in a worker thread. The saved watch also permits synchronous
evidence readers to invalidate a check without launching a process.
"""
import ast
import asyncio
import errno
import hashlib
import os
import re
import select
import shlex
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path

# inotify (Linux): los avisos que interesan y cómo vienen en el buffer.
IN_MODIFY, IN_ATTRIB, IN_CLOSE_WRITE = 0x2, 0x4, 0x8
IN_MOVED_FROM, IN_MOVED_TO, IN_CREATE, IN_DELETE = 0x40, 0x80, 0x100, 0x200
IN_DELETE_SELF, IN_MOVE_SELF, IN_Q_OVERFLOW, IN_IGNORED = 0x400, 0x800, 0x4000, 0x8000
MASCARA_INOTIFY = (IN_MODIFY | IN_ATTRIB | IN_CLOSE_WRITE | IN_MOVED_FROM | IN_MOVED_TO | IN_CREATE | IN_DELETE
                   | IN_DELETE_SELF | IN_MOVE_SELF)
_EVENTO_INOTIFY = struct.Struct('iIII')


def leer_inotify(buffer):
    """[(wd, máscara, nombre)] de un buffer leído de inotify; el nombre es '' si el aviso es del propio objeto."""
    eventos, posicion = [], 0
    while posicion + _EVENTO_INOTIFY.size <= len(buffer):
        wd, mascara, _, largo = _EVENTO_INOTIFY.unpack_from(buffer, posicion)
        posicion += _EVENTO_INOTIFY.size
        nombre = bytes(buffer[posicion:posicion + largo]).split(b'\0', 1)[0]
        posicion += largo
        eventos.append((wd, mascara, os.fsdecode(nombre)))
    return eventos


def _listar(carpeta):
    """(entradas, mtime) de una carpeta, con la fecha estable durante la lista."""
    for _ in range(5):
        antes = os.stat(carpeta).st_mtime_ns
        entradas = set(os.listdir(carpeta))
        if os.stat(carpeta).st_mtime_ns == antes:
            return entradas, antes
    raise OSError('La carpeta no deja de cambiar: ' + carpeta)


# Descriptores que se dejan libres para el resto del motor mientras vigila.
MARGEN_DESCRIPTORES = 512
_tope_por_proceso = None


def _tope_de_descriptores(duro, infinito):
    """Lo más que puede abrir este proceso: el límite duro, y en macOS también kern.maxfilesperproc."""
    global _tope_por_proceso
    tope = None if duro == infinito else duro
    if sys.platform == 'darwin':
        if _tope_por_proceso is None:
            try:
                salida = subprocess.run(['/usr/sbin/sysctl', '-n', 'kern.maxfilesperproc'],
                                        capture_output=True, timeout=5, check=True).stdout
                _tope_por_proceso = int(salida.strip())
            except (OSError, subprocess.SubprocessError, ValueError):
                _tope_por_proceso = 10240  # OPEN_MAX de <sys/syslimits.h>
        tope = _tope_por_proceso if tope is None else min(tope, _tope_por_proceso)
    return tope


def subir_limite_de_archivos(necesarios):
    """Sube el límite blando de archivos abiertos para vigilar `necesarios` rutas; devuelve el que queda.

    macOS da 256 a lo que se abre desde el Finder (`launchctl limit maxfiles`) y kqueue pide un
    descriptor por archivo y carpeta vigilados: en un proyecto de miles de archivos toda
    comprobación fallaba con «Too many open files», aunque la misma orden fuera bien por
    run_command. Sólo se sube el blando, nunca por encima del duro ni de lo que admite el sistema.
    """
    try:
        import resource
    except ImportError:
        return None
    blando, duro = resource.getrlimit(resource.RLIMIT_NOFILE)
    querido = necesarios + MARGEN_DESCRIPTORES
    if blando == resource.RLIM_INFINITY or blando >= querido:
        return blando
    tope = _tope_de_descriptores(duro, resource.RLIM_INFINITY)
    nuevo = querido if tope is None else min(querido, tope)
    if nuevo > blando:
        try:
            resource.setrlimit(resource.RLIMIT_NOFILE, (nuevo, duro))
            blando = nuevo
        except (ValueError, OSError):
            pass
    return blando


class MutationWatch:
    """Observe writes across all snapshots, including writes after their reads.

    Install before validating the baseline; keep alive across the worker await.
    Queue overflow, unsupported platforms and registration errors fail closed.
    This is a publication boundary, not a promise that files stay immutable.

    Cada aviso de una carpeta se explica o deja la vigilancia sucia (`sucio`). Se
    explica si lo que entró o salió de la carpeta es una caché o un temporal que
    no estaba versionado (`nombre_de_cache`), y queda en `caches`
    ({carpeta: nombres}). Con inotify el aviso trae el nombre. Con kqueue no: un
    hilo lista la carpeta en cuanto avisa y compara con la lista anterior. Un
    aviso sin cambio visible (algo se creó y se borró antes de listar), un chmod
    o cualquier aviso de un archivo vigilado no se explican. Queda un hueco: un
    archivo creado y borrado en los mismos ~50 ms en que se crea una caché.
    """

    def __init__(self, snapshots):
        self.fd = None
        self.queue = None
        self.files = []
        # ctime de cada ruta vigilada: macOS avisa con NOTE_ATTRIB también cuando
        # sólo cambia la fecha de acceso (git la mueve al leer .git/index), y eso
        # invalidaba toda comprobación de git aunque nadie escribiera nada. Un
        # acceso no mueve el ctime; un chmod, chown o escritura sí.
        self.ctimes = {}
        self.avisos = False
        self.sucio = False
        self.caches = {}
        self.rutas = {}
        # Por carpeta vigilada (kqueue): sus entradas con la fecha de la carpeta al
        # listarlas, y su modo y dueño al empezar.
        self.listas = {}
        self.fechas = {}
        self.modos = {}
        self.carpetas = set()
        # Lo vigilado por la foto: una caché con algo de esto dentro no es caché.
        self.vigilados = set()
        self._cerrojo = threading.Lock()
        self._parar = threading.Event()
        self._hilo = None
        paths = set()
        for snapshot in snapshots:
            watch = snapshot['watch']
            paths.update(watch['directories'])
            self.carpetas.update(watch['directories'])
            self.vigilados.update(watch['files'])
            for name, value in watch['files'].items():
                path = Path(name)
                if value is not None:
                    paths.add(name)
                # Observe creation of absent Git refs / MERGE_HEAD too.
                while not path.parent.exists():
                    path = path.parent
                paths.add(str(path.parent))
        try:
            if hasattr(select, 'kqueue'):
                limite = subir_limite_de_archivos(len(paths))
                if limite is not None and limite < len(paths) + MARGEN_DESCRIPTORES:
                    raise OSError(errno.EMFILE, f'el proyecto tiene {len(paths)} archivos y carpetas que '
                                  f'vigilar y este proceso sólo puede abrir {limite}')
                self.queue = select.kqueue()
                flags = (select.KQ_NOTE_WRITE | select.KQ_NOTE_DELETE | select.KQ_NOTE_RENAME
                         | select.KQ_NOTE_ATTRIB | select.KQ_NOTE_EXTEND | select.KQ_NOTE_REVOKE)
                for name in sorted(paths):
                    fd = os.open(name, os.O_RDONLY | os.O_NONBLOCK)
                    self.files.append(fd)
                    self.rutas[fd] = name
                    estado = os.fstat(fd)
                    self.ctimes[fd] = estado.st_ctime_ns
                    if name in self.carpetas:
                        self.listas[fd], self.fechas[fd] = _listar(name)
                        self.modos[fd] = (estado.st_mode, estado.st_uid, estado.st_gid)
                    self.queue.control([select.kevent(fd, filter=select.KQ_FILTER_VNODE,
                        flags=select.KQ_EV_ADD | select.KQ_EV_CLEAR, fflags=flags)], 0, 0)
                self._hilo = threading.Thread(target=self._vigilar, name='alfalfa-vigilante', daemon=True)
                self._hilo.start()
            elif sys.platform.startswith('linux'):
                import ctypes
                libc = ctypes.CDLL(None, use_errno=True)
                self.fd = libc.inotify_init1(os.O_NONBLOCK | os.O_CLOEXEC)
                if self.fd < 0:
                    self.fd = None
                    raise OSError(ctypes.get_errno(), 'inotify_init1')
                for name in sorted(paths):
                    wd = libc.inotify_add_watch(self.fd, os.fsencode(name), MASCARA_INOTIFY)
                    if wd < 0:
                        raise OSError(ctypes.get_errno(), 'inotify_add_watch')
                    self.rutas[wd] = name
            else:
                raise OSError('No hay vigilancia de mutaciones disponible.')
        except BaseException:
            self.close()
            raise

    def cache_nueva(self, carpeta, nombre):
        """¿La entrada `nombre` de `carpeta` es una caché o un temporal sin nada versionado ni vigilado dentro?"""
        if not nombre_de_cache(nombre):
            return False
        ruta = os.path.join(carpeta, nombre)
        return not any(v == ruta or v.startswith(ruta + os.sep) for v in self.vigilados)

    def _explicar(self, carpeta, nombre):
        if self.cache_nueva(carpeta, nombre):
            self.caches.setdefault(carpeta, set()).add(nombre)
        else:
            self.sucio = True

    def _vigilar(self):
        # Lista cada carpeta en cuanto avisa: así se ve lo que entró aunque luego salga.
        while not self._parar.is_set():
            with self._cerrojo:
                if self.queue is None:
                    return
                try:
                    self._kqueue(self.queue.control([], 64, 0.05))
                except OSError:
                    self.avisos = self.sucio = True
                    return
            time.sleep(0.001)

    def _kqueue(self, eventos):
        for evento in eventos:
            fd = evento.ident
            try:
                estado = os.fstat(fd)
            except OSError:
                self.avisos = self.sucio = True
                continue
            antes, self.ctimes[fd] = self.ctimes.get(fd), estado.st_ctime_ns
            if fd not in self.listas:
                if evento.fflags & ~select.KQ_NOTE_ATTRIB or estado.st_ctime_ns != antes:
                    self.avisos = self.sucio = True
                continue
            if evento.fflags & (select.KQ_NOTE_DELETE | select.KQ_NOTE_RENAME | select.KQ_NOTE_REVOKE) \
                    or (estado.st_mode, estado.st_uid, estado.st_gid) != self.modos[fd]:
                self.avisos = self.sucio = True
                continue
            if not evento.fflags & (select.KQ_NOTE_WRITE | select.KQ_NOTE_EXTEND):
                if estado.st_ctime_ns != antes:
                    # ctime movido sin entradas nuevas: atributos extendidos, ACL…
                    self.avisos = self.sucio = True
                continue
            self.avisos = True
            carpeta = self.rutas[fd]
            try:
                ahora, fecha = _listar(carpeta)
            except OSError:
                self.sucio = True
                continue
            cambio = ahora ^ self.listas[fd]
            if not cambio and fecha != self.fechas[fd]:
                # Avisó, la carpeta cambió desde la última lista y no se ve nada:
                # algo entró y salió antes de listar. Con la misma fecha es un aviso
                # de algo que ya se vio (llegó después de listar).
                self.sucio = True
            self.listas[fd], self.fechas[fd] = ahora, fecha
            for nombre in cambio:
                self._explicar(carpeta, nombre)

    def _inotify(self, eventos):
        for wd, mascara, nombre in eventos:
            self.avisos = True
            carpeta = self.rutas.get(wd)
            if mascara & (IN_Q_OVERFLOW | IN_IGNORED) or carpeta is None:
                self.sucio = True
            elif nombre and carpeta in self.carpetas:
                self._explicar(carpeta, nombre)
            else:
                # Un aviso del propio objeto: un archivo vigilado, o un chmod o un borrado de la carpeta.
                self.sucio = True

    def changed(self):
        if self.queue is not None:
            with self._cerrojo:
                while self.queue is not None:
                    eventos = self.queue.control([], 64, 0)
                    self._kqueue(eventos)
                    if len(eventos) < 64:
                        break
            return self.avisos
        if self.fd is not None:
            while True:
                try:
                    datos = os.read(self.fd, 65536)
                except BlockingIOError:
                    break
                if not datos:
                    break
                self._inotify(leer_inotify(datos))
            return self.avisos
        return True

    def solo_caches(self):
        """¿Hubo avisos y todos se explican por cachés o temporales no versionados?"""
        return self.changed() and not self.sucio and bool(self.caches)

    def close(self):
        self._parar.set()
        if self._hilo is not None and self._hilo is not threading.current_thread():
            self._hilo.join(timeout=2)
        with self._cerrojo:
            if self.queue is not None:
                self.queue.close()
                self.queue = None
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
        for fd in self.files:
            os.close(fd)
        self.files.clear()


def functional_command(argv):
    """Exclude discovery and green prose; coverage remains a review obligation."""
    if not isinstance(argv, list) or not argv:
        return False
    program = Path(argv[0]).name.lower()
    if program in ('bash', 'sh', 'zsh') and len(argv) == 3 and argv[1] in ('-c', '-lc'):
        return _shell_funcional(str(argv[2]))
    if program == 'node':
        # Sin esto, ningún proyecto de JavaScript podía dejar evidencia y toda
        # aprobación se bloqueaba.
        valores = [str(v) for v in argv[1:]]
        return '--test' in valores or any(
            not v.startswith('-') and v.endswith(('.js', '.mjs', '.cjs', '.ts')) for v in valores[:1])
    if program.startswith(('python', 'pypy')):
        # `python3 -B verificar.py` es el mismo script.
        while len(argv) > 1 and argv[1] in ('-B', '-u', '-E', '-s', '-I'):
            argv = argv[:1] + argv[2:]
        if argv[1:2] == ['-m']:
            return len(argv) > 2 and argv[2] in ('pytest', 'unittest', 'mypy', 'ruff', 'compileall')
        if argv[1:2] == ['-c'] and len(argv) > 2:
            try:
                return any(isinstance(node, ast.Assert) for node in ast.walk(ast.parse(argv[2])))
            except SyntaxError:
                return False
        return len(argv) > 1 and argv[1].endswith('.py')
    if program in ('git', 'rg', 'grep', 'shasum', 'sha256sum', 'ls', 'cat', 'echo', 'true', 'pwd'):
        return False
    return program in ('pytest', 'ruff', 'mypy', 'swift', 'xcodebuild', 'npm', 'npx', 'pnpm',
                       'yarn', 'bun', 'cargo', 'go', 'make', 'cmake', 'ctest', 'gradle', 'mvn',
                       'dotnet', 'ruby', 'rspec', 'bundle') and any(
        str(token).split(':')[0] in ('test', 'tests', 'build', 'check', 'lint', 'typecheck', 'tsc', 'clippy', 'vet',
                  'verify', 'pytest', 'ruff', 'mypy', 'eslint', 'jest', 'vitest', 'noEmit')
        or str(token).startswith(('--noEmit', '-only-testing:')) for token in argv[1:]) \
        or program in ('pytest', 'ruff', 'mypy', 'ctest', 'rspec', 'tsc', 'vitest', 'jest', 'eslint')


def _shell_funcional(texto):
    """¿`cd demo && node --test` prueba algo? Una tubería o un `;` esconden el código de salida."""
    if re.search(r'[|;&<>`$\n]', texto.replace('&&', ' ')):
        return False
    try:
        return any(functional_command(shlex.split(tramo)) for tramo in texto.split('&&'))
    except ValueError:
        return False


TEMPORALES = ('.tmp-alfalfa/',)
# Como en los permisos (PREFIJOS_DE_CACHE): cualquier `.tmp-…/` o `.temp-…/` de
# primer nivel es de temporales, así un integrador puede usar el suyo (.tmp-<app>/).
PREFIJOS_TEMPORALES = ('.tmp-', '.temp-')


def temporal(name):
    """¿`name` (relativo a la raíz) cae en una carpeta de temporales de primer nivel?"""
    return str(name).partition('/')[0].startswith(PREFIJOS_TEMPORALES)


def nombre_de_cache(nombre):
    """¿`nombre` (una entrada, sin barras) es de una caché regenerable o un temporal?

    Las cachés son las que el agente puede borrar sin preguntar
    (permisos.ordenes.CACHES: `.pytest_cache`, `__pycache__`, `.ruff_cache`…), más
    la carpeta `pytest-cache-files-…` que pytest crea y renombra a `.pytest_cache`.
    """
    from ..permisos.ordenes import CACHES
    return nombre in CACHES or nombre.startswith('pytest-cache-files-') or temporal(nombre)


def cambio_sin_trabajo(snapshot, guard):
    """¿Lo único que pasó durante la orden fue crear o borrar cachés no versionadas?

    Cada aviso del vigilante quedó explicado por una entrada de caché nueva o
    borrada (ver `MutationWatch`), y todo lo demás sigue igual: los archivos con
    el mismo contenido, inodo y fechas, y cada carpeta con las mismas entradas
    sin contar esas cachés. Pasa, por ejemplo, con el primer pytest de un repo sin
    .gitignore, que deja `.pytest_cache/` (prueba real con Claude Haiku, 09/11).
    """
    try:
        return bool(guard is not None and guard.solo_caches()
                    and matches(for_mutation_watch(snapshot), tolerar=guard.caches))
    except (OSError, KeyError, TypeError):
        return False


def con_caches(snapshot, guard):
    """La foto con las carpetas de `guard.caches` como han quedado, para que no caduque por ellas.

    Quien la lea luego (`matches`) vuelve a listar con lo ignorado de ahora: las
    cachés nuevas cuentan como ya estaban.
    """
    ignorado = _current_ignored({**snapshot, 'watch': {**snapshot['watch'], '_use_snapshot_ignored': False}})
    if ignorado is None:
        return snapshot
    carpetas = dict(snapshot['watch']['directories'])
    for carpeta in guard.caches:
        if carpeta in carpetas:
            carpetas[carpeta] = _entries(Path(carpeta), ignorado, snapshot.get('root'))
    return {**snapshot, 'watch': {**snapshot['watch'], 'directories': carpetas, 'ignored': sorted(ignorado)}}

def con_escrituras_del_arnes(snapshot, rutas, borrada=None):
    """La foto con lo que el propio arnés acaba de escribir o borrar (`rutas`, absolutas: los archivos
    de una habilidad aprendida del proyecto, en .alfalfa/skills), para que la comprobación no caduque
    por ellos. `borrada`: la carpeta de la habilidad si se borró entera.

    Sólo vale para una comprobación que seguía vigente justo antes de esa escritura. No se vuelve a
    listar ninguna carpeta que ya se vigilaba: sólo se le añaden (o quitan) los nombres del camino hacia
    esas rutas, así que un archivo nuevo de otro proceso la sigue caducando. Las carpetas que creó el
    arnés pasan a vigilarse con su listado de ahora, y los archivos escritos, con su huella. Sólo deja de
    vigilarse lo que había dentro de la habilidad borrada; cualquier otra carpeta que falte la caduca.
    """
    rutas = [Path(r) for r in rutas]
    borrada = Path(borrada) if borrada else None
    raiz = Path(snapshot.get('root') or '/')
    watch = snapshot['watch']
    carpetas = {}
    for nombre, valor in watch['directories'].items():
        carpeta = Path(nombre)
        if borrada is not None and (carpeta == borrada or borrada in carpeta.parents) and not carpeta.exists():
            continue
        entradas = list(valor)
        for ruta in rutas:
            if carpeta not in ruta.parents:
                continue
            hijo = ruta.relative_to(carpeta).parts[0]
            if (carpeta / hijo).exists() and hijo not in entradas:
                entradas.append(hijo)
            elif not (carpeta / hijo).exists() and hijo in entradas:
                entradas.remove(hijo)
        carpetas[nombre] = sorted(entradas)
    ignorado = set(watch.get('ignored') or [])
    for ruta in rutas:
        for carpeta in reversed(ruta.parents):
            if carpeta == raiz or raiz not in carpeta.parents or str(carpeta) in carpetas or not carpeta.is_dir():
                continue
            # Una carpeta nueva del arnés: su contenido sólo puede ser lo que acaba de escribir.
            carpetas[str(carpeta)] = _entries(carpeta, ignorado, snapshot.get('root'))
    archivos = dict(watch['files'])
    for ruta in rutas:
        archivos[str(ruta)] = _file(ruta)
    return {**snapshot, 'watch': {**watch, 'directories': carpetas, 'files': archivos}}


# Los revisores nombran su carpeta de capturas a su manera: independent-review,
# revision-qa, review-current-1848…
CARPETAS_DEL_REVISOR = ('independent-review',)
PREFIJOS_DEL_REVISOR = ('revision', 'review', 'independent-review')


def escrito_por_el_revisor(name):
    """¿Lo escribió el revisor? `name` es relativo a la raíz del workspace."""
    partes = name.split('/')
    if 'evidence' not in partes and 'evidencia' not in partes:
        return any(parte in CARPETAS_DEL_REVISOR for parte in partes)
    return any(parte.lower().startswith(PREFIJOS_DEL_REVISOR) for parte in partes)


def _del_revisor(path, root):
    try:
        return escrito_por_el_revisor(Path(path).relative_to(root).as_posix())
    except ValueError:
        return False


def _file(path):
    if not path.exists() and not path.is_symlink():
        return None
    stat = path.lstat()
    if path.is_symlink():
        data = os.readlink(path).encode()
    elif path.is_dir():
        # Un submódulo registrado (gitlink) es una carpeta: leerla como archivo
        # tumbaba la huella con «Is a directory» (pasó con GLTFKit2).
        data = ('dir:' + '\0'.join(sorted(os.listdir(path)))).encode()
    else:
        data = path.read_bytes()
    return [hashlib.sha256(data).hexdigest(), stat.st_mode, stat.st_ino,
            stat.st_mtime_ns, stat.st_ctime_ns]


def _entries(path, ignored, root=None):
    # Las capturas que el revisor deja en la evidencia no son el trabajo: vigilarlas
    # caducaba la evidencia al terminar cada revisión («La evidencia caducó»).
    return sorted(p.name for p in path.iterdir()
                  if p.name != '.git' and not temporal(p.name) and str(p) not in ignored
                  and not (root and _del_revisor(p, root)))


def _shared(path, refs):
    """Lo que le importa a este worktree de un archivo de `.git` común a todos.

    `.git/config` y `packed-refs` los comparten todos los worktrees del repo.
    Cada `git push -u` de otro ticket reescribe config con su `[branch "…"]`,
    y comparar fecha e inodo caducaba la evidencia de todas las revisiones en
    curso («La evidencia caducó»). Se compara el
    contenido sin las secciones de ramas, y de packed-refs sólo las refs
    propias.
    """
    try:
        text = path.read_bytes().decode(errors='surrogateescape')
    except FileNotFoundError:
        return None
    kept = []
    if path.name == 'packed-refs':
        propia = False
        for line in text.splitlines():
            if not line.startswith('^'):
                propia = line.split(' ', 1)[-1] in refs
            if propia:
                kept.append(line)
    else:
        rama = False
        for line in text.splitlines():
            if line.lstrip().startswith('['):
                rama = re.match(r'\s*\[\s*branch\b', line) is not None
            if not rama:
                kept.append(line)
    return hashlib.sha256('\n'.join(kept).encode(errors='surrogateescape')).hexdigest()


def _current_ignored(snapshot):
    """Read the current Git ignore view so new build artifacts stay out of a review receipt."""
    if snapshot.get('git') is None:
        return set()
    if snapshot.get('watch', {}).get('_use_snapshot_ignored'):
        try:
            return set(snapshot['watch']['ignored'])
        except (KeyError, TypeError):
            return None
    try:
        result = subprocess.run(
            ['git', '-C', snapshot['root'], 'ls-files', '-oi', '--exclude-standard',
             '--directory', '-z'],
            capture_output=True, timeout=15, check=True,
            env={**os.environ, 'GIT_OPTIONAL_LOCKS': '0'})
        names = result.stdout.decode(errors='surrogateescape').split('\0')
        root = Path(snapshot['root'])
        return {str(root / name.rstrip('/')) for name in names if name}
    except (OSError, subprocess.SubprocessError, KeyError, TypeError):
        return None


def matches(snapshot, tolerar=None):
    """¿Sigue todo como en la foto? `tolerar` ({carpeta: nombres}) no cuenta esas entradas."""
    if not isinstance(snapshot, dict) or not snapshot.get('watch'):
        return False
    tolerar = tolerar or {}

    def igual(name, value):
        fuera = tolerar.get(name) or ()
        actuales = _entries(Path(name), ignored, snapshot.get('root'))
        return [n for n in actuales if n not in fuera] == [n for n in value if n not in fuera]
    try:
        watch = snapshot['watch']
        ignored = _current_ignored(snapshot)
        if ignored is None:
            return False
        return (all(_same_file(Path(name), value) for name, value in watch['files'].items())
                and all(_shared(Path(name), refs) == value
                        for name, (value, refs) in watch.get('shared', {}).items())
                and all(igual(name, value) for name, value in watch['directories'].items())
                and ('index' not in watch or _preparado(snapshot['root']) == watch['index'][1]))
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        return False


def same_context(left, right):
    """Compare receipts while treating the current ignored-path listing as derived data."""
    if not isinstance(left, dict) or not isinstance(right, dict):
        return False
    try:
        left = {**left, 'watch': {**left['watch'], 'ignored': [], '_use_snapshot_ignored': False}}
        right = {**right, 'watch': {**right['watch'], 'ignored': [], '_use_snapshot_ignored': False}}
    except (KeyError, TypeError):
        return False
    return left == right


def for_mutation_watch(snapshot):
    """Use the already-read ignore list while a filesystem watcher is active."""
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get('watch'), dict):
        return snapshot
    return {**snapshot, 'watch': {**snapshot['watch'], '_use_snapshot_ignored': True}}


def _preparado(root):
    """Huella de lo preparado en el índice: modo, blob, ruta y marcas
    (assume-unchanged, skip-worktree), sin los datos de stat."""
    result = subprocess.run(['git', '-C', str(root), 'ls-files', '--stage', '-v', '--debug'],
                            capture_output=True, timeout=15, check=True,
                            env={**os.environ, 'GIT_OPTIONAL_LOCKS': '0'})
    lineas = []
    for linea in result.stdout.split(b'\n'):
        if not linea.startswith(b' '):
            lineas.append(linea)
        elif b'flags:' in linea:
            lineas.append(linea[linea.index(b'flags:'):])
    return hashlib.sha256(b'\n'.join(lineas)).hexdigest()


def _same_file(path, expected):
    if expected is None:
        return not path.exists() and not path.is_symlink()
    stat = path.lstat()
    if [stat.st_mode, stat.st_ino, stat.st_mtime_ns, stat.st_ctime_ns] == expected[1:]:
        return True
    # La limpieza comparte los archivos repetidos con un clon: mismo contenido y
    # fecha, pero otro inodo y otro ctime. Comparar sólo metadatos caducaba la
    # evidencia de los tickets en revisión. Si el modo
    # y la fecha siguen iguales, decide el contenido.
    if [stat.st_mode, stat.st_mtime_ns] != [expected[1], expected[3]]:
        return False
    actual = _file(path)
    return actual is not None and actual[0] == expected[0]


def _status(porcelain):
    return '\n'.join(line for line in porcelain.split('\n')
                     if not escrito_por_el_revisor(line[3:].split(' -> ')[-1].strip('"').rstrip('/')))


def _capture(root):
    root = Path(root).resolve()
    def git(*args):
        result = subprocess.run(['git', '-C', str(root), *args], capture_output=True,
                                timeout=15, check=True,
                                env={**os.environ, 'GIT_OPTIONAL_LOCKS': '0'})
        return result.stdout.decode(errors='surrogateescape').rstrip('\n')
    ignored = set()
    git_files = []
    shared = {}
    try:
        gitdir = Path(git('rev-parse', '--absolute-git-dir'))
        common = Path(git('rev-parse', '--git-common-dir'))
        if not common.is_absolute():
            common = (root / common).resolve()
        names = set(git('ls-files', '-co', '--exclude-standard', '-z').split('\0')) - {''}
        ignored = {str(root / name.rstrip('/')) for name in
                   git('ls-files', '-oi', '--exclude-standard', '--directory', '-z').split('\0') if name}
        # El índice no entra por fecha e inodo: git reescribe sus datos de stat
        # cuando los archivos cambian de inodo (la limpieza los comparte) sin que
        # cambie nada preparado. Importa lo preparado, `ls-files --stage`.
        git_files = [gitdir / name for name in ('HEAD', 'MERGE_HEAD')]
        index = [str(gitdir / 'index'), _preparado(root)]
        git_files.append(common / 'info/exclude')
        marker = Path(git('rev-parse', '--show-toplevel')) / '.git'
        if marker.is_file() or marker.is_symlink():
            git_files.append(marker)
        # Una rama de integración compartida puede avanzar mientras este
        # worktree se revisa; sólo importa la referencia que señala su HEAD.
        refs = []
        ref = (gitdir / 'HEAD').read_text().strip()
        if ref.startswith('ref: '):
            git_files.append(common / ref[5:])
            refs.append(ref[5:])
        for name in ('packed-refs', 'config'):
            shared[str(common / name)] = [_shared(common / name, refs), sorted(refs)]
        git_before = {str(path): _file(path) for path in git_files}
        try:
            head = git('rev-parse', '--verify', 'HEAD')
        except subprocess.CalledProcessError:
            head = None  # Valid repository with an unborn branch.
        context = {'head': head, 'status': _status(git('status', '--porcelain')),
                   'conflicts': git('diff', '--name-only', '--diff-filter=U'),
                   'merge_head': _file(gitdir / 'MERGE_HEAD'),
                   'gitdir': str(gitdir)}
    except subprocess.CalledProcessError:
        # A broken repository must not masquerade as a plain directory.
        if (root / '.git').exists():
            raise ValueError('No se pudo verificar el estado Git actual.')
        names = {str(p.relative_to(root)) for p in root.rglob('*')
                 if p.is_file() or p.is_symlink()}
        context = None
    # .tmp-alfalfa/ (o el .tmp-… de un integrador) son temporales de los agentes (cachés de Go, builds de Swift):
    # nunca son parte del trabajo, y si una rama los llegó a versionar, miles de
    # artefactos de compilación entraban en cada huella. Git sigue informando de
    # ellos en `status` si cambian.
    names = {name for name in names
             if not temporal(name) and not escrito_por_el_revisor(name)}
    paths = [root / name for name in sorted(names)]
    directories = {root}
    for path in paths:
        directories.update(parent for parent in path.parents if parent == root or root in parent.parents)
    directory_before = {str(path): _entries(path, ignored, root) for path in directories}
    watch = {'files': {str(path): _file(path) for path in paths + git_files},
             'shared': shared,
             'directories': directory_before,
             'ignored': sorted(ignored)}
    if context is not None:
        watch['index'] = index
    result = {'root': str(root), 'git': context, 'watch': watch,
              'sources': {str(path): watch['files'][str(path)] for path in paths}}
    if not matches(result):
        raise ValueError('El workspace cambió mientras se tomaba su huella.')
    if context is not None:
        if any(watch['files'][name] != value for name, value in git_before.items()):
            raise ValueError('Git cambió mientras se tomaba su huella.')
        final_names = {name for name in git('ls-files', '-co', '--exclude-standard', '-z').split('\0')
                       if name and not temporal(name) and not escrito_por_el_revisor(name)}
        if final_names != names or _status(git('status', '--porcelain')) != context['status']:
            raise ValueError('El workspace cambió mientras se tomaba su huella.')
        if not matches(result):
            raise ValueError('Git o el workspace cambiaron durante la comprobación final.')
    return result


async def capture(root):
    for attempt in range(3):
        try:
            return await asyncio.to_thread(_capture, root)
        except ValueError as exc:
            if str(exc) != 'Git cambió mientras se tomaba su huella.' or attempt == 2:
                raise
            # HEAD o el índice avanzaron mientras se leían. Repetimos la toma
            # completa para capturar un estado coherente, sin aceptar la mezcla.
            await asyncio.sleep(0.01 * (attempt + 1))
