"""Buscar e instalar versiones nuevas de AlfalfaCode.

Una versión es una etiqueta `vX.Y.Z` del repositorio. Hay dos formas de tenerlo
instalado y cada una se actualiza a su manera:

- clon: la carpeta del paquete está dentro de un repositorio git (es lo que usa
  `scripts/alfalfa` y la app de Mac). Se trae la etiqueta y se avanza la rama
  sólo si no hay cambios sin guardar ni commits propios.
- paquete: instalado con pip desde GitHub. Se reinstala con pip en la etiqueta
  nueva, en el mismo Python que está ejecutando la orden.
- app: va dentro de `AlfalfaCode.app` (`empaquetado.py`). La versión se busca por
  HTTPS, sin git (en un Mac sin las herramientas de línea de órdenes, `git`
  abre el diálogo de instalarlas), y no se instala nada: el bundle está firmado.
  Se descarga el DMG nuevo y se arrastra a Aplicaciones.
- curl: la CLI suelta que dejó `instalar.sh` en `~/.local/share/alfalfa` (el motor de
  la app copiado de su DMG). Se actualiza volviendo a pasar el `instalar.sh` que viaja
  con el paquete, que baja la última release y comprueba su huella y su firma.
- brew: la fórmula `alfalfa` del tap. La actualiza Homebrew (`brew upgrade alfalfa`);
  cambiarla por debajo dejaría a Homebrew creyendo que tiene otra versión.
"""
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from . import __version__, empaquetado

REPO_POR_DEFECTO = 'https://github.com/0sw2ld0/AlfalfaCode.git'
_ETIQUETA = re.compile(r'^v(\d+)\.(\d+)\.(\d+)$')
_ESPERA = 120
PAGINA_DE_DESCARGA = 'https://github.com/0sw2ld0/AlfalfaCode/releases/latest'
INSTALADOR = Path(__file__).with_name('instalar.sh')
ORDEN_CURL = 'curl -fsSL https://alfalfacode.cuycoders.dev/instalar.sh | sh'
ORDEN_BREW = 'brew upgrade alfalfa'
_GITHUB = re.compile(r'^https://github\.com/([\w.-]+)/([\w.-]+?)(?:\.git)?/?$')


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


def ultima_version_https(url, get=None):
    """Como `ultima_version`, por la API de GitHub y sin git (para la app empaquetada)."""
    m = _GITHUB.match(url)
    if not m:
        raise ErrorDeActualizacion('Sólo se pueden buscar versiones de un repositorio de github.com: ' + url)
    if get is None:
        import httpx
        get = httpx.get
    try:
        r = get(f'https://api.github.com/repos/{m[1]}/{m[2]}/tags', params={'per_page': 100},
                headers={'Accept': 'application/vnd.github+json', 'User-Agent': f'AlfalfaCode/{__version__}'},
                timeout=20, follow_redirects=True)
        r.raise_for_status()
        etiquetas = [e.get('name', '') for e in r.json() if isinstance(e, dict)]
    except Exception as exc:
        raise ErrorDeActualizacion('No se pudo consultar el repositorio de AlfalfaCode: '
                                   + (str(exc) or type(exc).__name__)) from None
    validas = [e for e in etiquetas if isinstance(e, str) and clave(e)]
    return max(validas, key=clave) if validas else None


FEED_RELEASE = 'https://github.com/0sw2ld0/AlfalfaCode/releases/latest/download/version.json'


def ultima_release(get=None):
    """La versión de la última release (`version.json`, el mismo que lee instalar.sh), como
    etiqueta `vX.Y.Z`; None si no la hay. Una etiqueta sin release todavía no se puede instalar."""
    if get is None:
        import httpx
        get = httpx.get
    try:
        r = get(FEED_RELEASE, headers={'User-Agent': f'AlfalfaCode/{__version__}'}, timeout=20,
                follow_redirects=True)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        version = r.json().get('version')
    except Exception as exc:
        raise ErrorDeActualizacion('No se pudo consultar la última versión de AlfalfaCode: '
                                   + (str(exc) or type(exc).__name__)) from None
    return 'v' + version if isinstance(version, str) and clave(version) else None


def _tipo(app=None, instalacion=None):
    """'app', 'curl', 'brew' o None (clon o paquete de pip, que se deciden con git)."""
    if instalacion:
        return instalacion
    if app is not None:
        return 'app' if app else None
    return empaquetado.instalacion()


def buscar(raiz=None, instalada=__version__, app=None, instalacion=None):
    """Qué versión hay instalada, cuál es la última y si conviene actualizar."""
    tipo = _tipo(app, instalacion)
    if tipo:
        # La app y la CLI suelta no tienen git a mano: la versión se pregunta por HTTPS. La de
        # curl, a la release que instalaría instalar.sh (no a la etiqueta más alta).
        url = os.environ.get('ALFALFA_REPO_URL') or REPO_POR_DEFECTO
        ultima = ultima_release() if tipo == 'curl' else ultima_version_https(url)
        instalacion = tipo
    else:
        if raiz is None:
            raiz = raiz_del_clon()
        url = url_del_repo(raiz)
        ultima = ultima_version(url)
        instalacion = 'clon' if raiz else 'paquete'
    hay_nueva = bool(ultima and clave(instalada) and clave(ultima) > clave(instalada))
    info = {
        'instalada': instalada,
        'ultima': ultima.lstrip('v') if ultima else None,
        'etiqueta': ultima,
        'hay_nueva': hay_nueva,
        'instalacion': instalacion,
        'url': url,
    }
    if tipo == 'app':
        info['descarga'] = PAGINA_DE_DESCARGA
    elif tipo == 'brew':
        info['orden'] = ORDEN_BREW
    elif tipo == 'curl':
        info['orden'] = ORDEN_CURL
    return info


def actualizar(raiz=None, instalada=__version__, python=sys.executable, app=None, instalacion=None,
               instalador=None):
    """Instala la última versión si es más nueva. Devuelve lo que pasó."""
    tipo = _tipo(app, instalacion)
    if raiz is None and not tipo:
        raiz = raiz_del_clon()
    info = buscar(raiz, instalada, instalacion=tipo)
    if not info['hay_nueva']:
        return dict(info, actualizado=False, app_mac_cambio=False)
    if info['instalacion'] == 'app':
        # Escribir en el bundle rompería su firma: la versión nueva llega en un DMG nuevo.
        raise ErrorDeActualizacion(
            f'Esta copia va dentro de AlfalfaCode.app y no se cambia sola. Descarga AlfalfaCode '
            f'{info["ultima"]} de {info["descarga"]} y arrástrala a Aplicaciones.')
    if info['instalacion'] == 'brew':
        raise ErrorDeActualizacion(
            f'Esta copia la instaló Homebrew y la actualiza Homebrew: `{ORDEN_BREW}` '
            f'(hay {info["ultima"]}, tienes {info["instalada"]}).')
    if info['instalacion'] == 'curl':
        # instalar.sh cambia la carpeta de este mismo motor mientras corre: después de esto sólo
        # se usa lo ya importado (`texto`, `Transport`), nada que haya que leer de disco.
        _reinstalar(instalador or INSTALADOR, empaquetado.contenidos())
        return dict(info, actualizado=True, app_mac_cambio=False)
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


def _reinstalar(instalador, raiz):
    """Vuelve a pasar `instalar.sh` sobre la misma carpeta: baja la última release, comprueba
    su huella y su firma y cambia la copia entera (o no toca nada si algo falla)."""
    if not Path(instalador).is_file():
        raise ErrorDeActualizacion('Falta instalar.sh en esta copia. Reinstálala con: ' + ORDEN_CURL)
    entorno = dict(os.environ)
    if raiz is not None:
        # raiz = <prefijo>/share/alfalfa
        entorno['ALFALFA_PREFIJO'] = str(Path(raiz).parent.parent)
    try:
        r = subprocess.run(['/bin/sh', str(instalador)], env=entorno, capture_output=True, text=True,
                           timeout=_ESPERA * 5)
    except subprocess.TimeoutExpired:
        raise ErrorDeActualizacion('La descarga de la versión nueva tardó demasiado.')
    if r.returncode != 0:
        ultimas = '\n'.join((r.stderr or r.stdout).strip().splitlines()[-5:])
        raise ErrorDeActualizacion('No se pudo instalar la versión nueva:\n' + ultimas)


def _pip(python, *args):
    """`pip install` en ese Python; si el entorno no trae pip (los de `uv venv`), con `uv pip`."""
    orden = [python, '-m', 'pip', 'install', '--quiet', *args]
    uv = shutil.which('uv')
    if uv and not _tiene_pip(python):
        orden = [uv, 'pip', 'install', '--quiet', '--python', python, *args]
    try:
        r = subprocess.run(orden, capture_output=True, text=True, timeout=_ESPERA * 5,
                           env=dict(os.environ, GIT_TERMINAL_PROMPT='0'))
    except subprocess.TimeoutExpired:
        raise ErrorDeActualizacion('pip tardó demasiado en instalar la versión nueva.')
    if r.returncode != 0:
        ultimas = '\n'.join((r.stderr or r.stdout).strip().splitlines()[-5:])
        raise ErrorDeActualizacion('pip no pudo instalar la versión nueva:\n' + ultimas)


def _tiene_pip(python):
    try:
        return subprocess.run([python, '-m', 'pip', '--version'], capture_output=True, timeout=30).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def instalar_dependencias(raiz=None, python=sys.executable):
    """Vuelve a instalar el clon en su sitio para que estén sus dependencias (por ejemplo,
    tras un `git pull` que añadió una). Instalado con pip, pip ya las trajo."""
    if raiz is None:
        raiz = raiz_del_clon()
    if not raiz:
        raise ErrorDeActualizacion('AlfalfaCode no está en un clon: reinstálalo con pip para traer sus dependencias.')
    _pip(python, '-e', str(raiz))


def texto(resultado):
    """Una frase para la terminal."""
    if resultado.get('actualizado'):
        frase = f'AlfalfaCode actualizado a {resultado["ultima"]} (tenías {resultado["instalada"]}).'
        if resultado.get('app_mac_cambio'):
            frase += ' La app de Mac también cambió: vuelve a compilarla con `swift build` en alfalfa-mac.'
        return frase
    if resultado['hay_nueva'] and resultado.get('instalacion') == 'app':
        return (f'Hay una versión nueva: {resultado["ultima"]} (tienes {resultado["instalada"]}). '
                f'Descárgala de {resultado["descarga"]} y arrástrala a Aplicaciones.')
    if resultado['hay_nueva'] and resultado.get('instalacion') == 'brew':
        return (f'Hay una versión nueva: {resultado["ultima"]} (tienes {resultado["instalada"]}). '
                f'Instálala con `{ORDEN_BREW}`.')
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
