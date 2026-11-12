"""Local observations, never model-supplied attestations.

Una comprobación vale para el código de ahora si la huella del workspace de
después de la orden es igual a la de antes. Nada vigila el workspace mientras la
orden corre: un archivo que se escribe y se deshace en ese rato no se ve, y es
una pérdida aceptada a cambio de no abrir un descriptor por archivo (en un
proyecto de miles de archivos abierto desde el Finder, el límite de 256 tumbaba
toda comprobación).

La huella es barata como la de git: un archivo cuyo stat (tamaño, fechas,
inodo, modo) no cambió no se vuelve a leer, y los hashes se guardan entre
comprobaciones con el stat como clave. Git se consulta en un hilo aparte.
"""
import ast
import asyncio
import hashlib
import os
import re
import shlex
import stat as _stat
import subprocess
import threading
import time
from pathlib import Path

# Racy mtime, como en git: si la fecha de un archivo cae tan cerca del momento en
# que se leyó que otra escritura en el mismo tick del reloj podría dejar el mismo
# stat, el stat no basta y decide el contenido. Linux pone las fechas con un
# reloj de varios milisegundos; el margen cubre eso y un sistema de archivos lento.
MARGEN_RACY_NS = 2_000_000_000

# Hashes ya calculados: {ruta: (stat, sha256, cuándo se leyó)}. El stat va
# entero (tamaño, mtime, ctime, inodo, modo y dispositivo) para que cualquier
# escritura, chmod o reemplazo lo invalide.
_HASHES = {}
_HASHES_MAXIMO = 400_000
_cerrojo_hashes = threading.Lock()

# Una carpeta que no es un repositorio se recorre entera: con más archivos que
# esto (la carpeta personal, un disco) no se toma huella y la comprobación queda
# sin verificar en vez de tardar minutos.
MAX_ARCHIVOS_SIN_GIT = 50_000


def _clave_stat(estado):
    return (estado.st_size, estado.st_mtime_ns, estado.st_ctime_ns, estado.st_ino, estado.st_mode,
            estado.st_dev)


def _racy(estado, momento):
    """¿El stat de este archivo podría repetirse tras otra escritura hecha después de `momento`?"""
    return max(estado.st_mtime_ns, estado.st_ctime_ns) >= momento - MARGEN_RACY_NS


def olvidar_hashes():
    """Vacía los hashes guardados (para las pruebas y las mediciones)."""
    with _cerrojo_hashes:
        _HASHES.clear()


def _hash(path, estado):
    """sha256 del contenido de `path`, sin releerlo si su stat no cambió desde la última vez."""
    modo = estado.st_mode
    if _stat.S_ISLNK(modo):
        return hashlib.sha256(os.readlink(path).encode(errors='surrogateescape')).hexdigest()
    if _stat.S_ISDIR(modo):
        # Un submódulo registrado (gitlink) es una carpeta: leerla como archivo
        # tumbaba la huella con «Is a directory» (pasó con GLTFKit2).
        return hashlib.sha256(('dir:' + '\0'.join(sorted(os.listdir(path)))).encode()).hexdigest()
    if not _stat.S_ISREG(modo):
        # Un FIFO o un socket: leerlo bloquearía la huella.
        return hashlib.sha256(('especial:' + str(_stat.S_IFMT(modo))).encode()).hexdigest()
    ruta = str(path)
    clave = _clave_stat(estado)
    guardado = _HASHES.get(ruta)
    if guardado is not None and guardado[0] == clave and not _racy(estado, guardado[2]):
        return guardado[1]
    leido = time.time_ns()
    with open(path, 'rb') as archivo:
        sha = hashlib.file_digest(archivo, 'sha256').hexdigest()
    with _cerrojo_hashes:
        if len(_HASHES) >= _HASHES_MAXIMO:
            _HASHES.clear()
        _HASHES[ruta] = (clave, sha, leido)
    return sha


def _lstat(path):
    try:
        return os.lstat(path)
    except (FileNotFoundError, NotADirectoryError):
        return None


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


def _en_cache(name):
    """¿`name` (relativo a la raíz) está dentro de una caché o un temporal?"""
    return any(nombre_de_cache(parte) for parte in str(name).split('/')[:-1])


def _cache_sin_trabajo(carpeta, nombre, vigilados):
    """¿La entrada `nombre` de `carpeta` es una caché o un temporal sin nada vigilado dentro?"""
    if not nombre_de_cache(nombre):
        return False
    ruta = os.path.join(carpeta, nombre)
    return not any(v == ruta or v.startswith(ruta + os.sep) for v in vigilados)


def tras_la_orden(snapshot):
    """(¿vale?, foto con la que queda) al acabar la orden: la huella de ahora contra la de antes.

    Vale si los archivos, el índice, HEAD y las refs siguen como en la foto y cada
    carpeta tiene las mismas entradas, sin contar las cachés y temporales que
    entraron o salieron sin nada vigilado dentro: el primer pytest de un repo sin
    .gitignore deja `.pytest_cache/` (prueba real con Claude Haiku, 09/11). La foto
    devuelta lleva esas carpetas como han quedado, para que la comprobación no
    caduque luego por ellas.
    """
    if not isinstance(snapshot, dict) or not snapshot.get('watch'):
        return False, snapshot
    try:
        watch = snapshot['watch']
        root = snapshot.get('root')
        ignored = _current_ignored(snapshot)
        if ignored is None:
            return False, snapshot
        carpetas = {}
        for name, antes in watch['directories'].items():
            ahora = _entries(name, ignored, root)
            if ahora != list(antes):
                cambio = set(ahora) ^ set(antes)
                if not cambio or not all(_cache_sin_trabajo(name, n, watch['files']) for n in cambio):
                    return False, snapshot
            carpetas[name] = ahora
        if not _sin_cambios(snapshot):
            return False, snapshot
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        return False, snapshot
    if carpetas == watch['directories']:
        return True, snapshot
    return True, {**snapshot, 'watch': {**watch, 'directories': carpetas, 'ignored': sorted(ignored)}}


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


def _file(path):
    """[sha256, modo, inodo, mtime_ns, ctime_ns, tamaño] de `path`, o None si no existe."""
    estado = _lstat(path)
    if estado is None:
        return None
    return [_hash(path, estado), estado.st_mode, estado.st_ino, estado.st_mtime_ns, estado.st_ctime_ns,
            estado.st_size]


def _entries(path, ignored, root=None):
    """Las entradas de la carpeta `path` que cuentan para la huella, ordenadas.

    Las capturas que el revisor deja en la evidencia no son el trabajo: vigilarlas
    caducaba la evidencia al terminar cada revisión («La evidencia caducó»). Va
    con cadenas y no con Path: en un proyecto de miles de carpetas, pathlib era
    lo más lento de la huella.
    """
    carpeta = str(path)
    prefijo = None
    if root:
        raiz = str(root).rstrip('/')
        if carpeta == (raiz or '/'):
            prefijo = ''
        elif carpeta.startswith(raiz + '/'):
            prefijo = carpeta[len(raiz) + 1:] + '/'
    return sorted(nombre for nombre in os.listdir(carpeta)
                  if nombre != '.git' and not temporal(nombre)
                  and os.path.join(carpeta, nombre) not in ignored
                  and not (prefijo is not None and escrito_por_el_revisor(prefijo + nombre)))


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


def _sin_cambios(snapshot):
    """¿Los archivos, lo compartido de .git y lo preparado en el índice siguen como en la foto?"""
    watch = snapshot['watch']
    tomada = watch.get('tomada_ns')
    return (all(_same_file(name, value, tomada) for name, value in watch['files'].items())
            and all(_shared(Path(name), refs) == value
                    for name, (value, refs) in watch.get('shared', {}).items())
            and ('index' not in watch or _preparado(snapshot['root']) == watch['index'][1]))


def matches(snapshot):
    """¿Sigue todo como en la foto?"""
    if not isinstance(snapshot, dict) or not snapshot.get('watch'):
        return False
    try:
        watch = snapshot['watch']
        ignored = _current_ignored(snapshot)
        if ignored is None:
            return False
        root = snapshot.get('root')
        return (all(_entries(name, ignored, root) == list(value)
                    for name, value in watch['directories'].items())
                and _sin_cambios(snapshot))
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        return False


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


def _same_file(name, expected, tomada=None):
    """¿`name` sigue como `expected` (lo que dio `_file`)? Sólo se lee si el stat no basta.

    `tomada` es cuándo empezó la foto (las de versiones anteriores no lo traen):
    si la fecha del archivo cae cerca, el stat podría repetirse tras otra
    escritura (racy mtime) y decide el contenido.
    """
    estado = _lstat(name)
    if expected is None:
        return estado is None
    if estado is None:
        return False
    if len(expected) > 5 and expected[5] != estado.st_size:
        return False
    if [estado.st_mode, estado.st_ino, estado.st_mtime_ns, estado.st_ctime_ns] == expected[1:5]:
        if tomada is None or not _racy(estado, tomada):
            return True
        return _hash(name, estado) == expected[0]
    # La limpieza comparte los archivos repetidos con un clon: mismo contenido y
    # fecha, pero otro inodo y otro ctime. Comparar sólo metadatos caducaba la
    # evidencia de los tickets en revisión. Si el modo
    # y la fecha siguen iguales, decide el contenido.
    if [estado.st_mode, estado.st_mtime_ns] != [expected[1], expected[3]]:
        return False
    return _hash(name, estado) == expected[0]


def _status(porcelain):
    return '\n'.join(line for line in porcelain.split('\n')
                     if not escrito_por_el_revisor(line[3:].split(' -> ')[-1].strip('"').rstrip('/')))


def _sin_git(root):
    """Los archivos de una carpeta que no es un repositorio, relativos a ella."""
    names = set()
    for carpeta, subcarpetas, archivos in os.walk(root, onerror=_fallo_al_recorrer):
        relativa = os.path.relpath(carpeta, root)
        relativa = '' if relativa == '.' else relativa + '/'
        if not relativa:
            # Los temporales de primer nivel no entran en la huella: ni se recorren.
            subcarpetas[:] = [s for s in subcarpetas if not temporal(s)]
        for nombre in archivos + [s for s in subcarpetas if os.path.islink(os.path.join(carpeta, s))]:
            names.add(relativa + nombre)
        if len(names) > MAX_ARCHIVOS_SIN_GIT:
            raise ValueError(f'La carpeta no es un repositorio git y tiene más de {MAX_ARCHIVOS_SIN_GIT} '
                             'archivos.')
    return names


def _fallo_al_recorrer(error):
    raise error


def _capture(root):
    tomada = time.time_ns()
    root = Path(root).resolve()
    def git(*args):
        result = subprocess.run(['git', '-C', str(root), *args], capture_output=True,
                                timeout=15, check=True,
                                env={**os.environ, 'GIT_OPTIONAL_LOCKS': '0'})
        return result.stdout.decode(errors='surrogateescape').rstrip('\n')

    def listar():
        versionados = set(git('ls-files', '-z').split('\0')) - {''}
        # Lo que hay sin versionar dentro de una caché (`__pycache__/*.pyc` en un repo
        # sin .gitignore) lo regenera cada orden: no es el trabajo.
        otros = {name for name in git('ls-files', '-o', '--exclude-standard', '-z').split('\0')
                 if name and not _en_cache(name)}
        return versionados | otros
    ignored = set()
    git_files = []
    shared = {}
    try:
        gitdir = Path(git('rev-parse', '--absolute-git-dir'))
        common = Path(git('rev-parse', '--git-common-dir'))
        if not common.is_absolute():
            common = (root / common).resolve()
        names = listar()
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
        names = _sin_git(root)
        context = None
    # .tmp-alfalfa/ (o el .tmp-… de un integrador) son temporales de los agentes (cachés de Go, builds de Swift):
    # nunca son parte del trabajo, y si una rama los llegó a versionar, miles de
    # artefactos de compilación entraban en cada huella. Git sigue informando de
    # ellos en `status` si cambian.
    names = {name for name in names
             if not temporal(name) and not escrito_por_el_revisor(name)}
    raiz = str(root)
    paths = [os.path.join(raiz, name) for name in sorted(names)]
    # Las carpetas de cada archivo hasta la raíz, con cadenas: con Path y sus
    # `parents` esto costaba segundos en un proyecto de 20 000 archivos.
    relativas = set()
    for name in names:
        corte = name.rfind('/')
        while corte > 0 and name[:corte] not in relativas:
            relativas.add(name[:corte])
            corte = name.rfind('/', 0, corte)
    directories = [raiz] + [os.path.join(raiz, carpeta) for carpeta in relativas]
    directory_before = {carpeta: _entries(carpeta, ignored, root) for carpeta in directories}
    watch = {'files': {**{path: _file(path) for path in paths}, **{str(path): _file(path) for path in git_files}},
             'shared': shared,
             'directories': directory_before,
             'ignored': sorted(ignored),
             'tomada_ns': tomada}
    if context is not None:
        watch['index'] = index
    result = {'root': str(root), 'git': context, 'watch': watch,
              'sources': {path: watch['files'][path] for path in paths}}
    if context is not None:
        if any(watch['files'][name] != value for name, value in git_before.items()):
            raise ValueError('Git cambió mientras se tomaba su huella.')
        final_names = {name for name in listar() if not temporal(name) and not escrito_por_el_revisor(name)}
        if final_names != names or _status(git('status', '--porcelain')) != context['status']:
            raise ValueError('El workspace cambió mientras se tomaba su huella.')
    if not matches(result):
        raise ValueError('El workspace cambió mientras se tomaba su huella.')
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
