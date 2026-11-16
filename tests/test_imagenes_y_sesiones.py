"""Imágenes adjuntas como en Codex y órdenes de la app sobre sesiones guardadas."""
import asyncio
import base64
import importlib.util
import io
import json
import shutil
import struct
import sys
import zlib
from pathlib import Path

import pytest

from alfalfa import aisladas, imagenes
from alfalfa.proveedores.nativo.runtime import NativeRuntime
from alfalfa.seatbelt import SandboxUnavailable
from alfalfa.stdio import Transport, sessions
from alfalfa.terminal import TerminalManager, main
from tests.sandbox_del_sistema import motivo_sin_sandbox

# PNG de 1×1.
PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5E'
                       'rkJggg==')


def _sin_pillow_aislado():
    """None si aquí Pillow mide y reduce, aislado en bwrap: Linux con Pillow y con el sandbox en pie."""
    if not sys.platform.startswith('linux'):
        return 'Pillow sólo se usa en Linux; en macOS lo hace sips'
    if importlib.util.find_spec('PIL') is None:
        return 'sin Pillow: pip install pillow'
    return motivo_sin_sandbox()


SIN_PILLOW = _sin_pillow_aislado()
CON_PILLOW = pytest.mark.skipif(SIN_PILLOW is not None, reason=str(SIN_PILLOW))
# Mide, reduce y convierte sips en macOS y Pillow en Linux.
SIN_LECTOR = None if shutil.which('sips') else SIN_PILLOW
CON_LECTOR = pytest.mark.skipif(SIN_LECTOR is not None, reason=str(SIN_LECTOR))


def _trozo(tipo, datos):
    return struct.pack('>I', len(datos)) + tipo + datos + struct.pack('>I', zlib.crc32(tipo + datos))


def _png(ruta, ancho, alto):
    """Un PNG gris de ancho × alto hecho a mano, sin sips ni Pillow."""
    filas = (b'\x00' + b'\x80' * ancho) * alto
    ruta.write_bytes(b'\x89PNG\r\n\x1a\n' + _trozo(b'IHDR', struct.pack('>IIBBBBB', ancho, alto, 8, 0, 0, 0, 0))
                     + _trozo(b'IDAT', zlib.compress(filas, 1)) + _trozo(b'IEND', b''))
    return ruta


def _medidas_png(ruta):
    datos = Path(ruta).read_bytes()
    assert datos[:8] == b'\x89PNG\r\n\x1a\n' and datos[12:16] == b'IHDR'
    return struct.unpack('>II', datos[16:24])


def _bmp(ruta, ancho, alto):
    """Un BMP de 24 bits hecho a mano: Ollama no lo lee y se convierte a PNG."""
    fila = b'\x20\x40\x80' * ancho
    fila += b'\x00' * (-len(fila) % 4)
    pixeles = fila * alto
    cabecera = struct.pack('<IiiHHIIiiII', 40, ancho, alto, 1, 24, 0, len(pixeles), 2835, 2835, 0, 0)
    ruta.write_bytes(b'BM' + struct.pack('<IHHI', 54 + len(pixeles), 0, 0, 54) + cabecera + pixeles)
    return ruta


@pytest.fixture
def foto(tmp_path):
    path = tmp_path / 'captura.png'
    path.write_bytes(PNG)
    return path


@pytest.fixture
def m(tmp_path, monkeypatch):
    (tmp_path / 'project').mkdir()
    monkeypatch.chdir(tmp_path / 'project')
    manager = TerminalManager('Encargo.', home=tmp_path / 'alfalfa')
    yield manager
    manager.close()


def test_las_etiquetas_son_las_de_codex(foto):
    texto = imagenes.con_etiquetas('¿Qué falla?', [str(foto)])
    assert texto == f'<image name=[Image #1] path="{foto.resolve()}"></image>\n¿Qué falla?'
    assert imagenes.rutas_etiquetadas(texto) == [str(foto.resolve())]


@pytest.mark.parametrize('nombre, error', [('nota.txt', 'no es una imagen'), ('falta.png', 'No existe')])
def test_rechaza_lo_que_no_es_una_imagen(tmp_path, nombre, error):
    path = tmp_path / nombre
    if nombre.endswith('.txt'):
        path.write_text('x')
    with pytest.raises(imagenes.ImagenInvalida, match=error):
        imagenes.con_etiquetas('x', [str(path)])
    with pytest.raises(imagenes.ImagenInvalida, match='absoluta'):
        imagenes.validar('relativa.png')


@CON_LECTOR
def test_reduce_las_grandes_y_no_amplia_las_pequenas(tmp_path, foto):
    grande = _png(tmp_path / 'grande.png', 3000, 4000)
    copia = imagenes.preparar(grande, tmp_path / 'copias')
    assert _medidas_png(copia) == (1536, 2048)
    pequeña = imagenes.preparar(foto, tmp_path / 'copias')
    assert pequeña.read_bytes() == PNG


@CON_LECTOR
def test_convierte_a_png_lo_que_ollama_no_lee(tmp_path):
    copia = imagenes.preparar(_bmp(tmp_path / 'esquema.bmp', 40, 30), tmp_path / 'copias')
    assert copia.suffix == '.png' and _medidas_png(copia) == (40, 30)


@CON_PILLOW
def test_pillow_no_descomprime_una_bomba_y_la_manda_tal_cual(tmp_path, monkeypatch):
    monkeypatch.setattr(imagenes, 'MAX_PIXELES', 1_000_000)
    grande = _png(tmp_path / 'grande.png', 3000, 4000)
    assert imagenes.preparar(grande, tmp_path / 'copias').read_bytes() == grande.read_bytes()
    with pytest.raises(imagenes.ImagenInvalida, match='No se pudo convertir esquema.bmp a PNG: Pillow no pudo'):
        imagenes.preparar(_bmp(tmp_path / 'esquema.bmp', 2000, 1000), tmp_path / 'copias')


@CON_PILLOW
def test_pillow_gira_la_foto_segun_su_exif(tmp_path):
    from PIL import Image
    foto = tmp_path / 'movil.jpg'
    exif = Image.Exif()
    exif[0x0112] = 6  # girada 90°: se ve vertical
    Image.new('RGB', (3000, 1000), 'white').save(foto, exif=exif)
    with Image.open(imagenes.preparar(foto, tmp_path / 'copias')) as copia:
        assert copia.format == 'JPEG' and copia.size == (683, 2048)


@pytest.fixture
def linux_sin_sips(monkeypatch):
    """El camino de Linux aunque sea macOS: sin sips, con Pillow, y aisladas.correr con la respuesta dada."""
    monkeypatch.setattr(imagenes.shutil, 'which', lambda nombre: None)
    monkeypatch.setattr(imagenes.sys, 'platform', 'linux')
    monkeypatch.setattr(imagenes, '_hay_pillow', lambda: True)
    llamadas, respuesta = [], {}

    def correr(argv, *, tope, plazo):
        llamadas.append(argv)
        if isinstance(respuesta['valor'], Exception):
            raise respuesta['valor']
        return respuesta['valor']

    monkeypatch.setattr(aisladas, 'correr', correr)
    return llamadas, respuesta


def test_en_linux_pillow_corre_aislado_y_solo_cambia_lo_que_hace_falta(tmp_path, foto, linux_sin_sips):
    llamadas, respuesta = linux_sin_sips
    respuesta['valor'] = (0, b'', '')
    assert imagenes.preparar(foto, tmp_path / 'copias').read_bytes() == PNG  # nada por stdout: tal cual
    argv = llamadas[0]
    assert argv[:3] == [sys.executable, '-I', '-c'] and argv[3] == imagenes._PILLOW
    assert argv[4:] == [str(foto.resolve()), 'PNG', '2048', str(imagenes.MAX_PIXELES)]
    respuesta['valor'] = (0, b'\x89PNG reducida', '')
    copia = imagenes.preparar(_bmp(tmp_path / 'esquema.bmp', 4, 4), tmp_path / 'copias')
    assert copia.suffix == '.png' and copia.read_bytes() == b'\x89PNG reducida'
    assert copia.stat().st_mode & 0o777 == 0o600 and not list((tmp_path / 'copias').glob('*.tmp'))


def test_en_linux_sin_sandbox_o_sin_pillow_va_tal_cual_o_se_dice(tmp_path, foto, linux_sin_sips, monkeypatch):
    llamadas, respuesta = linux_sin_sips
    respuesta['valor'] = SandboxUnavailable('bwrap: setting up uid map: Permission denied')
    assert imagenes.preparar(foto, tmp_path / 'copias').read_bytes() == PNG
    with pytest.raises(imagenes.ImagenInvalida, match='sandbox.*uid map'):
        imagenes.preparar(_bmp(tmp_path / 'esquema.bmp', 4, 4), tmp_path / 'copias')
    monkeypatch.setattr(imagenes, '_hay_pillow', lambda: False)
    with pytest.raises(imagenes.ImagenInvalida, match='falta Pillow'):
        imagenes.preparar(_bmp(tmp_path / 'otro.bmp', 5, 5), tmp_path / 'copias')
    assert len(llamadas) == 2


def test_el_mensaje_lleva_la_imagen_en_base64_a_ollama(m, foto, monkeypatch):
    from alfalfa import proveedores
    monkeypatch.setattr(proveedores, 'con_vision', lambda modelo=None, limite=3: [])
    rt = NativeRuntime(m, m.task)
    rt.user_message(imagenes.con_etiquetas('Mira', [str(foto)]))
    guardado = rt.state['messages'][-1]
    assert Path(guardado['imagenes'][0]).parent == rt.directory / 'imagenes'
    assert 'images' not in guardado  # en disco sólo la ruta, no los bytes
    enviado = asyncio.run(rt._con_imagenes(rt.state['messages'], {'capabilities': ['vision']}))[-1]
    assert enviado['images'] == [base64.b64encode(PNG).decode()] and 'imagenes' not in enviado
    with pytest.raises(ValueError, match='no ve imágenes'):
        asyncio.run(rt._con_imagenes(rt.state['messages'], {'capabilities': ['tools']}))


def test_una_imagen_que_falta_se_explica_sin_cortar_el_turno(m, tmp_path):
    rt = NativeRuntime(m, m.task)
    rt.user_message(f'<image name=[Image #1] path="{tmp_path}/no.png"></image>\nhola')
    assert 'No se pudo adjuntar' in rt.state['messages'][-1]['content']
    assert 'imagenes' not in rt.state['messages'][-1]


def test_stdio_acepta_imagenes_y_las_valida(foto):
    out = io.StringIO()
    t = Transport(out)
    t.receive(json.dumps({'tipo': 'mensaje', 'texto': '', 'imagenes': [str(foto)]}))
    assert t.inbox == [imagenes.con_etiquetas('Mira la imagen adjunta.', [str(foto)])]
    t.receive(json.dumps({'tipo': 'mensaje', 'texto': 'x', 'imagenes': ['/no/existe.png']}))
    assert 'No existe' in json.loads(out.getvalue().splitlines()[-1])['texto']


def _sesion(home, ident, objective='Arreglar'):
    d = home / 'sesiones' / ident
    d.mkdir(parents=True)
    (d / 'task.json').write_text(json.dumps(dict(id=ident, objective=objective, cwd='/repo', status='completed',
                                                 model='m', effort='low', updated_at=1)))
    return d


def test_marcar_renombra_fija_y_archiva(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv('ALFALFA_HOME', str(tmp_path))
    _sesion(tmp_path, 'abc', objective='<image name=[Image #1] path="/x.png"></image>\nArreglar')
    assert main(['--marcar', 'abc', 'titulo', 'Mi  título']) == 0
    assert main(['--marcar', 'abc', 'fijada', 'sí']) == 0
    assert main(['--marcar', 'abc', 'archivada', 'si']) == 0
    assert main(['--marcar', 'abc', 'archivada', 'no']) == 0
    salida = [json.loads(linea) for linea in capsys.readouterr().out.splitlines()]
    assert salida[-1]['tipo'] == 'sesion_cambiada'
    fila = sessions()[0]
    assert (fila['encargo'], fila['titulo'], fila['fijada'], fila.get('archivada')) \
        == ('Arreglar', 'Mi título', True, None)
    assert main(['--marcar', 'abc', 'color', 'rojo']) == 1
    assert main(['--marcar', '../abc', 'titulo', 'x']) == 1


def test_eliminar_la_mueve_a_la_papelera(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv('ALFALFA_HOME', str(tmp_path / 'home'))
    monkeypatch.setenv('HOME', str(tmp_path))
    _sesion(tmp_path / 'home', 'abc')
    assert main(['--eliminar-sesion', 'abc']) == 0
    assert (tmp_path / '.Trash' / 'alfalfa-abc' / 'task.json').is_file()
    assert sessions() == []


def test_no_elimina_una_sesion_en_ejecucion(tmp_path, monkeypatch, capsys):
    import fcntl
    import os
    monkeypatch.setenv('ALFALFA_HOME', str(tmp_path))
    d = _sesion(tmp_path, 'abc')
    fd = os.open(d / 'lock', os.O_CREAT | os.O_RDWR)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        assert main(['--eliminar-sesion', 'abc']) == 1
        assert 'en ejecución' in json.loads(capsys.readouterr().out)['texto']
    finally:
        os.close(fd)
    assert d.is_dir()
