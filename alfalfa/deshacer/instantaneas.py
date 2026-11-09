"""Instantáneas del contenido del espacio de trabajo en un git paralelo.

Antes y después de cada turno se guarda lo que hay en la carpeta de trabajo en un
repositorio aparte, fuera del proyecto: `$ALFALFA_HOME/instantaneas/<clave>`, con
`--git-dir` ahí y `--work-tree` en la carpeta. El `.git` del proyecto no se toca
(ni se lee su índice) y funciona igual en una carpeta que no es repositorio.

- Una carpeta, un almacén (la clave es la huella de su ruta): las sesiones
  bifurcadas siguen en la misma carpeta y comparten objetos.
- Cada sesión tiene su índice (`indices/<sesión>`), así git sólo vuelve a leer lo
  que cambió de fecha o tamaño desde la última instantánea.
- Se respetan los `.gitignore` de la carpeta y su `.git/info/exclude`, y se saltan
  las cachés que ya conocen los permisos (`permisos.ordenes.CACHES`), las carpetas
  de temporales (`.tmp-…/`), `node_modules/` y los entornos de Python.
- Un archivo de más de `TOPE_ARCHIVO` bytes no se guarda (queda en `grandes`).
- Cada instantánea es un commit con su ref `refs/alfalfa/<sesión>/<nombre>`; la
  limpieza borra las refs de más de `DIAS` días y, si el almacén pasa de
  `TOPE_TOTAL`, también las más viejas hasta bajar del tope.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

CARPETA = 'instantaneas'
TOPE_ARCHIVO = 5 * 1024 * 1024
# Archivos nuevos o cambiados de una vez: más que esto no es un proyecto, es la casa entera.
TOPE_ARCHIVOS = 60_000
DIAS = 30
TOPE_TOTAL = 2 * 1024 ** 3
TIEMPO = 90
# Carpetas pesadas que se reinstalan y no son trabajo del usuario.
PESADAS = ('node_modules/', '.venv/', 'venv/', '.tox/', '.DS_Store')
AUTOR = {'GIT_AUTHOR_NAME': 'AlfalfaCode', 'GIT_AUTHOR_EMAIL': 'alfalfa@localhost',
         'GIT_COMMITTER_NAME': 'AlfalfaCode', 'GIT_COMMITTER_EMAIL': 'alfalfa@localhost'}
_NOMBRE = re.compile(r'[^A-Za-z0-9._-]+')


class SinInstantanea(Exception):
    """No se pudo guardar o leer una instantánea; el texto dice por qué."""


def _seguro(nombre):
    """Un trozo de ref válido: sin barras, espacios ni `..`."""
    limpio = _NOMBRE.sub('-', str(nombre)).strip('.-')[:80]
    return limpio.replace('..', '-') or 'x'


def sha256_de(path):
    """Huella sha256 del archivo (como `changes`), 'DELETED' si no está o None si no se puede leer."""
    path = Path(path)
    if not path.exists() and not path.is_symlink():
        return 'DELETED'
    try:
        if path.is_symlink() or not path.is_file():
            return None
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _ignore_global():
    """Dónde está el ignore global del usuario: `core.excludesFile` o el de XDG."""
    candidatas = []
    try:
        salida = subprocess.run(['git', 'config', '--global', '--path', '--get', 'core.excludesFile'],
                                capture_output=True, text=True, timeout=5,
                                env={k: os.environ[k] for k in ('PATH', 'HOME', 'LANG') if k in os.environ})
        if salida.returncode == 0 and salida.stdout.strip():
            candidatas.append(Path(salida.stdout.strip()).expanduser())
    except (OSError, subprocess.SubprocessError):
        pass
    candidatas.append(Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config') / 'git' / 'ignore')
    return [c for c in candidatas if c.is_file()]


class Almacen:
    """El git paralelo de una carpeta de trabajo."""

    def __init__(self, hogar, cwd):
        self.cwd = Path(cwd).expanduser().resolve()
        self.clave = hashlib.sha256(str(self.cwd).encode()).hexdigest()[:16]
        self.dir = Path(hogar) / CARPETA / self.clave

    @classmethod
    def de_clave(cls, hogar, clave, cwd):
        almacen = cls(hogar, cwd)
        if clave and clave != almacen.clave:
            almacen.clave = _seguro(clave)
            almacen.dir = Path(hogar) / CARPETA / almacen.clave
        return almacen

    # MARK: - git

    def _entorno(self, sesion=None):
        entorno = {k: os.environ[k] for k in ('PATH', 'HOME', 'LANG', 'TMPDIR') if k in os.environ}
        entorno.update(AUTOR, GIT_DIR=str(self.dir), GIT_WORK_TREE=str(self.cwd), GIT_CONFIG_NOSYSTEM='1',
                       # Ni la configuración del usuario (hooks, fsmonitor, filtros): su ignore global se copia.
                       GIT_CONFIG_GLOBAL=os.devnull,
                       GIT_TERMINAL_PROMPT='0', GIT_OPTIONAL_LOCKS='0', LC_ALL='C')
        if sesion:
            entorno['GIT_INDEX_FILE'] = str(self.dir / 'indices' / _seguro(sesion))
        return entorno

    def git(self, *args, sesion=None, entrada=None, literal=False):
        entorno = self._entorno(sesion)
        if literal:
            entorno['GIT_LITERAL_PATHSPECS'] = '1'
        orden = ['git', '-c', 'core.autocrlf=false', '-c', 'core.safecrlf=false', '-c', 'core.quotepath=off',
                 '-c', 'core.fsmonitor=false', '-c', 'gc.auto=0', '-c', 'core.hooksPath=/dev/null',
                 '-c', 'core.excludesFile=' + str(self.dir / 'info' / 'exclude-global'), *args]
        try:
            salida = subprocess.run(orden, cwd=self.cwd, env=entorno, input=entrada, capture_output=True,
                                    timeout=TIEMPO)
        except (OSError, subprocess.SubprocessError) as exc:
            raise SinInstantanea('git no respondió: ' + str(exc)[:200]) from None
        if salida.returncode != 0:
            error = salida.stderr.decode(errors='replace').strip().splitlines()
            raise SinInstantanea('git ' + args[0] + ': ' + (error[-1] if error else 'falló')[:300])
        return salida.stdout

    def preparar(self):
        if not self.cwd.is_dir():
            raise SinInstantanea('La carpeta de trabajo no existe.')
        if self.cwd == Path.home().resolve() or self.cwd == Path('/') or len(self.cwd.parts) < 3:
            raise SinInstantanea('No se guardan instantáneas de la carpeta personal ni de la raíz del disco.')
        if not (self.dir / 'HEAD').is_file():
            self.dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            subprocess.run(['git', 'init', '-q', '--bare', str(self.dir)], capture_output=True, timeout=TIEMPO,
                           env={k: os.environ[k] for k in ('PATH', 'HOME', 'LANG') if k in os.environ}, check=True)
            (self.dir / 'indices').mkdir(mode=0o700, exist_ok=True)
            (self.dir / 'carpeta').write_text(str(self.cwd) + '\n')
        (self.dir / 'indices').mkdir(mode=0o700, exist_ok=True)
        self._exclusiones()

    def _exclusiones(self):
        """info/exclude: cachés, pesadas y el exclude del proyecto; el global del usuario se respeta."""
        from ..evidencia.espacio import PREFIJOS_TEMPORALES
        from ..permisos.ordenes import CACHES
        lineas = ['# AlfalfaCode: no se guardan en las instantáneas', '.git', *PESADAS,
                  *sorted(CACHES), 'pytest-cache-files-*/', *(p + '*/' for p in PREFIJOS_TEMPORALES)]
        propio = self.cwd / '.git' / 'info' / 'exclude'
        try:
            if propio.is_file() and not propio.is_symlink():
                lineas += ['# .git/info/exclude del proyecto', *propio.read_text(errors='replace').splitlines()]
        except OSError:
            pass
        info = self.dir / 'info'
        info.mkdir(exist_ok=True)
        texto = '\n'.join(lineas) + '\n'
        if not (info / 'exclude').is_file() or (info / 'exclude').read_text() != texto:
            (info / 'exclude').write_text(texto)
        globales = []
        for candidata in _ignore_global():
            try:
                globales = candidata.read_text(errors='replace').splitlines()
                break
            except OSError:
                continue
        (info / 'exclude-global').write_text('\n'.join(globales) + '\n')

    # MARK: - Guardar

    def guardar(self, sesion, nombre, mensaje=''):
        """Instantánea de la carpeta ahora: {repo, commit, fecha, grandes?}. SinInstantanea si no se pudo."""
        self.preparar()
        lista = self.git('ls-files', '-z', '-o', '-m', '--exclude-standard', sesion=sesion)
        nuevos = [n for n in lista.decode(errors='surrogateescape').split('\0') if n]
        if len(nuevos) > TOPE_ARCHIVOS:
            raise SinInstantanea(f'La carpeta tiene demasiados archivos nuevos ({len(nuevos)}) para una instantánea.')
        grandes = []
        for nombre_archivo in dict.fromkeys(nuevos):
            try:
                estado = os.lstat(self.cwd / nombre_archivo)
            except OSError:
                continue
            if estado.st_size > TOPE_ARCHIVO and not os.path.islink(self.cwd / nombre_archivo):
                grandes.append(nombre_archivo)
        especificacion = ['.'] + [':(exclude,literal)' + g for g in grandes]
        self.git('add', '-A', '--ignore-errors', '--pathspec-from-file=-', '--pathspec-file-nul', sesion=sesion,
                 entrada='\0'.join(especificacion).encode(errors='surrogateescape'))
        arbol = self.git('write-tree', sesion=sesion).decode().strip()
        commit = self.git('commit-tree', arbol, '-m', (mensaje or nombre)[:200]).decode().strip()
        self.git('update-ref', 'refs/alfalfa/' + _seguro(sesion) + '/' + _seguro(nombre), commit)
        fila = {'repo': self.clave, 'commit': commit, 'fecha': time.time()}
        if grandes:
            fila['grandes'] = grandes[:20]
        return fila

    def guardar_ref(self, commit, sesion, nombre):
        """Otra ref para un commit que ya está (una sesión bifurcada no pierde sus instantáneas)."""
        self.git('update-ref', 'refs/alfalfa/' + _seguro(sesion) + '/' + _seguro(nombre), commit)

    # MARK: - Leer

    def existe(self, commit):
        try:
            self.git('cat-file', '-e', str(commit) + '^{commit}')
            return True
        except SinInstantanea:
            return False

    def arbol(self, commit):
        """{ruta: (modo, blob)} de todo lo guardado en `commit`."""
        salida = self.git('ls-tree', '-r', '-z', '--full-tree', str(commit))
        filas = {}
        for linea in salida.decode(errors='surrogateescape').split('\0'):
            if not linea:
                continue
            cabeza, _, ruta = linea.partition('\t')
            modo, _tipo, blob = cabeza.split(' ')
            filas[ruta] = (modo, blob)
        return filas

    def cambios(self, desde, hasta):
        """{ruta: (modo, blob) en `hasta`, o None si allí no está} de lo que difiere entre dos commits."""
        salida = self.git('diff-tree', '-r', '-z', '--no-renames', '--no-commit-id', str(desde), str(hasta))
        trozos = salida.decode(errors='surrogateescape').split('\0')
        filas = {}
        for cabeza, ruta in zip(trozos[0::2], trozos[1::2]):
            if not cabeza.startswith(':'):
                continue
            _modo_a, modo_b, _blob_a, blob_b, estado = cabeza[1:].split(' ')
            filas[ruta] = None if estado == 'D' else (modo_b, blob_b)
        return filas

    def contenido(self, blob):
        return self.git('cat-file', 'blob', blob)

    # MARK: - Restaurar

    def restaurar(self, rutas, destino):
        """Deja cada ruta como en `destino` ({ruta: (modo, blob)}): la escribe si está y la borra si no.

        Devuelve (hechas, fallos) con las rutas relativas."""
        hechas, fallos = [], []
        escribir = [r for r in rutas if r in destino]
        for ruta in [r for r in rutas if r not in destino]:
            path = self.cwd / ruta
            try:
                if path.is_symlink() or path.is_file():
                    path.unlink()
                elif path.exists():
                    raise OSError('es una carpeta')
                hechas.append(ruta)
                self._limpiar_carpetas(path.parent, destino)
            except OSError as exc:
                fallos.append({'ruta': ruta, 'error': str(exc)[:200]})
        for ruta in escribir:
            path = self.cwd / ruta
            # Si en su sitio hay ahora una carpeta (o un enlace que la cubre), no se pisa.
            if path.is_dir() and not path.is_symlink():
                fallos.append({'ruta': ruta, 'error': 'ahora es una carpeta'})
                continue
            modo, blob = destino[ruta]
            try:
                if modo == '160000':
                    raise OSError('es un submódulo')
                path.parent.mkdir(parents=True, exist_ok=True)
                datos = self.contenido(blob)
                temporal = path.with_name('.' + path.name + '.alfalfa-restaurar')
                temporal.unlink(missing_ok=True)
                if modo == '120000':
                    os.symlink(os.fsdecode(datos), temporal)
                else:
                    temporal.write_bytes(datos)
                    os.chmod(temporal, 0o755 if modo == '100755' else 0o644)
                os.replace(temporal, path)
                hechas.append(ruta)
            except (OSError, SinInstantanea) as exc:
                fallos.append({'ruta': ruta, 'error': str(exc)[:200]})
        return hechas, fallos

    def _limpiar_carpetas(self, carpeta, destino):
        """Quita las carpetas que quedaron vacías y que la instantánea no tenía."""
        prefijos = {str(Path(r).parent) for r in destino}
        while carpeta != self.cwd and self.cwd in carpeta.parents:
            relativa = str(carpeta.relative_to(self.cwd))
            if any(p == relativa or p.startswith(relativa + '/') for p in prefijos):
                return
            try:
                carpeta.rmdir()
            except OSError:
                return
            carpeta = carpeta.parent

    # MARK: - Limpieza

    def tamano(self):
        total = 0
        for raiz, _carpetas, archivos in os.walk(self.dir):
            for archivo in archivos:
                try:
                    total += os.lstat(os.path.join(raiz, archivo)).st_size
                except OSError:
                    pass
        return total

    def limpiar(self, dias=None, tope=None, ahora=None):
        """Borra refs de más de `dias` días y, por encima de `tope` bytes, las más viejas. Devuelve cuántas."""
        dias = DIAS if dias is None else dias
        tope = TOPE_TOTAL if tope is None else tope
        ahora = time.time() if ahora is None else ahora
        if not (self.dir / 'HEAD').is_file():
            return 0
        salida = self.git('for-each-ref', '--format=%(committerdate:unix) %(refname)', 'refs/alfalfa/')
        refs = sorted((int(fecha), nombre) for fecha, _, nombre in
                      (linea.partition(' ') for linea in salida.decode().splitlines()) if fecha.isdigit())
        viejas = [nombre for fecha, nombre in refs if ahora - fecha > dias * 86400]
        borradas = self._borrar(viejas)
        restantes = [nombre for _, nombre in refs if nombre not in set(viejas)]
        while restantes and self.tamano() > tope:
            # De diez en diez, de la más vieja a la más nueva, compactando cada vez.
            tanda, restantes = restantes[:10], restantes[10:]
            borradas += self._borrar(tanda)
        return borradas

    def _borrar(self, refs):
        if not refs:
            return 0
        self.git('update-ref', '--stdin', entrada=''.join('delete ' + r + '\n' for r in refs).encode())
        self.git('reflog', 'expire', '--expire=now', '--all')
        self.git('gc', '--prune=now', '--quiet')
        # Los índices guardan árboles ya calculados (cache-tree) que el gc pudo borrar:
        # `write-tree` los daría por buenos sin escribirlos. Se rehacen en la próxima instantánea.
        for indice in (self.dir / 'indices').glob('*'):
            indice.unlink(missing_ok=True)
        return len(refs)


def limpiar_todo(hogar, **kwargs):
    """Limpia todos los almacenes del hogar; quita los de carpetas que ya no existen. Devuelve refs borradas."""
    raiz = Path(hogar) / CARPETA
    if not raiz.is_dir():
        return 0
    borradas = 0
    for carpeta in raiz.iterdir():
        if not carpeta.is_dir() or carpeta.is_symlink():
            continue
        try:
            origen = Path((carpeta / 'carpeta').read_text().strip())
        except OSError:
            continue
        if not origen.is_dir():
            shutil.rmtree(carpeta, ignore_errors=True)
            continue
        almacen = Almacen.de_clave(hogar, carpeta.name, origen)
        try:
            borradas += almacen.limpiar(**kwargs)
        except SinInstantanea:
            continue
    return borradas
