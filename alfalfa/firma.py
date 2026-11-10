"""La firma de AlfalfaCode en los commits que hace su agente.

Cuando el agente ejecuta `git commit` con run_command, el arnés añade
`--trailer "Co-Authored-By: AlfalfaCode <alfalfacode@cuycoders.dev>"` justo
después de `commit`. Lo hace antes de pedir permiso, para que la tarjeta enseñe
la orden real. GitHub enlaza ese correo con la cuenta `alfalfacode` y la muestra
entre los colaboradores del repositorio, como hace con Claude y con Codex.

No se firma si:
- el mensaje ya lleva la firma;
- la orden solo enseña algo (`--dry-run`, `-h`, `--help`);
- el git instalado es anterior a la 2.32, que es la primera con `--trailer`.

Se apaga con `--sin-firma`, con `ALFALFA_FIRMA=0` o con el interruptor de
Ajustes ▸ General de la app. Una aplicación que integra el arnés cambia la línea,
o la quita, con `ajustes.firma_de_commits()`.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

CORREO = 'alfalfacode@cuycoders.dev'
FIRMA = 'Co-Authored-By: AlfalfaCode <' + CORREO + '>'

# Opciones de git que van antes de la suborden y llevan su valor en el argumento siguiente.
CON_VALOR = frozenset({'-C', '-c', '--git-dir', '--work-tree', '--namespace', '--super-prefix', '--config-env'})
# Con ellas `git commit` no crea ningún commit.
SIN_COMMIT = frozenset({'--dry-run', '-h', '--help'})
VERSION_MINIMA = (2, 32)

_versiones = {}


def por_entorno():
    """Lo que dice ALFALFA_FIRMA. Si no está, la firma va encendida."""
    return os.environ.get('ALFALFA_FIRMA', '1').strip().lower() not in ('0', 'no', 'false', 'off')


class Firma:
    """El interruptor de una sesión: None hace caso a ALFALFA_FIRMA; True o False lo fijan."""

    def __init__(self, activa=None):
        self._fijada = activa if isinstance(activa, bool) else None

    def cambiar(self, activa):
        self._fijada = bool(activa)

    def activa(self):
        return por_entorno() if self._fijada is None else self._fijada


def texto(manager):
    """La línea con la que se firman los commits de la sesión de `manager`, o None si no se firma."""
    interruptor = getattr(manager, 'firma', None)
    if isinstance(interruptor, Firma):
        if not interruptor.activa():
            return None
    elif not por_entorno():
        return None
    from . import ajustes
    linea = ajustes.firma_de_commits()
    if not isinstance(linea, str) or not linea.strip() or '\n' in linea or '\r' in linea:
        return None
    return linea.strip()


def _subcomando(argv):
    """El índice de la suborden de git en `argv`, o None si no es git."""
    if not argv or Path(str(argv[0])).name != 'git':
        return None
    i = 1
    while i < len(argv) and str(argv[i]).startswith('-'):
        i += 2 if argv[i] in CON_VALOR else 1
    return i if i < len(argv) else None


def _admite_trailer(programa):
    """Si el git de `programa` ya tiene `git commit --trailer` (2.32 o posterior)."""
    if programa not in _versiones:
        try:
            salida = subprocess.run([programa, '--version'], capture_output=True, text=True, timeout=5).stdout
            numeros = re.search(r'(\d+)\.(\d+)', salida or '')
            _versiones[programa] = bool(numeros) and (int(numeros[1]), int(numeros[2])) >= VERSION_MINIMA
        except (OSError, subprocess.SubprocessError):
            _versiones[programa] = False
    return _versiones[programa]


def firmar(argv, linea):
    """`argv` con la firma si es un `git commit` que hay que firmar; si no, el mismo `argv`."""
    if not linea:
        return argv
    i = _subcomando(argv)
    if i is None or argv[i] != 'commit':
        return argv
    resto = [str(a) for a in argv[i + 1:]]
    if any(a in SIN_COMMIT for a in resto):
        return argv
    correo = re.search(r'<([^<>]+)>', linea)
    buscado = (correo[1] if correo else linea).lower()
    if any(buscado in a.lower() for a in resto):
        return argv
    if not _admite_trailer(str(argv[0])):
        return argv
    return list(argv[:i + 1]) + ['--trailer', linea] + list(argv[i + 1:])
