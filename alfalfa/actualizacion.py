"""Buscar e instalar versiones nuevas de AlfalfaCode.

Una versión es una etiqueta `vX.Y.Z` del repositorio. Hay dos formas de tenerlo
instalado y cada una se actualiza a su manera:

- clon: la carpeta del paquete está dentro de un repositorio git (es lo que usa
  `scripts/alfalfa` y la app de Mac). Se trae la etiqueta y se avanza la rama
  sólo si no hay cambios sin guardar ni commits propios.
- paquete: instalado con pip desde GitHub. Se reinstala con pip en la etiqueta
  nueva, en el mismo Python que está ejecutando la orden.
"""
import os
from pathlib import Path
import re
import subprocess
import sys

from . import __version__

REPO_POR_DEFECTO = 'https://github.com/0sw2ld0/AlfalfaCode.git'
_ETIQUETA = re.compile(r'^v(\d+)\.(\d+)\.(\d+)$')
_ESPERA = 120


class ErrorDeActualizacion(Exception):
    """Algo impide buscar o instalar; el texto se enseña tal cual al usuario."""


def clave(version):
    """(1, 2, 3) para «v1.2.3» o «1.2.3»; None si no es una versión."""
    texto = version if version.startswith('v') else 'v' + version
    m = _ETIQUETA.match(texto)
    return tuple(int(n) for n in m.groups()) if m else None


def raiz_del_clon(paquete=None):
    """Carpeta del repositorio si el paquete vive en un clon de AlfalfaCode."""
    paquete = Path(paquete or Path(__file__).resolve().parent)
    raiz = paquete.parent
    if (raiz / '.git').exists() and (raiz / 'pyproject.toml').is_file():
        return raiz
    return None


def _git(*args, cwd=None):
    entorno = dict(os.environ, GIT_TERMINAL_PROMPT='0')
    try:
        r = subprocess.run(['git', *args], cwd=cwd, env=entorno, capture_output=True,
                           text=True, timeout=_ESPERA)
    except FileNotFoundError:
        raise ErrorDeActualizacion('No está instalado git.')
    except subprocess.TimeoutExpired:
        raise ErrorDeActualizacion('GitHub no respondió a tiempo.')
    if r.returncode != 0:
        raise ErrorDeActualizacion((r.stderr or r.stdout).strip() or f'git {args[0]} falló.')
    return r.stdout


def url_del_repo(raiz=None):
    if os.environ.get('ALFALFA_REPO_URL'):
        return os.environ['ALFALFA_REPO_URL']
    if raiz:
        try:
            return _git('remote', 'get-url', 'origin', cwd=raiz).strip() or REPO_POR_DEFECTO
        except ErrorDeActualizacion:
            pass
    return REPO_POR_DEFECTO


def ultima_version(url):
    """La etiqueta más alta `vX.Y.Z` del repositorio, o None si no hay ninguna."""
    try:
        salida = _git('ls-remote', '--tags', '--refs', url)
    except ErrorDeActualizacion as exc:
        raise ErrorDeActualizacion('No se pudo consultar el repositorio de AlfalfaCode: ' + str(exc))
    etiquetas = [linea.rsplit('refs/tags/', 1)[-1] for linea in salida.splitlines() if 'refs/tags/' in linea]
    validas = [e for e in etiquetas if clave(e)]
    return max(validas, key=clave) if validas else None


def buscar(raiz=None, instalada=__version__):
    """Qué versión hay instalada, cuál es la última y si conviene actualizar."""
    if raiz is None:
        raiz = raiz_del_clon()
    url = url_del_repo(raiz)
    ultima = ultima_version(url)
    hay_nueva = bool(ultima and clave(instalada) and clave(ultima) > clave(instalada))
    return {
        'instalada': instalada,
        'ultima': ultima.lstrip('v') if ultima else None,
        'etiqueta': ultima,
        'hay_nueva': hay_nueva,
        'instalacion': 'clon' if raiz else 'paquete',
        'url': url,
    }


def actualizar(raiz=None, instalada=__version__, python=sys.executable):
    """Instala la última versión si es más nueva. Devuelve lo que pasó."""
    if raiz is None:
        raiz = raiz_del_clon()
    info = buscar(raiz, instalada)
    if not info['hay_nueva']:
        return dict(info, actualizado=False, app_mac_cambio=False)
    etiqueta = info['etiqueta']
    app_mac_cambio = False
    if raiz:
        if _git('status', '--porcelain', '--untracked-files=no', cwd=raiz).strip():
            raise ErrorDeActualizacion(
                f'El clon de {raiz} tiene cambios sin guardar; guárdalos o descártalos antes de actualizar.')
        antes = _git('rev-parse', 'HEAD', cwd=raiz).strip()
        _git('fetch', '--tags', info['url'], f'refs/tags/{etiqueta}:refs/tags/{etiqueta}', cwd=raiz)
        try:
            _git('merge', '--ff-only', f'refs/tags/{etiqueta}', cwd=raiz)
        except ErrorDeActualizacion:
            raise ErrorDeActualizacion(
                f'La rama del clon tiene commits que no están en {etiqueta}; actualízala a mano.')
        cambios = _git('diff', '--name-only', antes, 'HEAD', cwd=raiz).splitlines()
        app_mac_cambio = any(c.startswith('alfalfa-mac/') for c in cambios)
        # Las dependencias pueden haber cambiado con la versión.
        _pip(python, '-e', str(raiz))
    else:
        _pip(python, '--upgrade', f'alfalfa @ git+{info["url"]}@{etiqueta}')
    return dict(info, actualizado=True, app_mac_cambio=app_mac_cambio)


def _pip(python, *args):
    try:
        r = subprocess.run([python, '-m', 'pip', 'install', '--quiet', *args], capture_output=True,
                           text=True, timeout=_ESPERA * 5, env=dict(os.environ, GIT_TERMINAL_PROMPT='0'))
    except subprocess.TimeoutExpired:
        raise ErrorDeActualizacion('pip tardó demasiado en instalar la versión nueva.')
    if r.returncode != 0:
        ultimas = '\n'.join((r.stderr or r.stdout).strip().splitlines()[-5:])
        raise ErrorDeActualizacion('pip no pudo instalar la versión nueva:\n' + ultimas)


def texto(resultado):
    """Una frase para la terminal."""
    if resultado.get('actualizado'):
        frase = f'AlfalfaCode actualizado a {resultado["ultima"]} (tenías {resultado["instalada"]}).'
        if resultado.get('app_mac_cambio'):
            frase += ' La app de Mac también cambió: vuelve a compilarla con `swift build` en alfalfa-mac.'
        return frase
    if resultado['hay_nueva']:
        return (f'Hay una versión nueva: {resultado["ultima"]} (tienes {resultado["instalada"]}). '
                'Instálala con `alfalfa --actualizar`.')
    if resultado['ultima'] is None:
        return f'Tienes {resultado["instalada"]}; el repositorio todavía no tiene versiones publicadas.'
    if clave(resultado['instalada']) and clave(resultado['instalada']) > clave(resultado['ultima']):
        return f'Tienes {resultado["instalada"]}, más nueva que la última publicada ({resultado["ultima"]}).'
    return f'Tienes la última versión ({resultado["instalada"]}).'


def orden(actualizar_ya, como_json, salida=None):
    """`alfalfa --buscar-actualizacion` y `alfalfa --actualizar`."""
    salida = salida or sys.stdout
    if como_json:
        from .stdio import Transport
        transporte = Transport(salida)
    try:
        resultado = actualizar() if actualizar_ya else dict(buscar(), actualizado=False, app_mac_cambio=False)
    except ErrorDeActualizacion as exc:
        if como_json:
            transporte.emit('error', texto=str(exc))
        else:
            print('AlfalfaCode · Error: ' + str(exc), file=sys.stderr)
        return 1
    if como_json:
        transporte.emit('actualizacion', texto=texto(resultado), **resultado)
    else:
        print(texto(resultado), file=salida)
    return 0
