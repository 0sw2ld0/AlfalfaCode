"""Local observations, never model-supplied attestations.

Git is queried in a worker thread. The saved watch also permits synchronous
evidence readers to invalidate a check without launching a process.
"""
import asyncio
import ast
import hashlib
import os
from pathlib import Path
import re
import shlex
import subprocess
import select
import sys


class MutationWatch:
    """Observe writes across all snapshots, including writes after their reads.

    Install before validating the baseline; keep alive across the worker await.
    Queue overflow, unsupported platforms and registration errors fail closed.
    This is a publication boundary, not a promise that files stay immutable.
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
        self.sucio = False
        paths = set()
        for snapshot in snapshots:
            watch = snapshot['watch']
            paths.update(watch['directories'])
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
                self.queue = select.kqueue()
                flags = (select.KQ_NOTE_WRITE | select.KQ_NOTE_DELETE | select.KQ_NOTE_RENAME
                         | select.KQ_NOTE_ATTRIB | select.KQ_NOTE_EXTEND | select.KQ_NOTE_REVOKE)
                for name in sorted(paths):
                    fd = os.open(name, os.O_RDONLY | os.O_NONBLOCK)
                    self.files.append(fd)
                    self.ctimes[fd] = os.fstat(fd).st_ctime_ns
                    self.queue.control([select.kevent(fd, filter=select.KQ_FILTER_VNODE,
                        flags=select.KQ_EV_ADD | select.KQ_EV_CLEAR, fflags=flags)], 0, 0)
            elif sys.platform.startswith('linux'):
                import ctypes
                libc = ctypes.CDLL(None, use_errno=True)
                self.fd = libc.inotify_init1(os.O_NONBLOCK | os.O_CLOEXEC)
                if self.fd < 0:
                    self.fd = None
                    raise OSError(ctypes.get_errno(), 'inotify_init1')
                for name in sorted(paths):
                    # MODIFY, ATTRIB, CLOSE_WRITE, moves, create, delete, self changes.
                    if libc.inotify_add_watch(self.fd, os.fsencode(name), 0x00000FCE) < 0:
                        raise OSError(ctypes.get_errno(), 'inotify_add_watch')
            else:
                raise OSError('No hay vigilancia de mutaciones disponible.')
        except BaseException:
            self.close()
            raise

    def changed(self):
        if self.queue is not None:
            if self.sucio:
                return True
            # Los eventos se consumen al leerlos: un cambio visto una vez se recuerda.
            for evento in self.queue.control([], 64, 0):
                if evento.fflags & ~select.KQ_NOTE_ATTRIB:
                    self.sucio = True
                    continue
                try:
                    if os.fstat(evento.ident).st_ctime_ns != self.ctimes.get(evento.ident):
                        self.sucio = True
                except OSError:
                    self.sucio = True
            return self.sucio
        if self.fd is not None:
            try:
                return bool(os.read(self.fd, 65536))
            except BlockingIOError:
                return False
        return True

    def close(self):
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


def matches(snapshot):
    if not isinstance(snapshot, dict) or not snapshot.get('watch'):
        return False
    try:
        watch = snapshot['watch']
        ignored = _current_ignored(snapshot)
        if ignored is None:
            return False
        return (all(_same_file(Path(name), value) for name, value in watch['files'].items())
                and all(_shared(Path(name), refs) == value
                        for name, (value, refs) in watch.get('shared', {}).items())
                and all(_entries(Path(name), ignored, snapshot.get('root')) == value
                        for name, value in watch['directories'].items())
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
