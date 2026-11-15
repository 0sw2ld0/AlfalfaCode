"""El git que lanza el propio arnés: corre fuera del sandbox y nunca ejecuta programas del proyecto.

El arnés llama a git por su cuenta (el entorno del primer mensaje, la huella de cada orden, refrescar
el índice antes de una comprobación, la búsqueda de los .env, la sesión…). Ese git leía la
configuración del repositorio, y con ella podía ejecutar lo que esta mandara: `core.fsmonitor`, un hook
(`post-index-change`), un filtro `clean` o un `textconv`. Bastaba un `.git/config` que llegara con la
carpeta (un zip, una copia) o un repositorio que el agente montara dentro del sandbox (`vendor/x` con
HEAD, objects, refs y config, que no se llaman `.git`) y una orden con `cwd` ahí: el programa corría con
todo el entorno del usuario, fuera del sandbox.

Aquí, siempre:
- sin fsmonitor ni hooks, y con los filtros y los textconv que defina la configuración anulados;
- con un entorno mínimo (sin las claves del usuario);
- sobre la carpeta que dice quien llama, que debe ser la raíz de la sesión, no el `cwd` de una orden.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

FIJAS = ('-c', 'core.fsmonitor=false', '-c', 'core.hooksPath=/dev/null', '-c', 'core.untrackedCache=false')
# Lo que un repositorio puede definir con cualquier nombre y que ejecuta un programa.
_CON_PROGRAMA = r'^(filter\..+\.(clean|smudge|process|required)|diff\..+\.(textconv|command))$'
_ENTORNO = ('PATH', 'HOME', 'LANG', 'LC_ALL', 'LC_CTYPE', 'TMPDIR', 'XDG_CONFIG_HOME', 'USER', 'LOGNAME')
_cache = {}


def entorno(**extra):
    """Lo justo para git: sin las claves del usuario ni GIT_DIR/GIT_WORK_TREE heredados."""
    valores = {k: os.environ[k] for k in _ENTORNO if k in os.environ}
    valores.update(GIT_TERMINAL_PROMPT='0', GIT_OPTIONAL_LOCKS='0')
    valores.update({k: str(v) for k, v in extra.items()})
    return valores


def _huella_de_la_configuracion(raiz):
    config = Path(raiz) / '.git' / 'config'
    try:
        return config.stat().st_mtime_ns
    except OSError:
        return None


def opciones(raiz):
    """Las opciones `-c` que anulan lo que ejecutaría programas en este repositorio."""
    raiz = str(raiz)
    huella = _huella_de_la_configuracion(raiz)
    guardadas = _cache.get(raiz)
    if guardadas and huella is not None and guardadas[0] == huella:
        return list(guardadas[1])
    try:
        definidas = subprocess.run(['git', *FIJAS, '-C', raiz, 'config', '--name-only', '--get-regexp', _CON_PROGRAMA],
                                   capture_output=True, text=True, timeout=10, env=entorno(),
                                   stdin=subprocess.DEVNULL)
        claves = definidas.stdout.splitlines() if definidas.returncode == 0 else []
    except (OSError, subprocess.SubprocessError):
        claves = []
    resultado = list(FIJAS)
    for clave in sorted(set(claves)):
        resultado += ['-c', clave + ('=false' if clave.endswith('.required') else '=')]
    _cache[raiz] = (huella, tuple(resultado))
    return resultado


def run(raiz, *args, env=None, **kwargs):
    """`git -C raiz …` como subprocess.run, con las opciones y el entorno seguros."""
    kwargs.setdefault('stdin', subprocess.DEVNULL)
    return subprocess.run(['git', *opciones(raiz), '-C', str(raiz), *args],
                          env=entorno(**(env or {})), **kwargs)
