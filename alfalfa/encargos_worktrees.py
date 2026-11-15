"""Worktrees de una cola: sólo Git local, sin hooks, fsmonitor ni filtros de checkout."""
from __future__ import annotations

import subprocess
from pathlib import Path

OPCIONES = ['-c', 'core.hooksPath=/dev/null', '-c', 'core.fsmonitor=false']


def opciones_seguras(raiz):
    # Las mismas que el resto del git del arnés (git_arnes): sin hooks, fsmonitor, filtros ni textconv.
    from .git_arnes import opciones
    return opciones(raiz)


def git(raiz, *args):
    resultado = subprocess.run(['git', *opciones_seguras(raiz), '-C', str(raiz), *args], capture_output=True,
                               text=True, timeout=30)
    if resultado.returncode:
        raise ValueError('Git no pudo preparar la cola: ' + (resultado.stderr.strip() or 'operación rechazada')[:400])
    return resultado.stdout.strip()


def base_limpia(raiz):
    raiz = Path(raiz).resolve()
    if Path(git(raiz, 'rev-parse', '--show-toplevel')).resolve() != raiz:
        raise ValueError('La cola en paralelo necesita la carpeta raíz de un repositorio Git.')
    if git(raiz, 'status', '--porcelain', '--untracked-files=normal'):
        raise ValueError('Guarda o confirma los cambios del proyecto antes de iniciar la cola en paralelo. '
                         'Cada worktree parte del mismo HEAD; los archivos ignorados no se copian.')
    return git(raiz, 'rev-parse', '--verify', 'HEAD^{commit}')


def preparar(raiz, destino, rama, base):
    destino = Path(destino)
    if destino.exists():
        # Reanudar no pisa ni regenera el trabajo que ya hay.
        if (destino.is_symlink() or not (destino / '.git').is_file()
                or git(destino, 'rev-parse', '--git-common-dir') == '.git'):
            raise ValueError('El worktree guardado ya no está disponible; revisa su carpeta antes de continuar.')
        comun = Path(git(raiz, 'rev-parse', '--path-format=absolute', '--git-common-dir')).resolve()
        if Path(git(destino, 'rev-parse', '--path-format=absolute', '--git-common-dir')).resolve() != comun:
            raise ValueError('La carpeta guardada no pertenece al repositorio de la cola.')
        if git(destino, 'symbolic-ref', '--short', 'HEAD') != rama:
            raise ValueError('La rama del worktree cambió; revísala antes de reanudar la cola.')
        return
    # Un .gitattributes puede activar filtros del config al hacer checkout. Desactivarlos
    # evita ejecutar programas del proyecto sólo por elegir «En paralelo».
    opciones = opciones_seguras(raiz)
    resultado = subprocess.run(['git', *opciones, '-C', str(raiz), 'worktree', 'add', '-b', rama,
                               str(destino), base], capture_output=True, text=True, timeout=30)
    if resultado.returncode:
        raise ValueError('No se pudo crear el worktree; se conserva cualquier trabajo existente. '
                         + resultado.stderr.strip()[:400])
