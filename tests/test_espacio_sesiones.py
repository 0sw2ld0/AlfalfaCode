"""`--espacio-sesiones` y `--liberar-sesiones`: ver lo que ocupan las sesiones y liberarlo.

- El listado da tamaño, último uso, carpeta y título, y se ordena por tamaño.
- Lo liberado va a la Papelera (una de prueba, con HOME temporal), nunca se borra para siempre.
- Una sesión en uso (con su cerrojo tomado) nunca se toca.
- Sale del índice de búsqueda y de las instantáneas de deshacer, y el espacio se recupera.
"""
import fcntl
import io
import json
import os
import time

import pytest

from alfalfa import busqueda_sesiones as busqueda
from alfalfa import terminal
from alfalfa.deshacer.instantaneas import Almacen
from alfalfa.sesiones import espacio
from tests.test_buscar_sesiones import Demo

DIA = 86400


@pytest.fixture
def demo(tmp_path, monkeypatch):
    monkeypatch.delenv('ALFALFA_BUSCAR_SESIONES', raising=False)
    monkeypatch.setenv('HOME', str(tmp_path))  # la Papelera de prueba: tmp_path/.Trash
    d = Demo(tmp_path, monkeypatch)
    (d.proyecto / 'app.py').write_text('print(1)\n')
    return d


def envejecer(carpeta, dias):
    """Que la sesión parezca sin usar desde hace `dias` días (task.json y todos sus archivos)."""
    cuando = time.time() - dias * DIA
    task = json.loads((carpeta / 'task.json').read_text())
    task['updated_at'] = cuando
    (carpeta / 'task.json').write_text(json.dumps(task))
    for raiz, _, archivos in os.walk(carpeta):
        for nombre in archivos:
            os.utime(os.path.join(raiz, nombre), (cuando, cuando))


def ejecutar(argv, monkeypatch):
    salida = io.StringIO()
    monkeypatch.setattr('sys.stdout', salida)
    codigo = terminal.main(argv)
    return codigo, salida.getvalue()


def test_lista_tamano_uso_carpeta_y_titulo_ordenada_por_tamano(demo):
    grande = demo.crear('grande', [{'role': 'user', 'content': 'hola'}], titulo='# Generar imágenes\nmás texto')
    imagenes = grande / 'native' / 'grande' / 'imagenes'
    imagenes.mkdir()
    (imagenes / 'a.png').write_bytes(os.urandom(300_000))
    chica = demo.crear('chica', [{'role': 'user', 'content': 'hola'}], cwd=demo.otro)
    (chica / 'marcas.json').write_text(json.dumps({'titulo': 'Mi título', 'fijada': True}))
    envejecer(chica, 40)
    filas = espacio.listar(demo.home)
    assert [f['id'] for f in filas] == ['grande', 'chica']
    g, c = filas
    assert g['titulo'] == 'Generar imágenes' and g['cwd'] == str(demo.proyecto)
    assert g['bytes'] >= 300_000 and g['imagenes'] >= 300_000
    assert g['dias_sin_usar'] == 0 and g['en_uso'] is False
    assert c['titulo'] == 'Mi título' and c['fijada'] is True and c['cwd'] == str(demo.otro)
    assert c['dias_sin_usar'] == 40
    assert [f['id'] for f in espacio.listar(demo.home, 'uso')] == ['chica', 'grande']


def test_cambiar_el_titulo_no_cuenta_como_usarla(demo):
    s = demo.crear('s', [{'role': 'user', 'content': 'hola'}])
    envejecer(s, 40)
    (s / 'marcas.json').write_text(json.dumps({'titulo': 'Renombrada hoy'}))
    assert espacio.listar(demo.home)[0]['dias_sin_usar'] == 40


def test_libera_las_viejas_a_la_papelera_sin_tocar_la_en_uso_ni_la_fijada(demo, monkeypatch):
    for ident in ('vieja', 'en_uso', 'fijada', 'nueva'):
        demo.crear(ident, [{'role': 'user', 'content': 'el tapir de ' + ident}])
    for ident in ('vieja', 'en_uso', 'fijada'):
        envejecer(demo.home / 'sesiones' / ident, 40)
    (demo.home / 'sesiones' / 'fijada' / 'marcas.json').write_text(json.dumps({'fijada': True}))
    assert len(demo.buscar('tapir')['resultados']) == 4  # crea el índice
    fd = os.open(demo.home / 'sesiones' / 'en_uso' / 'lock', os.O_CREAT | os.O_RDWR)
    fcntl.flock(fd, fcntl.LOCK_EX)  # el motor de esa sesión, o la app con ella abierta entre turnos
    try:
        codigo, salida = ejecutar(['--liberar-sesiones', '--sin-usar-dias', '30', '--si', '--json'], monkeypatch)
    finally:
        os.close(fd)
    assert codigo == 0
    respuesta = json.loads(salida)
    assert respuesta['tipo'] == 'sesiones_liberadas'
    assert [m['id'] for m in respuesta['movidas']] == ['vieja']
    assert respuesta['saltadas'] == [{'id': 'en_uso', 'motivo': 'en_uso'}]
    assert 'ya no se podrán retomar' in respuesta['nota']
    # A la Papelera, entera; nada borrado para siempre.
    papelera = demo.home.parent / '.Trash' / 'alfalfa-vieja'
    assert (papelera / 'task.json').is_file() and respuesta['movidas'][0]['papelera'] == str(papelera)
    assert not (demo.home / 'sesiones' / 'vieja').exists()
    assert {p.name for p in (demo.home / 'sesiones').iterdir()} == {'en_uso', 'fijada', 'nueva'}
    # Sin entradas huérfanas en el índice, y su espacio devuelto (sin páginas libres en el archivo).
    assert {f[0] for f in demo.filas()} == {'en_uso', 'fijada', 'nueva'}
    with busqueda.Indice(demo.home) as indice:
        assert indice.conexion.execute('PRAGMA freelist_count').fetchone()[0] == 0


def test_con_ids_y_las_fijadas_si_se_nombran(demo, monkeypatch):
    demo.crear('a', [{'role': 'user', 'content': 'x'}])
    demo.crear('b', [{'role': 'user', 'content': 'x'}])
    (demo.home / 'sesiones' / 'a' / 'marcas.json').write_text(json.dumps({'fijada': True}))
    codigo, salida = ejecutar(['--liberar-sesiones', 'a', 'zz', '--si', '--json'], monkeypatch)
    respuesta = json.loads(salida)
    assert codigo == 0 and [m['id'] for m in respuesta['movidas']] == ['a']
    assert respuesta['saltadas'] == [{'id': 'zz', 'motivo': 'no_existe'}]
    assert (demo.home / 'sesiones' / 'b').is_dir()


def test_sin_si_solo_enseña_lo_que_se_moveria(demo, monkeypatch):
    s = demo.crear('vieja', [{'role': 'user', 'content': 'x'}])
    envejecer(s, 40)
    codigo, salida = ejecutar(['--liberar-sesiones', '--sin-usar-dias', '30', '--json'], monkeypatch)
    respuesta = json.loads(salida)
    assert codigo == 0 and respuesta['vista_previa'] is True and [f['id'] for f in respuesta['sesiones']] == ['vieja']
    assert s.is_dir()
    # En la terminal, sin poder preguntar, tampoco mueve nada.
    monkeypatch.setattr('sys.stdin', io.StringIO(''))
    codigo, salida = ejecutar(['--liberar-sesiones', '--sin-usar-dias', '30'], monkeypatch)
    assert codigo == 1 and '--si' in salida and 'ya no se podrán retomar' in salida and s.is_dir()


def test_en_la_terminal_pregunta_y_solo_mueve_con_un_si(demo, monkeypatch):
    s = demo.crear('vieja', [{'role': 'user', 'content': 'x'}])

    class Tecla(io.StringIO):
        def isatty(self):
            return True
    monkeypatch.setattr('sys.stdin', Tecla('n\n'))
    assert ejecutar(['--liberar-sesiones', 'vieja'], monkeypatch)[0] == 1 and s.is_dir()
    monkeypatch.setattr('sys.stdin', Tecla('s\n'))
    codigo, salida = ejecutar(['--liberar-sesiones', 'vieja'], monkeypatch)
    assert codigo == 0 and 'a la Papelera' in salida and not s.exists()


def test_se_borran_sus_instantaneas_y_no_las_de_otra_sesion_de_la_carpeta(demo, monkeypatch):
    demo.crear('s1', [{'role': 'user', 'content': 'x'}])
    demo.crear('s2', [{'role': 'user', 'content': 'x'}])
    almacen = Almacen(demo.home, demo.proyecto)
    almacen.guardar('s1', 'antes')
    (demo.proyecto / 'grande.txt').write_bytes(os.urandom(400_000))  # sólo lo ve la instantánea de s1
    almacen.guardar('s1', 'despues')
    (demo.proyecto / 'grande.txt').unlink()
    almacen.guardar('s2', 'antes')
    peso = almacen.tamano()
    codigo, salida = ejecutar(['--liberar-sesiones', 's1', '--si', '--json'], monkeypatch)
    respuesta = json.loads(salida)
    assert codigo == 0 and respuesta['instantaneas']['refs'] == 2
    refs = almacen.git('for-each-ref', '--format=%(refname)', 'refs/alfalfa/').decode().split()
    assert refs == ['refs/alfalfa/s2/antes']
    assert almacen.tamano() < peso - 300_000, 'los objetos que sólo usaba s1 no se fueron'
    assert not (almacen.dir / 'indices' / 's1').exists()


def test_eliminar_una_desde_la_barra_lateral_tambien_libera_sus_instantaneas(demo, monkeypatch):
    demo.crear('s1', [{'role': 'user', 'content': 'x'}])
    almacen = Almacen(demo.home, demo.proyecto)
    almacen.guardar('s1', 'antes')
    codigo, salida = ejecutar(['--eliminar-sesion', 's1'], monkeypatch)
    assert codigo == 0 and json.loads(salida)['eliminada'] is True
    assert almacen.git('for-each-ref', 'refs/alfalfa/').decode() == ''
    assert (demo.home.parent / '.Trash' / 'alfalfa-s1' / 'task.json').is_file()


def test_listado_en_json_con_los_totales(demo, monkeypatch):
    demo.crear('s1', [{'role': 'user', 'content': 'el tapir'}])
    demo.buscar('tapir')
    codigo, salida = ejecutar(['--espacio-sesiones', '--json'], monkeypatch)
    respuesta = json.loads(salida)
    assert codigo == 0 and respuesta['tipo'] == 'espacio_sesiones'
    assert respuesta['sesiones'][0]['id'] == 's1' and respuesta['totales']['indice'] > 0
    assert respuesta['totales']['sesiones'] == respuesta['sesiones'][0]['bytes']


def test_opciones_sueltas_dan_error(demo, monkeypatch):
    with pytest.raises(SystemExit):
        terminal.main(['--si'])
    codigo, salida = ejecutar(['--liberar-sesiones', '--json'], monkeypatch)
    assert codigo == 1 and json.loads(salida)['tipo'] == 'error'
