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


# Limpieza. Sólo se quita un worktree de la cola (y su rama) si no guarda nada que no esté ya en otro sitio:
# sin cambios ni archivos nuevos sin seguimiento (los ignorados se van con la carpeta), en su rama y sin commits
# que no estén ya en el punto de partida, la rama base o la rama actual del repositorio. Nunca `--force` ni
# `branch -D`: si Git se niega, se conserva y se dice por qué.
QUITADO, CONSERVADO, RAMA_CONSERVADA = 'quitado', 'conservado', 'rama_conservada'


def _correr(raiz, *args):
    from .git_arnes import run
    resultado = run(raiz, *args, capture_output=True, text=True, timeout=30)
    return resultado.returncode, resultado.stdout.strip(), resultado.stderr.strip()


def rama_actual(raiz):
    """La rama del repositorio al empezar la cola (None con HEAD suelto)."""
    codigo, salida, _ = _correr(raiz, 'symbolic-ref', '-q', '--short', 'HEAD')
    return salida if codigo == 0 and salida else None


def _registros(raiz):
    """Los worktrees que Git tiene registrados, por ruta real."""
    codigo, salida, error = _correr(raiz, 'worktree', 'list', '--porcelain')
    if codigo:
        raise ValueError('Git no pudo listar los worktrees: ' + (error or 'operación rechazada')[:300])
    registros, actual = {}, None
    for linea in salida.splitlines() + ['']:
        if linea.startswith('worktree '):
            actual = {'ruta': linea[len('worktree '):]}
        elif actual is not None and linea:
            clave, _, valor = linea.partition(' ')
            actual[clave] = valor
        elif actual is not None:
            registros[str(Path(actual['ruta']).resolve())] = actual
            actual = None
    return registros


def _plural(n, uno, varios):
    return f'{n} {uno if n == 1 else varios}'


def _sin_confirmar(carpeta):
    codigo, salida, error = _correr(carpeta, 'status', '--porcelain', '--untracked-files=all',
                                    '--ignore-submodules=none')
    if codigo:
        return 'Git no pudo leer su estado (' + (error or 'sin detalle')[:200] + ')'
    lineas = salida.splitlines()
    nuevos = sum(linea.startswith('??') for linea in lineas)
    partes = []
    if len(lineas) - nuevos:
        partes.append('tiene ' + _plural(len(lineas) - nuevos, 'cambio sin confirmar', 'cambios sin confirmar'))
    if nuevos:
        partes.append(('tiene ' if not partes else '') + _plural(nuevos, 'archivo nuevo sin seguimiento',
                                                                 'archivos nuevos sin seguimiento'))
    return ' y '.join(partes) or None


def _commits_propios(raiz, punta, base, rama_base):
    """Cuántos commits de `punta` no están en el punto de partida, la rama base ni la rama actual."""
    contra = [base]
    for ref in ([('refs/heads/' + rama_base)] if rama_base else []) + ['HEAD']:
        codigo, sha, _ = _correr(raiz, 'rev-parse', '--verify', '-q', ref + '^{commit}')
        if codigo == 0 and sha:
            contra.append(sha)
    codigo, salida, error = _correr(raiz, 'rev-list', '--count', punta, '--not', *contra)
    if codigo or not salida.isdigit():
        raise ValueError('Git no pudo comparar la rama (' + (error or 'sin detalle')[:200] + ')')
    return int(salida)


def limpiar(raiz, carpeta, rama, base, rama_base=None):
    """Quita el worktree `carpeta` y su `rama` si no guardan trabajo propio. Devuelve (estado, motivo)."""
    try:
        return _limpiar(Path(raiz).resolve(), Path(carpeta), rama, base, rama_base)
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        return CONSERVADO, 'no se pudo comprobar: ' + str(exc)[:300]


def _limpiar(raiz, carpeta, rama, base, rama_base):
    if Path(_correr(raiz, 'rev-parse', '--show-toplevel')[1] or '/').resolve() != raiz:
        return CONSERVADO, 'la carpeta del proyecto ya no es la raíz de su repositorio'
    registro = _registros(raiz).get(str(carpeta.resolve()))
    existe = carpeta.exists() or carpeta.is_symlink()
    if registro is not None and 'locked' in registro:
        return CONSERVADO, 'el worktree está bloqueado (git worktree lock)'
    if existe:
        if carpeta.is_symlink() or not (carpeta / '.git').is_file() or registro is None:
            return CONSERVADO, 'la carpeta ya no es un worktree registrado de este repositorio'
        comun = _correr(raiz, 'rev-parse', '--path-format=absolute', '--git-common-dir')[1]
        propio = _correr(carpeta, 'rev-parse', '--path-format=absolute', '--git-common-dir')[1]
        if not comun or not propio or Path(propio).resolve() != Path(comun).resolve():
            return CONSERVADO, 'la carpeta ya no pertenece al repositorio de la cola'
        if registro.get('branch') != 'refs/heads/' + rama:
            return CONSERVADO, 'su HEAD ya no está en la rama ' + rama
        pendiente = _sin_confirmar(carpeta)
        if pendiente:
            return CONSERVADO, pendiente
    elif registro is not None and registro.get('branch') != 'refs/heads/' + rama:
        # Sin carpeta, lo único que queda del worktree es su registro; con HEAD suelto, el registro
        # es lo que apunta a esos commits.
        return CONSERVADO, 'la carpeta ya no existe y su HEAD no estaba en la rama ' + rama
    codigo, punta, _ = _correr(raiz, 'rev-parse', '--verify', '-q', 'refs/heads/' + rama + '^{commit}')
    punta = punta if codigo == 0 and punta else None
    if punta:
        propios = _commits_propios(raiz, punta, base, rama_base)
        if propios:
            return CONSERVADO, ('tiene ' + _plural(propios, 'commit sin integrar', 'commits sin integrar')
                                + ' en ' + (rama_base or 'la rama base'))
    if registro is not None:
        # Sin --force: Git vuelve a comprobar que está limpio. Con la carpeta ya borrada, quita sólo
        # su registro (lo que haría `git worktree prune`, pero sin tocar otros worktrees del usuario).
        codigo, _, error = _correr(raiz, 'worktree', 'remove', str(carpeta))
        if codigo:
            return CONSERVADO, 'Git no quitó el worktree: ' + (error or 'operación rechazada')[:300]
    if punta:
        # -d, no -D: Git se niega si la rama tiene algo que no está en HEAD.
        codigo, _, error = _correr(raiz, 'branch', '-d', rama)
        if codigo:
            return RAMA_CONSERVADA, 'se quitó el worktree, pero Git conserva la rama: ' + (error or '')[:300]
    return QUITADO, None
