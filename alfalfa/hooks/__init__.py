"""Hooks compatibles con Claude Code para AlfalfaCode (ver motor.py y PROCEDENCIA.md).

Sólo se cargan hooks que el usuario instaló: `$ALFALFA_HOME/hooks.json` y los
de los complementos que activó en `$ALFALFA_HOME/complementos.json`. Un
`.claude/settings.json` o un `hooks/hooks.json` de la carpeta de trabajo nunca
se cargan: el proyecto no puede ejecutar nada por su cuenta.
"""
from __future__ import annotations

import json
from pathlib import Path

from .motor import EVENTOS, MotorHooks, leer_fuente  # noqa: F401
from .sesion import HooksDeSesion  # noqa: F401

ARCHIVO = 'hooks.json'


def fuente_usuario(home):
    """La fuente de `$ALFALFA_HOME/hooks.json`, o None si no existe.

    Además del formato de Claude Code admite `entorno`: las variables que el
    usuario declara para sus hooks (nunca credenciales del arnés).
    """
    ruta = Path(home) / ARCHIVO
    if not ruta.exists() and not ruta.is_symlink():
        return None
    fuente = dict(archivo=ruta, origen='usuario')
    try:
        if not ruta.is_symlink() and ruta.is_file() and ruta.stat().st_size <= 1024 * 1024:
            datos = json.loads(ruta.read_text(encoding='utf-8'))
            if isinstance(datos, dict) and 'entorno' in datos:
                fuente['entorno'] = datos['entorno']
                fuente['datos'] = {k: v for k, v in datos.items() if k != 'entorno'}
    except (OSError, ValueError, UnicodeError):
        pass  # leer_fuente lo diagnostica al leer el archivo
    return fuente


def motor_de_sesion(home, raiz, *, fuentes_complementos=(), sandbox=None):
    fuentes = [f for f in [fuente_usuario(home)] if f] + list(fuentes_complementos)
    return MotorHooks.desde_fuentes(fuentes, raiz=raiz, sandbox=sandbox)
