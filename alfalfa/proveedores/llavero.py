"""Claves de los proveedores en el Llavero de macOS, nunca en texto plano.

Se guardan como contraseñas genéricas del servicio `AlfalfaCode`, una cuenta por
proveedor (`ollama`, `openai`, `alfalfa` y el id de cada compatible con OpenAI,
como `openrouter`). La orden `security` del sistema las
lee y las escribe, así que la terminal y la app de Mac comparten las mismas sin
que el Llavero pregunte a cada uso. El secreto nunca va en los argumentos de la
orden (se verían con `ps`): se le pasa por la entrada estándar de `security -i`.

Fuera de macOS no hay Llavero: leer devuelve None y guardar falla con un error
claro; ahí las claves llegan por variables de entorno.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
import time

SERVICIO = 'AlfalfaCode'
CUENTAS = ('ollama', 'openai', 'alfalfa')
# Las claves de las API son ASCII imprimible sin espacios; así no hay comillas
# ni barras que escapar al hablar con `security -i`.
_SECRETO = re.compile(r'^[\x21-\x7e]{8,512}$')

# Las pruebas sustituyen el Llavero por un diccionario: nunca tocan el real.
_almacen: dict | None = None
# El protocolo sanea cada mensaje con las claves: sin memoria, cada línea
# lanzaría `security`. Lo leído vale un minuto o hasta guardar o borrar.
VIGENCIA = 60.0
_leidos: dict = {}


class LlaveroNoDisponible(RuntimeError):
    pass


def _security():
    if sys.platform != 'darwin':
        return None
    return shutil.which('security') or '/usr/bin/security'


# Los compatibles con OpenAI del catálogo guardan su clave con su id (`openrouter`).
_CUENTA = re.compile(r'^[a-z0-9][a-z0-9-]{0,63}$')


def _cuenta(cuenta):
    if cuenta not in CUENTAS and not (isinstance(cuenta, str) and _CUENTA.fullmatch(cuenta)):
        raise ValueError('Proveedor desconocido para el Llavero: ' + str(cuenta))
    return cuenta


def leer(cuenta):
    """El secreto de `cuenta`, o None si no hay (o no hay Llavero)."""
    _cuenta(cuenta)
    if _almacen is not None:
        return _almacen.get(cuenta)
    guardado = _leidos.get(cuenta)
    if guardado and time.monotonic() - guardado[0] < VIGENCIA:
        return guardado[1]
    orden = _security()
    if not orden:
        return None
    try:
        hecho = subprocess.run([orden, 'find-generic-password', '-s', SERVICIO, '-a', cuenta, '-w'],
                               capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    valor = (hecho.stdout.strip() if hecho.returncode == 0 else '') or None
    _leidos[cuenta] = (time.monotonic(), valor)
    return valor


def guardar(cuenta, secreto):
    """Guarda (o sustituye) el secreto de `cuenta`."""
    _cuenta(cuenta)
    secreto = (secreto or '').strip()
    if not _SECRETO.fullmatch(secreto) or '"' in secreto or '\\' in secreto:
        raise ValueError('La clave no parece válida: debe ser texto sin espacios, comillas ni barras.')
    _leidos.pop(cuenta, None)
    if _almacen is not None:
        _almacen[cuenta] = secreto
        return
    orden = _security()
    if not orden:
        raise LlaveroNoDisponible('Aquí no hay Llavero de macOS: usa la variable de entorno del proveedor.')
    linea = f'add-generic-password -U -s {SERVICIO} -a {cuenta} -l "AlfalfaCode {cuenta}" -w "{secreto}"\n'
    hecho = subprocess.run([orden, '-i'], input=linea, capture_output=True, text=True, timeout=15)
    if hecho.returncode != 0 or 'error' in (hecho.stderr or '').lower():
        raise LlaveroNoDisponible('El Llavero no aceptó la clave de ' + cuenta + '.')


def borrar(cuenta):
    """Borra el secreto de `cuenta`; no falla si no estaba."""
    _cuenta(cuenta)
    _leidos.pop(cuenta, None)
    if _almacen is not None:
        _almacen.pop(cuenta, None)
        return
    orden = _security()
    if not orden:
        return
    subprocess.run([orden, 'delete-generic-password', '-s', SERVICIO, '-a', cuenta],
                   capture_output=True, text=True, timeout=10)
