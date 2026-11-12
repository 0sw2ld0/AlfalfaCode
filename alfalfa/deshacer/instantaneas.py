"""Instantáneas del contenido del espacio de trabajo en un git paralelo.

Antes y después de cada turno se guarda lo que hay en la carpeta de trabajo en un
repositorio aparte, fuera del proyecto: `$ALFALFA_HOME/instantaneas/<clave>`, con
`--git-dir` ahí y `--work-tree` en la carpeta. El `.git` del proyecto no se toca
(ni se lee su índice) y funciona igual en una carpeta que no es repositorio.

- Una carpeta, un almacén (la clave es la huella de su ruta): las sesiones
  bifurcadas siguen en la misma carpeta y comparten objetos. Todo lo que escribe
  en el almacén va bajo un `flock` suyo (`cerrojo`): dos sesiones, o el principio y
  el final de un turno cancelado, no chocan en el mismo índice.
- Cada sesión tiene su índice (`indices/<sesión>`), así git sólo vuelve a leer lo
  que cambió de fecha o tamaño. Una sesión nueva empieza con una copia del índice
  más reciente del almacén.
- Los bytes se guardan tal cual: `info/attributes` anula `text`, `eol`, `ident`,
  los filtros y `working-tree-encoding` de los `.gitattributes` del proyecto.
- Se respetan los `.gitignore` de la carpeta, su `.git/info/exclude` y el ignore
  global de git, y se saltan las cachés que ya conocen los permisos, las carpetas de
  temporales (`.tmp-…/`), `node_modules/` y los entornos de Python.
- Lo que no entra queda apuntado con cada instantánea (`excluidos/<commit>`): los
  ignorados, los archivos de más de `TOPE_ARCHIVO` bytes, los repositorios anidados
  (git no guarda su contenido) y los que no se pueden leer. Así deshacer nunca borra
  algo que ya existía sólo porque no estaba en la instantánea.
- Restaurar no sigue enlaces: ninguna carpeta de la ruta puede ser un enlace ni
  quedar fuera de la carpeta de trabajo. Un archivo que ya existe se reescribe en su
  sitio (conserva permisos, atributos extendidos y enlaces duros) y sólo cambia su
  bit de ejecución; uno nuevo se crea con la umask.
- Cada instantánea es un commit con su ref `refs/alfalfa/<sesión>/<nombre>`; la
  limpieza borra las refs de más de `DIAS` días y, si el almacén pasa de
  `TOPE_TOTAL`, también las más viejas, salvo las de los últimos `PROTEGIDAS_DIAS`
  días y las de deshacer y rehacer.

Cuando la carpeta es (o está dentro de) un repositorio git, el almacén toma prestados
sus objetos y parte de su HEAD, como los «ghost commits» de Codex pero sin escribir en
el `.git` del proyecto:

- `objects/info/alternates` apunta al almacén de objetos común del proyecto (el de
  `--git-common-dir`, así vale también desde un worktree). git lee de ahí lo que ya
  está y sólo escribe en el almacén lo nuevo o cambiado.
- El primer índice se siembra con el árbol de HEAD de la carpeta (`read-tree`) y un
  `update-index --refresh`, que compara cada archivo con su blob sin escribir nada:
  los rastreados sin cambios no se copian. Los de más de `TOPE_ARCHIVO` se quitan del
  índice antes de comparar (no se leen, como antes).
- Si el proyecto reescribe su historia y un `gc` borra objetos que una instantánea
  tomaba prestados, esa instantánea queda incompleta: `faltan()` lo comprueba antes de
  restaurar y esos archivos no se tocan (motivo `perdido`). Lo nuevo y lo cambiado de
  cada turno vive siempre en el almacén.
- Una instantánea entera tiene `TIEMPO` segundos. Si se agotan, la carpeta queda
  marcada (`sin_deshacer.json`) y los turnos no la reintentan hasta `DESCANSO`.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path

CARPETA = 'instantaneas'
TOPE_ARCHIVO = 5 * 1024 * 1024
# Archivos nuevos o cambiados de una vez: más que esto no es un proyecto, es la casa entera.
TOPE_ARCHIVOS = 60_000
TOPE_EXCLUIDOS = 20_000
DIAS = 30
PROTEGIDAS_DIAS = 2
TOPE_TOTAL = 2 * 1024 ** 3
TIEMPO = 90
# Tras agotar el tiempo, los turnos no vuelven a intentarlo en esta carpeta hasta pasado esto.
DESCANSO = 86400
MARCA_SIN_DESHACER = 'sin_deshacer.json'
# Carpetas pesadas que se reinstalan y no son trabajo del usuario.
PESADAS = ('node_modules/', '.venv/', 'venv/', '.tox/', '.DS_Store')
# Archivos (no carpetas) de las cachés de los permisos.
CACHES_ARCHIVO = {'.coverage'}
ATRIBUTOS = '* -text !eol -ident -filter !working-tree-encoding -crlf\n'
AUTOR = {'GIT_AUTHOR_NAME': 'AlfalfaCode', 'GIT_AUTHOR_EMAIL': 'alfalfa@localhost',
         'GIT_COMMITTER_NAME': 'AlfalfaCode', 'GIT_COMMITTER_EMAIL': 'alfalfa@localhost'}
_NOMBRE = re.compile(r'[^A-Za-z0-9._-]+')
# Lo que git dice de un archivo que no puede leer. Las rutas pueden llevar comillas y
# apóstrofos (it's/): cada patrón se ancla en lo que va después de la ruta, no en la comilla.
_ILEGIBLES = (re.compile(r'^(?:error|warning): (?:open|read)\("(.+)"\): '),
              re.compile(r"^(?:error|warning): unable to index file '(.+)'$"),
              re.compile(r"^(?:error|warning): could not open '(.+)'(?:: |$)"),
              re.compile(r"^(?:error|warning): unable to access '(.+)': "))
# git avisa así de una carpeta que no puede leer; se apunta con su barra final (un prefijo).
_CARPETA_ILEGIBLE = re.compile(r"^(?:error|warning): could not open directory '(.+)': ")


def _ilegible(linea):
    """La ruta de un aviso de git sobre algo que no pudo leer (carpeta con «/» final), o None."""
    carpeta = _CARPETA_ILEGIBLE.match(linea)
    if carpeta:
        return carpeta.group(1).rstrip('/') + '/'
    for patron in _ILEGIBLES:
        hallado = patron.match(linea)
        if hallado:
            return hallado.group(1)
    return None
# Los modos que git ya guarda; cualquier otro (0600, 0700…) se apunta aparte.
MODOS_GIT = (0o644, 0o755)


class SinInstantanea(Exception):
    """No se pudo guardar o leer una instantánea; el texto dice por qué."""


class TiempoAgotado(SinInstantanea):
    """La instantánea no terminó en `TIEMPO` segundos."""


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


def _bytes(n):
    """«850 KB», «1,2 GB»: el tamaño para el aviso de «Preparando deshacer…»."""
    for unidad, valor in (('GB', 1e9), ('MB', 1e6), ('KB', 1e3)):
        if n >= valor:
            cifra = n / valor
            return (f'{cifra:.1f}'.replace('.', ',') if cifra < 10 else f'{cifra:.0f}') + ' ' + unidad
    return f'{n} B'


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


def proyecto_de(cwd):
    """{objetos, arbol} del repositorio git que contiene `cwd`, o None si no lo hay o no se puede leer.

    `objetos` es su almacén común de objetos (también desde un worktree) y `arbol`, el árbol de
    HEAD en `cwd` (None si HEAD aún no tiene commit o la carpeta no está en él). Sólo `rev-parse`:
    ni hooks ni fsmonitor, y sin escribir nada en el proyecto."""
    entorno = {k: os.environ[k] for k in ('PATH', 'HOME', 'LANG') if k in os.environ}
    entorno.update(GIT_TERMINAL_PROMPT='0', GIT_OPTIONAL_LOCKS='0', LC_ALL='C')

    def rev_parse(*args):
        try:
            salida = subprocess.run(['git', '-c', 'core.fsmonitor=false', 'rev-parse', *args], cwd=cwd,
                                    env=entorno, capture_output=True, timeout=15)
        except (OSError, subprocess.SubprocessError):
            return None
        return salida.stdout.decode(errors='surrogateescape') if salida.returncode == 0 else None

    salida = rev_parse('--is-inside-work-tree', '--path-format=absolute', '--git-common-dir', '--show-prefix')
    lineas = salida.split('\n') if salida else []
    if len(lineas) < 3 or lineas[0] != 'true':
        return None
    objetos = (Path(cwd) / lineas[1]).resolve() / 'objects'
    if not objetos.is_dir():
        return None
    prefijo = lineas[2]
    arbol = rev_parse('--verify', '-q', 'HEAD:' + prefijo if prefijo else 'HEAD^{tree}')
    return {'objetos': objetos, 'arbol': arbol.strip() if arbol and arbol.strip() else None}


def cubre(excluidos, ruta):
    """Por qué `ruta` no estaba en una instantánea con estos `excluidos`, o None si sí estaba (o no se sabe)."""
    if not excluidos:
        return None
    if ruta in excluidos.get('grandes', ()):
        return 'grande'
    for ilegible in excluidos.get('ilegibles', ()):
        if ruta == ilegible or (ilegible.endswith('/') and ruta.startswith(ilegible)):
            return 'ilegible'
    for anidado in excluidos.get('anidados', ()):
        if ruta == anidado or ruta.startswith(anidado.rstrip('/') + '/'):
            return 'repo_anidado'
    for ignorado in excluidos.get('ignorados', ()):
        if ruta == ignorado or (ignorado.endswith('/') and ruta.startswith(ignorado)):
            return 'ignorado'
    return None


class Almacen:
    """El git paralelo de una carpeta de trabajo."""

    def __init__(self, hogar, cwd):
        self.cwd = Path(cwd).expanduser().resolve()
        self.clave = hashlib.sha256(str(self.cwd).encode()).hexdigest()[:16]
        self.dir = Path(hogar) / CARPETA / self.clave
        # Hasta cuándo (time.monotonic) puede seguir la instantánea en curso; None fuera de guardar().
        self._limite = None
        self.proyecto = None
        # Quien quiera saber cuánto falta: avance(texto) con «8.306 archivos», «1,2 GB»…
        self.avance = None

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
            entorno['GIT_INDEX_FILE'] = str(self._indice(sesion))
        return entorno

    def _indice(self, sesion):
        return self.dir / 'indices' / _seguro(sesion)

    def git(self, *args, sesion=None, entrada=None, literal=False, tolerar=()):
        """La salida de git. Con `tolerar`, esos códigos de salida no fallan y se devuelve (salida, error)."""
        entorno = self._entorno(sesion)
        if literal:
            entorno['GIT_LITERAL_PATHSPECS'] = '1'
        orden = ['git', '-c', 'core.autocrlf=false', '-c', 'core.safecrlf=false', '-c', 'core.quotepath=off',
                 '-c', 'core.fsmonitor=false', '-c', 'gc.auto=0', '-c', 'core.hooksPath=/dev/null',
                 '-c', 'core.excludesFile=' + str(self.dir / 'info' / 'exclude-global'), *args]
        tiempo = TIEMPO
        if self._limite is not None:
            tiempo = self._limite - time.monotonic()
            if tiempo <= 0:
                raise TiempoAgotado(f'La instantánea tardó más de {TIEMPO} s.')
        try:
            salida = subprocess.run(orden, cwd=self.cwd, env=entorno, input=entrada, capture_output=True,
                                    timeout=tiempo)
        except subprocess.TimeoutExpired:
            raise TiempoAgotado(f'La instantánea tardó más de {TIEMPO} s (git {args[0]}).') from None
        except (OSError, subprocess.SubprocessError) as exc:
            raise SinInstantanea('git no respondió: ' + str(exc)[:200]) from None
        if salida.returncode in tolerar:
            return salida.stdout, salida.stderr.decode(errors='replace')
        if salida.returncode != 0:
            error = salida.stderr.decode(errors='replace').strip().splitlines()
            raise SinInstantanea('git ' + args[0] + ': ' + (error[-1] if error else 'falló')[:300])
        return salida.stdout

    @contextmanager
    def cerrojo(self):
        """Todo lo que escribe en el almacén, de uno en uno entre procesos. Los `.lock` que
        queden dentro son de un git que murió (un tiempo agotado): se quitan."""
        self.dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(self.dir / 'cerrojo', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            limite = time.monotonic() + TIEMPO
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() > limite:
                        raise SinInstantanea('Otra instantánea de esta carpeta no termina.') from None
                    time.sleep(0.05)
            viejos = [*(self.dir / 'indices').glob('*.lock'), *self.dir.glob('*.lock'),
                      *(self.dir / 'refs').rglob('*.lock')]
            for viejo in viejos:
                viejo.unlink(missing_ok=True)
            yield
        finally:
            os.close(fd)

    def preparar(self):
        if not self.cwd.is_dir():
            raise SinInstantanea('La carpeta de trabajo no existe.')
        if self.cwd == Path.home().resolve() or self.cwd == Path('/') or len(self.cwd.parts) < 3:
            raise SinInstantanea('No se guardan instantáneas de la carpeta personal ni de la raíz del disco.')
        if not (self.dir / 'HEAD').is_file():
            self.dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            try:
                subprocess.run(['git', 'init', '-q', '--bare', str(self.dir)], capture_output=True, timeout=TIEMPO,
                               env={k: os.environ[k] for k in ('PATH', 'HOME', 'LANG') if k in os.environ},
                               check=True)
            except (OSError, subprocess.SubprocessError) as exc:
                raise SinInstantanea('No se pudo crear el almacén de instantáneas: ' + str(exc)[:200]) from None
            (self.dir / 'carpeta').write_text(str(self.cwd) + '\n')
        for carpeta in ('indices', 'excluidos', 'info'):
            (self.dir / carpeta).mkdir(mode=0o700, exist_ok=True)
        self._exclusiones()
        self.proyecto = proyecto_de(self.cwd)
        self._prestados()

    def _prestados(self):
        """`objects/info/alternates`: el almacén de objetos del proyecto, para leer de ahí lo que ya
        tiene en vez de copiarlo. Se conservan los anteriores que sigan existiendo: instantáneas
        viejas pueden tomar prestado de un `.git` que se movió (otro `gitdir`)."""
        alternates = self.dir / 'objects' / 'info' / 'alternates'
        try:
            lineas = [linea for linea in alternates.read_text().splitlines() if linea.strip()]
        except OSError:
            lineas = []
        vigentes = [linea for linea in lineas if Path(linea).is_dir()]
        if self.proyecto and str(self.proyecto['objetos']) not in vigentes:
            vigentes.append(str(self.proyecto['objetos']))
        if vigentes != lineas:
            alternates.parent.mkdir(parents=True, exist_ok=True)
            if vigentes:
                alternates.write_text('\n'.join(vigentes) + '\n')
            else:
                alternates.unlink(missing_ok=True)

    def _escribir_si_cambia(self, path, texto):
        if not path.is_file() or path.read_text() != texto:
            path.write_text(texto)

    def _exclusiones(self):
        """info/exclude (cachés, pesadas y el exclude del proyecto), el ignore global y los atributos neutros."""
        from ..evidencia.espacio import PREFIJOS_TEMPORALES
        from ..permisos.ordenes import CACHES
        lineas = ['# AlfalfaCode: no se guardan en las instantáneas', '.git', *PESADAS,
                  *sorted(n + ('' if n in CACHES_ARCHIVO else '/') for n in CACHES),
                  'pytest-cache-files-*/', *(p + '*/' for p in PREFIJOS_TEMPORALES)]
        propio = self.cwd / '.git' / 'info' / 'exclude'
        try:
            if propio.is_file() and not propio.is_symlink():
                lineas += ['# .git/info/exclude del proyecto', *propio.read_text(errors='replace').splitlines()]
        except OSError:
            pass
        info = self.dir / 'info'
        self._escribir_si_cambia(info / 'exclude', '\n'.join(lineas) + '\n')
        self._escribir_si_cambia(info / 'attributes', ATRIBUTOS)
        globales = []
        for candidata in _ignore_global():
            try:
                globales = candidata.read_text(errors='replace').splitlines()
                break
            except OSError:
                continue
        self._escribir_si_cambia(info / 'exclude-global', '\n'.join(globales) + '\n')

    # MARK: - Guardar

    def guardar(self, sesion, nombre, mensaje=''):
        """Instantánea de la carpeta ahora: {repo, commit, fecha, grandes?}. SinInstantanea si no se pudo."""
        self.preparar()
        with self.cerrojo():
            # El plazo cuenta desde que se tiene el cerrojo: esperar a otra sesión no lo gasta.
            self._limite = time.monotonic() + TIEMPO
            try:
                return self._guardar(sesion, nombre, mensaje)
            finally:
                self._limite = None

    def _contar(self, texto):
        if callable(self.avance):
            try:
                self.avance(texto)
            except Exception:  # noqa: BLE001 — quien escucha no puede romper la instantánea.
                pass

    def _sembrar(self, sesion, arbol):
        """El índice de una sesión sin índice previo, con el árbol de HEAD del proyecto: sus blobs ya
        están en el almacén prestado. Lo grande se quita antes de comparar (no se lee), y
        `--refresh` compara el resto con lo que hay en disco sin escribir objetos."""
        self.git('read-tree', arbol, sesion=sesion)
        filas = self.git('ls-files', '-z', '-s', sesion=sesion).decode(errors='surrogateescape').split('\0')
        rutas = [fila.split('\t', 1)[1] for fila in filas if '\t' in fila and not fila.startswith('160000 ')]
        fuera, total = [], 0
        for ruta in rutas:
            try:
                estado = os.lstat(self.cwd / ruta)
            except FileNotFoundError:
                continue  # Borrado: `add -A` lo apunta.
            except OSError:
                # Dentro de una carpeta que no se puede abrir: su versión de HEAD no es la de disco.
                fuera.append(ruta)
                continue
            if stat.S_ISREG(estado.st_mode):
                if estado.st_size > TOPE_ARCHIVO or not os.access(self.cwd / ruta, os.R_OK):
                    # Grande o ilegible: fuera, como en un almacén sin sembrar (se apunta después).
                    fuera.append(ruta)
                else:
                    total += estado.st_size
        if fuera:
            self._quitar(sesion, fuera)
        self._contar(f'{len(rutas) - len(fuera)} archivos, {_bytes(total)}')
        # Sale con 1 si algo no coincide: eso lo guarda después `add`.
        self.git('update-index', '-q', '--refresh', '--ignore-missing', sesion=sesion, tolerar=(0, 1))

    def _quitar(self, sesion, rutas):
        """Quita estas rutas del índice de la sesión (sin mirar HEAD ni el disco)."""
        self.git('update-index', '-z', '--force-remove', '--stdin', sesion=sesion,
                 entrada=''.join(r + '\0' for r in rutas).encode(errors='surrogateescape'))

    def _guardar(self, sesion, nombre, mensaje):
        indice = self._indice(sesion)
        if not indice.exists():
            # Una sesión nueva (o bifurcada) parte del índice más reciente: no relee toda la carpeta.
            previos = [p for p in (self.dir / 'indices').iterdir() if p.is_file() and not p.name.endswith('.lock')]
            if previos:
                shutil.copy2(max(previos, key=lambda p: p.stat().st_mtime), indice)
            elif self.proyecto and self.proyecto.get('arbol'):
                self._sembrar(sesion, self.proyecto['arbol'])
        lista, avisos = self.git('ls-files', '-z', '-o', '-m', '--exclude-standard', sesion=sesion, tolerar=(0,))
        nuevos = [n for n in lista.decode(errors='surrogateescape').split('\0') if n]
        if len(nuevos) > TOPE_ARCHIVOS:
            raise SinInstantanea(f'La carpeta tiene demasiados archivos nuevos ({len(nuevos)}) para una instantánea.')
        # Un repositorio anidado sale como «carpeta/»: git sólo guardaría un enlace a su commit (y sin
        # commit, ni eso: fallaría todo). Se deja fuera, con los que ya estaban en el índice.
        anidados = [n.rstrip('/') for n in nuevos if n.endswith('/')]
        indexados = self.git('ls-files', '-z', '-s', sesion=sesion).decode(errors='surrogateescape')
        enlazados = [fila.split('\t', 1)[1] for fila in indexados.split('\0')
                     if fila.startswith('160000 ') and '\t' in fila]
        if enlazados:
            self.git('rm', '-r', '-q', '--cached', '--ignore-unmatch', '--pathspec-from-file=-',
                     '--pathspec-file-nul', sesion=sesion, literal=True,
                     entrada='\0'.join(enlazados).encode(errors='surrogateescape'))
        anidados = sorted(set(anidados) | set(enlazados))
        grandes, ilegibles = [], []
        # Si git avisa de algo, las carpetas que no se pueden leer se buscan a mano: su aviso no es
        # fiable (un nombre con salto de línea lo parte en dos). Lo que no se entienda deja la lista
        # de excluidos marcada como incompleta.
        incompleta = any(_ilegible(linea.strip()) is None for linea in avisos.splitlines() if linea.strip())
        if avisos.strip():
            ilegibles += self._carpetas_ilegibles()
        for nombre_archivo in dict.fromkeys(n for n in nuevos if not n.endswith('/')):
            path = self.cwd / nombre_archivo
            try:
                estado = os.lstat(path)
            except OSError:
                continue
            if stat.S_ISLNK(estado.st_mode):
                continue
            if estado.st_size > TOPE_ARCHIVO:
                grandes.append(nombre_archivo)
            elif not os.access(path, os.R_OK):
                ilegibles.append(nombre_archivo)
        fuera = grandes + ilegibles + anidados
        if grandes:
            # Si ya estaba en el índice (un archivo que creció), su versión anterior no puede pasar
            # por la de ahora: fuera también de ahí.
            self._quitar(sesion, grandes)
        especificacion = ['.'] + [':(exclude,literal)' + g for g in fuera]
        _, errores = self.git('add', '-A', '--ignore-errors', '--pathspec-from-file=-', '--pathspec-file-nul',
                              sesion=sesion, tolerar=(0, 1),
                              entrada='\0'.join(especificacion).encode(errors='surrogateescape'))
        for linea in errores.splitlines():
            ruta = _ilegible(linea.strip())
            if ruta and not ruta.endswith('/'):
                ilegibles.append(ruta)
            elif ruta is None and linea.startswith('fatal:'):
                raise SinInstantanea('git add: ' + linea[:300])
            elif ruta is None and linea.strip():
                incompleta = True
        if errores.strip() and not avisos.strip():
            ilegibles += self._carpetas_ilegibles()
        arbol = self.git('write-tree', sesion=sesion).decode().strip()
        commit = self.git('commit-tree', arbol, '-m', (mensaje or nombre)[:200]).decode().strip()
        self.git('update-ref', 'refs/alfalfa/' + _seguro(sesion) + '/' + _seguro(nombre), commit)
        ignorados = self.git('ls-files', '-z', '-o', '-i', '--exclude-standard', '--directory', sesion=sesion)
        ignorados = [n for n in ignorados.decode(errors='surrogateescape').split('\0') if n]
        excluidos = {'grandes': grandes, 'ilegibles': sorted(set(ilegibles)), 'anidados': anidados,
                     'ignorados': ignorados[:TOPE_EXCLUIDOS], 'recortado': len(ignorados) > TOPE_EXCLUIDOS,
                     'incompleta': incompleta, **self._modos(sesion, ilegibles),
                     'modos_ignorados': self._modos_ignorados(ignorados[:TOPE_EXCLUIDOS])}
        (self.dir / 'excluidos' / commit).write_text(json.dumps(excluidos, ensure_ascii=False))
        fila = {'repo': self.clave, 'commit': commit, 'fecha': time.time()}
        if grandes:
            fila['grandes'] = grandes[:20]
        return fila

    def _carpetas_ilegibles(self):
        """Las carpetas (con «/» final) que no se pueden abrir, buscadas sin pasar por el texto de git."""
        ilegibles, pendientes = [], [self.cwd]
        saltar = {'.git', *(p.rstrip('/') for p in PESADAS)}
        while pendientes:
            carpeta = pendientes.pop()
            try:
                with os.scandir(carpeta) as entradas:
                    for entrada in entradas:
                        if entrada.name in saltar or not entrada.is_dir(follow_symlinks=False):
                            continue
                        if not os.access(entrada.path, os.R_OK | os.X_OK, follow_symlinks=False):
                            # Sin x no se puede entrar (git falla con «unable to stat»); sin r, ni listar.
                            ilegibles.append(str(Path(entrada.path).relative_to(self.cwd)) + '/')
                        else:
                            pendientes.append(Path(entrada.path))
            except PermissionError:
                ilegibles.append(str(carpeta.relative_to(self.cwd)) + '/')
            except OSError:
                continue
        return ilegibles

    def _modos_de(self, rutas):
        """{ruta: modo} de estos archivos cuando no son 0644 ni 0755."""
        modos = {}
        for ruta in rutas:
            try:
                estado = os.lstat(self.cwd / ruta)
            except OSError:
                continue
            if stat.S_ISREG(estado.st_mode) and stat.S_IMODE(estado.st_mode) not in MODOS_GIT:
                modos[ruta] = stat.S_IMODE(estado.st_mode)
        return modos

    def _modos_ignorados(self, ignorados, tope=5000):
        """{ruta: modo} de los ignorados con permisos que no son 0644 ni 0755, también dentro de una
        carpeta ignorada entera (sin entrar en las pesadas ni en las cachés, y hasta `tope` archivos)."""
        from ..permisos.ordenes import CACHES
        saltar = {'.git', *(p.rstrip('/') for p in PESADAS), *CACHES}
        archivos = [n for n in ignorados if not n.endswith('/')]
        pendientes = [self.cwd / n for n in ignorados if n.endswith('/') and Path(n).name not in saltar]
        while pendientes and len(archivos) < tope:
            carpeta = pendientes.pop()
            try:
                with os.scandir(carpeta) as entradas:
                    for entrada in entradas:
                        if entrada.is_dir(follow_symlinks=False):
                            if entrada.name not in saltar:
                                pendientes.append(Path(entrada.path))
                        elif entrada.is_file(follow_symlinks=False):
                            archivos.append(str(Path(entrada.path).relative_to(self.cwd)))
            except OSError:
                continue
        return self._modos_de(archivos[:tope])

    def _modos(self, sesion, ilegibles=()):
        """Los permisos que git no conserva: {'modos': {archivo: modo}} de los que no son 0644 ni 0755,
        {'modos_carpetas': {carpeta: modo}} de las que no son 0755 y {'carpetas': {carpeta: modo}} de
        todas las que se conocen (las de los archivos guardados, las vacías y las ilegibles)."""
        modos, carpetas, todas = {}, {}, {}
        vistas = set()
        for ruta in self.git('ls-files', '-z', sesion=sesion).decode(errors='surrogateescape').split('\0'):
            if not ruta:
                continue
            try:
                estado = os.lstat(self.cwd / ruta)
            except OSError:
                continue
            if stat.S_ISREG(estado.st_mode) and stat.S_IMODE(estado.st_mode) not in MODOS_GIT:
                modos[ruta] = stat.S_IMODE(estado.st_mode)
            carpeta = str(Path(ruta).parent)
            while carpeta not in ('.', '') and carpeta not in vistas:
                vistas.add(carpeta)
                try:
                    modo = stat.S_IMODE(os.lstat(self.cwd / carpeta).st_mode)
                except OSError:
                    break
                todas[carpeta] = modo
                if modo != 0o755:
                    carpetas[carpeta] = modo
                carpeta = str(Path(carpeta).parent)
        # Las carpetas sin nada guardado dentro (vacías) y las que no se pueden leer: git no las lista.
        sueltas = self.git('ls-files', '-z', '-o', '--directory', '--exclude-standard', sesion=sesion, tolerar=(0,))[0]
        for nombre in [*sueltas.decode(errors='surrogateescape').split('\0'), *ilegibles]:
            if not nombre.endswith('/') or nombre.rstrip('/') in todas:
                continue
            try:
                estado = os.lstat(self.cwd / nombre.rstrip('/'))
            except OSError:
                continue
            if stat.S_ISDIR(estado.st_mode):
                todas[nombre.rstrip('/')] = stat.S_IMODE(estado.st_mode)
                if stat.S_IMODE(estado.st_mode) != 0o755:
                    carpetas[nombre.rstrip('/')] = stat.S_IMODE(estado.st_mode)
        return {'modos': modos, 'modos_carpetas': carpetas, 'carpetas': todas}

    def guardar_ref(self, commit, sesion, nombre):
        """Otra ref para un commit que ya está (un rehacer no se pierde con la limpieza)."""
        with self.cerrojo():
            self.git('update-ref', 'refs/alfalfa/' + _seguro(sesion) + '/' + _seguro(nombre), commit)

    def entradas(self, commit, rutas):
        """{ruta: (modo, blob)} de estas rutas en `commit` (las que estén)."""
        if not rutas:
            return {}
        salida = self.git('ls-tree', '-z', '--full-tree', str(commit), '--', *rutas, literal=True)
        filas = {}
        for linea in salida.decode(errors='surrogateescape').split('\0'):
            cabeza, _, ruta = linea.partition('\t')
            if ruta:
                modo, _tipo, blob = cabeza.split(' ')
                filas[ruta] = (modo, blob)
        return filas

    # MARK: - Leer

    def existe(self, commit):
        try:
            self.git('cat-file', '-e', str(commit) + '^{commit}')
            return True
        except SinInstantanea:
            return False

    def faltan(self, blobs):
        """Los de estos objetos que ya no están (ni en el almacén ni en el prestado del proyecto)."""
        blobs = sorted({b for b in blobs if b})
        if not blobs:
            return set()
        try:
            salida = self.git('cat-file', '--batch-check=%(objectname)', entrada=('\n'.join(blobs) + '\n').encode())
        except SinInstantanea:
            return set(blobs)
        return {linea.split(' ', 1)[0] for linea in salida.decode(errors='replace').splitlines()
                if linea.endswith(' missing')}

    # MARK: - Sin deshacer en esta carpeta

    def sin_deshacer(self, ahora=None):
        """Por qué los turnos no toman instantáneas aquí ({motivo, fecha}), o None. Caduca a los `DESCANSO` s."""
        try:
            marca = json.loads((self.dir / MARCA_SIN_DESHACER).read_text())
        except (OSError, ValueError):
            return None
        fecha = marca.get('fecha') if isinstance(marca, dict) else None
        if not isinstance(fecha, (int, float)) or (time.time() if ahora is None else ahora) - fecha > DESCANSO:
            return None
        return marca

    def marcar_sin_deshacer(self, motivo):
        try:
            self.dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            (self.dir / MARCA_SIN_DESHACER).write_text(json.dumps({'motivo': motivo, 'fecha': time.time()},
                                                                  ensure_ascii=False))
        except OSError:
            pass

    def quitar_marca(self):
        try:
            (self.dir / MARCA_SIN_DESHACER).unlink(missing_ok=True)
        except OSError:
            pass

    def excluidos(self, commit):
        """Lo que no entró en la instantánea `commit`, o None si no se sabe (instantánea antigua)."""
        try:
            return json.loads((self.dir / 'excluidos' / _seguro(commit)).read_text())
        except (OSError, ValueError):
            return None

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

    def _dentro(self, path):
        """Error si alguna carpeta de la ruta es un enlace, no es carpeta o queda fuera de la de trabajo."""
        actual = self.cwd
        for parte in path.relative_to(self.cwd).parts[:-1]:
            actual = actual / parte
            if actual.is_symlink():
                raise OSError('la carpeta ' + str(actual.relative_to(self.cwd)) + ' es un enlace')
            if actual.exists() and not actual.is_dir():
                raise OSError(str(actual.relative_to(self.cwd)) + ' no es una carpeta')
        padre = Path(os.path.realpath(path.parent if path.parent.exists() else self.cwd))
        if padre != self.cwd and self.cwd not in padre.parents:
            raise OSError('queda fuera de la carpeta de trabajo')

    def _carpetas(self, path):
        """Crea las carpetas que faltan una a una, sin seguir enlaces, y devuelve las creadas.

        Se crean 0700 (abiertas para escribir dentro, cerradas a los demás); su modo final se
        pone al terminar de restaurar, de dentro hacia fuera (una 0555 no impediría escribir)."""
        creadas = []
        actual = self.cwd
        for parte in path.relative_to(self.cwd).parts[:-1]:
            actual = actual / parte
            if not actual.exists() and not actual.is_symlink():
                actual.mkdir(mode=0o700)
                creadas.append(actual)
            elif actual.is_symlink() or not actual.is_dir():
                raise OSError('la carpeta ' + str(actual.relative_to(self.cwd)) + ' es un enlace')
        return creadas

    def permisos_de_carpeta(self, ruta, modo):
        """Pone a una carpeta el modo que tenía, sin seguir enlaces."""
        with self.cerrojo():
            path = self.cwd / ruta.rstrip('/')
            self._dentro(path)
            if path.is_symlink() or not path.is_dir():
                raise OSError('no es una carpeta')
            os.chmod(path, modo)

    def _escribir(self, path, modo, datos, permisos=None):
        """`permisos`: el modo real que apuntó la instantánea; sin él, los de ahora con el bit x de git."""
        if modo == '120000':
            if path.is_symlink() or path.exists():
                path.unlink()
            os.symlink(os.fsdecode(datos), path)
            return
        ejecutable = modo == '100755'
        if path.is_symlink() or (path.exists() and not path.is_file()):
            path.unlink()
        if path.exists():
            # En su sitio: conserva permisos, atributos extendidos, ACL y enlaces duros.
            fd = os.open(path, os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW)
        else:
            # Ya con el modo final (la umask sólo puede quitar): nunca existe con más permisos.
            inicial = permisos if permisos is not None else (0o777 if ejecutable else 0o666)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, inicial)
        with os.fdopen(fd, 'wb') as salida:
            salida.write(datos)
        actual = stat.S_IMODE(os.lstat(path).st_mode)
        if permisos is not None:
            nuevo = permisos
        else:
            nuevo = actual | ((actual & 0o444) >> 2) if ejecutable else actual & ~0o111
        if nuevo != actual:
            os.chmod(path, nuevo)

    def restaurar(self, rutas, destino, creadas_desde=None, commit=None):
        """Deja cada ruta como en `destino` ({ruta: (modo, blob)}): la escribe si está y la borra si no.

        Primero los borrados y después las escrituras; las carpetas que se queden vacías sólo
        se quitan si se crearon después de `creadas_desde`. Con `commit` (la instantánea de
        `destino`), cada archivo vuelve con los permisos exactos que tenía. Devuelve (hechas, fallos)."""
        with self.cerrojo():
            return self._restaurar(rutas, destino, creadas_desde, commit)

    def _restaurar(self, rutas, destino, creadas_desde, commit=None):
        excluidos = self.excluidos(commit) if commit else None
        modos = excluidos.get('modos') if isinstance(excluidos, dict) else None
        modos_carpetas = excluidos.get('modos_carpetas') if isinstance(excluidos, dict) else None
        hechas, fallos, creadas, abiertas = [], [], [], {}
        for ruta in [r for r in rutas if r not in destino]:
            path = self.cwd / ruta
            try:
                self._dentro(path)
                self._abrir(path, abiertas)
                if path.is_symlink() or path.is_file():
                    path.unlink()
                elif path.exists():
                    raise OSError('es una carpeta')
                hechas.append(ruta)
                self._limpiar_carpetas(path.parent, destino, creadas_desde)
            except OSError as exc:
                fallos.append({'ruta': ruta, 'error': str(exc)[:200]})
        for ruta in [r for r in rutas if r in destino]:
            path = self.cwd / ruta
            modo, blob = destino[ruta]
            try:
                if modo == '160000':
                    raise OSError('es un repositorio anidado')
                self._dentro(path)
                if path.is_dir() and not path.is_symlink():
                    raise OSError('ahora es una carpeta')
                datos = self.contenido(blob)
                self._abrir(path, abiertas)
                creadas += self._carpetas(path)
                self._dentro(path)
                permisos = None
                if isinstance(modos, dict) and modo != '120000':
                    # Los de la instantánea: 0600 si estaba apuntado; si no, 0644 o 0755 como guarda git.
                    permisos = modos.get(ruta) or (0o755 if modo == '100755' else 0o644)
                self._escribir(path, modo, datos, permisos)
                hechas.append(ruta)
            except (OSError, SinInstantanea) as exc:
                fallos.append({'ruta': ruta, 'error': str(exc)[:200]})
        devolver = {c: (modos_carpetas or {}).get(str(c.relative_to(self.cwd)), 0o755) for c in set(creadas)}
        devolver.update(abiertas)
        for carpeta in sorted(devolver, key=lambda c: len(c.parts), reverse=True):
            try:
                os.chmod(carpeta, devolver[carpeta])
            except OSError:
                pass
        return hechas, fallos

    def _abrir(self, path, abiertas):
        """Da permiso de escritura (u+wx) a las carpetas de la ruta que ya existen y no lo tienen (una
        0555 de la que el agente borró algo), apuntando su modo para devolvérselo al terminar."""
        actual = self.cwd
        for parte in path.relative_to(self.cwd).parts[:-1]:
            actual = actual / parte
            if actual.is_symlink() or not actual.is_dir():
                return
            if actual not in abiertas and not os.access(actual, os.W_OK | os.X_OK):
                modo = stat.S_IMODE(os.lstat(actual).st_mode)
                os.chmod(actual, modo | 0o700)
                abiertas[actual] = modo

    def _limpiar_carpetas(self, carpeta, destino, creadas_desde):
        """Quita las carpetas vacías que la instantánea no tenía y que se crearon después de ella."""
        if creadas_desde is None:
            return
        prefijos = {str(Path(r).parent) for r in destino}
        while carpeta != self.cwd and self.cwd in carpeta.parents and not carpeta.is_symlink():
            relativa = str(carpeta.relative_to(self.cwd))
            if any(p == relativa or p.startswith(relativa + '/') for p in prefijos):
                return
            try:
                # Sin fecha de creación (fuera de macOS) no se sabe si ya estaba: se deja.
                nacida = getattr(os.lstat(carpeta), 'st_birthtime', None)
                if nacida is None or nacida < creadas_desde:
                    return
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
        """Borra refs de más de `dias` días y, por encima de `tope` bytes, las más viejas que no
        estén protegidas (las recientes y las de deshacer y rehacer). Devuelve cuántas."""
        dias = DIAS if dias is None else dias
        tope = TOPE_TOTAL if tope is None else tope
        ahora = time.time() if ahora is None else ahora
        if not (self.dir / 'HEAD').is_file():
            return 0
        with self.cerrojo():
            salida = self.git('for-each-ref', '--format=%(committerdate:unix) %(refname)', 'refs/alfalfa/')
            refs = sorted((int(fecha), nombre) for fecha, _, nombre in
                          (linea.partition(' ') for linea in salida.decode().splitlines()) if fecha.isdigit())
            viejas = [nombre for fecha, nombre in refs if ahora - fecha > dias * 86400]
            borradas = self._borrar(viejas)
            prescindibles = [nombre for fecha, nombre in refs if nombre not in set(viejas)
                             and ahora - fecha > PROTEGIDAS_DIAS * 86400
                             and not re.search(r'/(deshacer|rehacer)-', nombre)]
            while prescindibles and self.tamano() > tope:
                # De diez en diez, de la más vieja a la más nueva, compactando cada vez.
                tanda, prescindibles = prescindibles[:10], prescindibles[10:]
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
        for lista in (self.dir / 'excluidos').glob('*'):
            if not self.existe(lista.name):
                lista.unlink(missing_ok=True)
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
