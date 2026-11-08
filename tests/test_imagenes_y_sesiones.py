"""Imágenes adjuntas como en Codex y órdenes de la app sobre sesiones guardadas."""
import base64
import io
import json
import subprocess
from pathlib import Path

import pytest

from alfalfa import imagenes
from alfalfa.proveedores.nativo.runtime import NativeRuntime
from alfalfa.stdio import Transport, sessions
from alfalfa.terminal import TerminalManager, main

# PNG de 1×1.
PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5E'
                       'rkJggg==')


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


def test_reduce_las_grandes_y_no_amplia_las_pequenas(tmp_path, foto):
    grande = tmp_path / 'grande.png'
    subprocess.run(['sips', '-z', '3000', '4000', str(foto), '--out', str(grande)], check=True, capture_output=True)
    copia = imagenes.preparar(grande, tmp_path / 'copias')
    assert max(imagenes._dimensiones(copia)) == 2048
    pequeña = imagenes.preparar(foto, tmp_path / 'copias')
    assert pequeña.read_bytes() == PNG


def test_el_mensaje_lleva_la_imagen_en_base64_a_ollama(m, foto):
    rt = NativeRuntime(m, m.task)
    rt.user_message(imagenes.con_etiquetas('Mira', [str(foto)]))
    guardado = rt.state['messages'][-1]
    assert Path(guardado['imagenes'][0]).parent == rt.directory / 'imagenes'
    assert 'images' not in guardado  # en disco sólo la ruta, no los bytes
    enviado = NativeRuntime._con_imagenes(rt.state['messages'], {'capabilities': ['vision']})[-1]
    assert enviado['images'] == [base64.b64encode(PNG).decode()] and 'imagenes' not in enviado
    with pytest.raises(ValueError, match='no admite imágenes'):
        NativeRuntime._con_imagenes(rt.state['messages'], {'capabilities': ['tools']})


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
