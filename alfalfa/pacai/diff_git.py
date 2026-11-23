"""El diff de una ejecución con git, para `diff_updated` cuando no hay instantáneas de deshacer.

Lo calcula el arnés (no el agente: no pasa por el sandbox ni pide permisos) y no toca nada del usuario: ni su
índice, ni su rama, ni su árbol de trabajo. Al empezar la ejecución se copia el índice del repo a un archivo
temporal (`GIT_INDEX_FILE`), se le añade lo que hay en la carpeta (`git add`) y `git write-tree` fija el punto de
partida; al terminar se hace lo mismo con otra copia y se comparan los dos árboles con `git diff-tree`. Copiar el
índice del usuario sólo sirve para que git no vuelva a leer los archivos que no cambiaron.

- Sólo la carpeta de la sesión (que puede ser una subcarpeta del repo), con las rutas relativas a ella.
- Respeta `.gitignore`: lo ignorado no entra. Tampoco lo nuevo en `.tmp-alfalfa/`, la carpeta de temporales de los
  agentes (registros de gradle, capturas, copias del «antes»), aunque el repo no la ignore. Un archivo nuevo de más
  de `MAX_NUEVO` bytes tampoco (los binarios enormes no se meten en la base de objetos), y de un binario sólo se
  cuenta que cambió.
- Lo único que se escribe en el repo son objetos sueltos en `.git/objects` (los de `git add`), que nadie referencia
  y `git gc` acaba borrando.
- Fuera de un repo git, o si git falla o tarda, no hay diff.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

__all__ = ['PuntoDePartida', 'MAX_NUEVO', 'MAX_PARCHE']

MAX_NUEVO = 1024 * 1024        # bytes de un archivo nuevo (sin seguimiento) para entrar en el diff
MAX_PARCHE = 200_000           # caracteres del parche; con más, `diff_updated` lleva sólo la lista de archivos
MAX_ARCHIVOS = 2000
TIEMPO = 20                    # segundos para cada orden de git
TEMPORALES = b'.tmp-alfalfa'   # carpeta de temporales de los agentes: lo nuevo que hay dentro no es un cambio


class ErrorGit(Exception):
    pass


def _entorno(indice: Path | None = None) -> dict:
    env = {k: v for k, v in os.environ.items()
           if k not in ('GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE', 'GIT_OBJECT_DIRECTORY', 'GIT_PREFIX')}
    env.update(GIT_OPTIONAL_LOCKS='0', GIT_TERMINAL_PROMPT='0', LC_ALL='C')
    if indice is not None:
        env['GIT_INDEX_FILE'] = str(indice)
    return env


def _git(carpeta: Path, *args: str, indice: Path | None = None, entrada: bytes | None = None,
         literal: bool = False) -> bytes:
    env = _entorno(indice)
    if literal:
        env['GIT_LITERAL_PATHSPECS'] = '1'
    try:
        r = subprocess.run(['git', '-c', 'core.fsmonitor=false', '-c', 'core.quotepath=false', *args], cwd=carpeta,
                           env=env, input=entrada, capture_output=True, timeout=TIEMPO)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ErrorGit(str(exc)) from None
    if r.returncode != 0:
        raise ErrorGit(r.stderr.decode('utf-8', 'replace')[:500])
    return r.stdout


class PuntoDePartida:
    """El árbol de la carpeta al empezar una ejecución. `tomar` devuelve None fuera de un repo git."""

    def __init__(self, carpeta: Path, temporal: Path, arbol: str):
        self.carpeta, self.temporal, self.arbol = carpeta, temporal, arbol

    @classmethod
    def tomar(cls, carpeta) -> PuntoDePartida | None:
        carpeta = Path(carpeta)
        try:
            if _git(carpeta, 'rev-parse', '--is-inside-work-tree').strip() != b'true':
                return None
            propio = carpeta / _git(carpeta, 'rev-parse', '--git-path', 'index').decode().strip()
        except ErrorGit:
            return None
        temporal = Path(tempfile.mkdtemp(prefix='alfalfa-pacai-diff-'))
        try:
            indice = temporal / 'partida'
            if propio.is_file():
                _copiar_indice(propio, indice)        # sólo se lee el del usuario
            return cls(carpeta, temporal, _arbol(carpeta, indice))
        except (ErrorGit, OSError):
            shutil.rmtree(temporal, ignore_errors=True)
            return None

    def diff(self) -> dict | None:
        """`files` (con `path`, y `added` y `removed` si es texto) y `patch` (o None si es muy largo) de lo que cambió
        desde el punto de partida; None si nada cambió o git falla."""
        try:
            indice = self.temporal / 'final'
            _copiar_indice(self.temporal / 'partida', indice)
            arbol = _arbol(self.carpeta, indice)
            if arbol == self.arbol:
                return None
            comun = ('diff-tree', '-r', '--no-renames', '--relative', '--no-color', '--no-ext-diff', '--no-textconv')
            archivos = _numstat(_git(self.carpeta, *comun, '--numstat', '-z', self.arbol, arbol))[:MAX_ARCHIVOS]
            if not archivos:
                return None
            parche = _git(self.carpeta, *comun, '-p', self.arbol, arbol).decode('utf-8', 'replace')
        except (ErrorGit, OSError):
            return None
        return {'files': archivos, 'patch': parche if len(parche) <= MAX_PARCHE else None}

    def olvidar(self) -> None:
        shutil.rmtree(self.temporal, ignore_errors=True)


def _copiar_indice(origen: Path, destino: Path) -> None:
    """Copia un índice conservando su hora de modificación.

    Git confía en el `stat` de cada entrada salvo en las «racy»: las que cambiaron en el mismo segundo que se
    escribió el índice, que reconoce comparando con la hora del propio archivo del índice. Una copia con la hora de
    ahora las hace pasar por seguras, y un cambio del mismo tamaño en ese segundo se daría por intacto.
    """
    shutil.copy2(origen, destino)


def _arbol(carpeta: Path, indice: Path) -> str:
    """Lo que hay ahora en la carpeta, en el índice temporal, como un árbol de git."""
    # Cambiados y borrados con seguimiento, en todo el repo (`-- .` falla en una carpeta sin nada con seguimiento);
    # `diff-tree --relative` deja luego sólo lo de la carpeta.
    _git(carpeta, 'add', '-u', indice=indice)
    nuevos = [r for r in _git(carpeta, 'ls-files', '-z', '--others', '--exclude-standard', '--', '.',
                              indice=indice).split(b'\0') if r]
    pequenos = []
    for ruta in nuevos:
        if TEMPORALES in ruta.split(b'/')[:-1]:
            continue
        try:
            datos = os.lstat(carpeta / os.fsdecode(ruta))
        except OSError:
            continue
        if datos.st_size <= MAX_NUEVO:
            pequenos.append(ruta)
    if pequenos:
        _git(carpeta, 'add', '--pathspec-from-file=-', '--pathspec-file-nul', indice=indice,
             entrada=b'\0'.join(pequenos), literal=True)
    return _git(carpeta, 'write-tree', indice=indice).decode().strip()


def _numstat(salida: bytes) -> list[dict]:
    archivos = []
    for registro in salida.split(b'\0'):
        partes = registro.split(b'\t', 2)
        if len(partes) != 3:
            continue
        mas, menos, ruta = partes
        fila: dict = {'path': os.fsdecode(ruta)}
        if mas.isdigit() and menos.isdigit():
            fila.update(added=int(mas), removed=int(menos))
        archivos.append(fila)
    return archivos
