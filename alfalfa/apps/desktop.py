"""Adapta la CLI de AlfalfaDesktop incluida al formato de Control de apps."""

import os
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
    # La CLI elige su propia ruta de captura. Dale un temporal privado junto al destino para
    # mover sólo el PNG de esta invocación y poder limpiar también los intentos fallidos.
    with TemporaryDirectory(prefix='.alfalfa-desktop-', dir=destino.parent) as carpeta:
        datos = await ejecutar(ruta, 'inspect', argumentos, env={**os.environ, 'TMPDIR': carpeta})
        estado = _estado(datos)
        if datos.get('capture'):
            png = Path(datos['capture'])
            if (png.parent.parent.resolve() != Path(carpeta).resolve()
                    or not png.parent.name.startswith('alfalfa-desktop-') or png.name != 'window.png'
                    or png.parent.is_symlink() or png.is_symlink() or not png.is_file()):
                estado['captura_error'] = 'AlfalfaDesktop devolvió una ruta de captura inválida.'
            else:
                try:
                    png.replace(destino)
                except OSError:
                    estado['captura_error'] = 'No se pudo guardar la captura de AlfalfaDesktop.'
                else:
                    estado['captura'] = str(destino)
        return estado


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
