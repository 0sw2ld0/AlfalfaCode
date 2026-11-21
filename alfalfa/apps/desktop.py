"""Adapta la CLI de AlfalfaDesktop incluida al formato de Control de apps."""

import os
import re
import shutil
import subprocess
import sys
from functools import lru_cache
from pathlib import Path
from tempfile import TemporaryDirectory

NOMBRE = 'alfalfa-desktop'
_ACCIONES = {
    'clic': 'click', 'doble': 'double-click', 'derecho': 'right-click', 'escribir': 'type',
    'tecla': 'key', 'desplazar': 'scroll', 'valor': 'value', 'arrastrar': 'drag',
    'activar': 'focus', 'pasar': 'hover', 'menu': 'menu',
}
_ARGUMENTOS = {'ventana': 'window', 'ruta': 'path', 'rol': 'role', 'texto': 'text', 'tecla': 'key',
               'raton': 'system-mouse', 'sin-cursor': 'hide-pointer'}
_ESTADO = {'window': 'ventana', 'title': 'titulo', 'width': 'ancho', 'height': 'alto',
           'tree': 'arbol', 'truncated': 'recortado', 'captureError': 'captura_error',
           'screenRecordingRequired': 'captura_pide_permiso'}
_ELEMENTO = {'index': 'i', 'path': 'ruta', 'role': 'rol', 'title': 'titulo', 'description': 'descripcion',
             'value': 'valor', 'help': 'ayuda', 'id': 'id', 'actions': 'acciones', 'x': 'x', 'y': 'y',
             'width': 'ancho', 'height': 'alto', 'subrole': 'subrol', 'disabled': 'desactivado', 'focused': 'foco'}
_METODOS = {'background': 'fondo', 'backgroundKeyboard': 'teclado_en_fondo', 'keyboard': 'teclado',
            'system-mouse': 'raton', 'focus': 'activar'}


def compatible(orden, opciones):
    # El inventario, las pulsaciones AX explícitas (incluidas las personalizadas) y la ruta vacía
    # conservan la semántica del ayudante anterior; el resto usa la CLI publicada.
    return orden == 'estado' or (orden == 'accion' and opciones.get('tipo') != 'pulsar' and opciones.get('ruta') != '')


async def llamar(ejecutar, ruta, orden, opciones):
    if orden == 'accion':
        argumentos = {_ARGUMENTOS.get(k, k): v for k, v in opciones.items() if k != 'tipo'}
        accion = await ejecutar(ruta, 'action', {**argumentos, 'kind': _ACCIONES[opciones['tipo']], 'approve': True})
        metodo = accion.get('method')
        return {'hecho': accion.get('performed'), 'como': _METODOS.get(metodo, metodo)}
    destino = opciones.get('captura')
    argumentos = {'pid': opciones['pid'], 'window': opciones.get('ventana'), 'max': opciones.get('max'),
                  'approve': True, 'capture': destino is not None}
    if destino is None:
        return _estado(await ejecutar(ruta, 'inspect', argumentos))
    # La CLI elige su propia ruta de captura. El ejecutable firmado puede usar el
    # temporal del usuario de macOS en vez del TMPDIR de esta invocación.
    with TemporaryDirectory(prefix='.alfalfa-desktop-', dir=destino.parent) as carpeta:
        temporal = Path(carpeta)
        datos = await ejecutar(ruta, 'inspect', argumentos, env={**os.environ, 'TMPDIR': carpeta})
        estado = _estado(datos)
        if origen := datos.get('capture'):
            _guardar_captura(Path(origen), destino, temporal, estado)
        return estado


@lru_cache(maxsize=1)
def _temporal_macos():
    if sys.platform == 'darwin':
        try:
            carpeta = subprocess.check_output(
                ['/usr/bin/getconf', 'DARWIN_USER_TEMP_DIR'], text=True, timeout=2, stderr=subprocess.DEVNULL
            ).strip()
            if carpeta:
                return Path(carpeta).resolve()
        except (OSError, subprocess.SubprocessError):
            pass
    return None


def _guardar_captura(png, destino, temporal, estado):
    carpeta = png.parent
    local = carpeta.parent.resolve() == temporal.resolve()
    if (png.name != 'window.png' or png.is_symlink() or carpeta.is_symlink() or not png.is_file()
            or not carpeta.name.startswith('alfalfa-desktop-')):
        estado['captura_error'] = 'AlfalfaDesktop devolvió una ruta de captura inválida.'
        return
    if not local:
        # Para el temporal de macOS se acepta sólo el directorio privado con UUID de la CLI.
        patron_uuid = r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}'
        info = carpeta.stat()
        if (carpeta.parent.resolve() != _temporal_macos()
                or not re.fullmatch('alfalfa-desktop-' + patron_uuid, carpeta.name, re.IGNORECASE)
                or info.st_uid != os.getuid() or info.st_mode & 0o077 or png.stat().st_uid != os.getuid()):
            estado['captura_error'] = 'AlfalfaDesktop devolvió una ruta de captura inválida.'
            return
    try:
        if local:
            png.replace(destino)
        else:
            copia = temporal / 'window.png'
            shutil.copyfile(png, copia)
            copia.replace(destino)
    except OSError:
        estado['captura_error'] = 'No se pudo guardar la captura de AlfalfaDesktop.'
    else:
        estado['captura'] = str(destino)
    finally:
        if not local:
            try:
                png.unlink(missing_ok=True)
                carpeta.rmdir()
            except OSError:
                pass


def _estado(datos):
    resultado = {destino: datos[origen] for origen, destino in _ESTADO.items() if origen in datos}
    resultado['elementos'] = [
        {destino: fila[origen] for origen, destino in _ELEMENTO.items() if origen in fila}
        for fila in datos.get('elements', [])
    ]
    if resultado.get('captura_pide_permiso') is True:
        resultado['captura_error'] = ('Falta el permiso de Grabación de pantalla '
                                     '(Ajustes del Sistema → Privacidad y seguridad).')
    return resultado
