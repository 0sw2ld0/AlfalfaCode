"""Guardas que funcionaban pero que ninguna prueba fijaba (M26 de la auditoría).

Quitar cualquiera de ellas no rompía la suite. Cada prueba de aquí falla si se quita la suya:
- la redacción tapa el `value` de un par `{"name": "X-Api-Key", "value": …}` dentro de un JSON;
- las claves guardadas de los proveedores y de los servidores MCP salen tapadas de una búsqueda
  aunque no tengan forma de clave (las pruebas de búsqueda sustituían siempre `secretos_conocidos`);
- `--auto` y Manual no leen sin preguntar a través de un enlace simbólico, aunque apunte dentro;
- reanudar una sesión desde otra carpeta, con un task.json de otra sesión o desde una carpeta
  enlazada se rechaza y la sesión queda como estaba.
"""
import json
import os

import pytest

from alfalfa.busqueda_sesiones import redaccion
from alfalfa.permisos.terminal import solo_lectura
from alfalfa.proveedores import llavero
from alfalfa.terminal import TerminalManager

# ---------------------------------------------------------------------------- redacción


def test_el_valor_de_un_par_nombre_valor_sensible_se_tapa():
    salida = json.dumps({'headers': [{'name': 'X-Api-Key', 'value': 'Hx7pQ2vLz4'},
                                     {'name': 'Accept', 'value': 'application/json'}]})
    tapado = redaccion.tapar(redaccion.texto_de_salida(salida))
    assert 'Hx7pQ2vLz4' not in tapado and redaccion.REDACTADO in tapado
    assert 'application/json' in tapado  # lo que no es sensible sigue


def test_las_claves_guardadas_salen_tapadas_aunque_no_parezcan_claves(tmp_path, monkeypatch):
    monkeypatch.setenv('ALFALFA_HOME', str(tmp_path / 'alfalfa'))
    (tmp_path / 'alfalfa').mkdir()
    assert llavero._almacen is not None  # el de la suite, en memoria: nunca el Llavero de verdad
    llavero.guardar('openai', 'clave-sin-forma-de-clave-0123')
    (tmp_path / 'alfalfa' / 'mcp.json').write_text(json.dumps({'servidores': {'remoto': {
        'url': 'https://mcp.example.com/mcp', 'cabeceras': {'X-Cliente': 'cabecera-secreta-sin-forma-987'}}}}))
    conocidos = redaccion.secretos_conocidos()
    assert 'clave-sin-forma-de-clave-0123' in conocidos and 'cabecera-secreta-sin-forma-987' in conocidos
    texto = 'la orden imprimió clave-sin-forma-de-clave-0123 y cabecera-secreta-sin-forma-987'
    assert redaccion.tapar(texto) == texto  # sin las conocidas, nada las delataría
    tapado = redaccion.tapar(texto, conocidos)
    assert 'sin-forma' not in tapado and tapado.count(redaccion.REDACTADO) == 2


# ---------------------------------------------------------------------------- lecturas sin preguntar


@pytest.fixture
def carpeta(tmp_path):
    raiz = (tmp_path / 'proyecto').resolve()
    (raiz / 'sub').mkdir(parents=True)
    (raiz / 'a.txt').write_text('a\n')
    (raiz / 'sub' / 'b.txt').write_text('b\n')
    (raiz / 'enlace-dentro').symlink_to(raiz / 'a.txt')
    (raiz / 'sub-enlazada').symlink_to(raiz / 'sub')
    return raiz


@pytest.mark.parametrize('argv', [['cat', 'enlace-dentro'], ['wc', '-l', 'enlace-dentro'],
                                  ['cat', 'sub-enlazada/b.txt'], ['head', 'sub-enlazada/b.txt']])
def test_un_enlace_aunque_apunte_dentro_pregunta(carpeta, argv):
    # Se puede reapuntar entre la comprobación y la ejecución: sólo se lee sin preguntar lo que no es enlace.
    assert solo_lectura(argv, carpeta, None) is False


@pytest.mark.parametrize('argv', [['cat', 'a.txt'], ['cat', 'sub/b.txt']])
def test_lo_mismo_sin_enlace_se_lee(carpeta, argv):
    assert solo_lectura(argv, carpeta, None) is True


# ---------------------------------------------------------------------------- reanudar


@pytest.fixture
def sesion(tmp_path, monkeypatch):
    for nombre in ('proyecto', 'otra'):
        (tmp_path / nombre).mkdir()
    monkeypatch.chdir(tmp_path / 'proyecto')
    hogar = tmp_path / 'hogar'
    m = TerminalManager('Encargo', home=hogar, model='demo:latest')
    m.task['status'] = 'interrupted'
    m.save()
    m.close()
    return hogar, m.task['id'], m.directory


def foto(carpeta):
    return {p.name: p.read_bytes() for p in carpeta.iterdir() if p.is_file() and p.name != 'lock'}


def test_reanudar_desde_otra_carpeta_se_rechaza_y_no_toca_la_sesion(sesion, tmp_path, monkeypatch):
    hogar, ident, carpeta = sesion
    antes = foto(carpeta)
    monkeypatch.chdir(tmp_path / 'otra')
    with pytest.raises(ValueError, match='Reanuda desde la carpeta original'):
        TerminalManager(home=hogar, resume=ident)
    assert foto(carpeta) == antes


def test_un_task_json_de_otra_sesion_se_rechaza(sesion):
    hogar, ident, carpeta = sesion
    task = json.loads((carpeta / 'task.json').read_text())
    task['id'] = 'otra-sesion'
    (carpeta / 'task.json').write_text(json.dumps(task))
    antes = foto(carpeta)
    with pytest.raises(ValueError, match='no coincide con su directorio'):
        TerminalManager(home=hogar, resume=ident)
    assert foto(carpeta) == antes


def test_una_carpeta_de_sesion_enlazada_se_rechaza(sesion):
    hogar, ident, carpeta = sesion
    os.symlink(carpeta, hogar / 'sesiones' / 'enlazada')
    with pytest.raises(ValueError, match='enlace simbólico'):
        TerminalManager(home=hogar, resume='enlazada')


# ---------------------------------------------------------------------------- deshacer al restaurar
# Son la segunda línea: la vista de deshacer ya ve el cambio como conflicto, pero la carpeta puede
# cambiar entre la vista y la restauración (otro proceso, el propio agente en segundo plano).


@pytest.fixture
def almacen(tmp_path, monkeypatch):
    from alfalfa.deshacer.instantaneas import Almacen
    hogar = tmp_path / 'hogar'
    monkeypatch.setenv('ALFALFA_HOME', str(hogar))
    cwd = tmp_path / 'proyecto'
    (cwd / 'src').mkdir(parents=True)
    (cwd / 'src' / 'x.py').write_text('v1\n')
    (cwd / 'a.txt').write_text('a1\n')
    a = Almacen(hogar, cwd)
    antes = a.guardar('s1', 'antes')['commit']
    return a, a.entradas(antes, ['src/x.py', 'a.txt']), tmp_path


def test_no_restaura_a_traves_de_una_carpeta_que_ahora_es_un_enlace(almacen):
    a, destino, tmp = almacen
    fuera = tmp / 'fuera'
    (a.cwd / 'src').rename(fuera)
    (fuera / 'x.py').write_text('v2\n')
    os.symlink(fuera, a.cwd / 'src')
    hechas, fallos = a.restaurar(['src/x.py'], destino)
    assert hechas == [] and [f['ruta'] for f in fallos] == ['src/x.py']
    assert (fuera / 'x.py').read_text() == 'v2\n', 'escribió fuera de la carpeta de trabajo'


def test_no_pisa_un_archivo_que_ahora_es_una_carpeta(almacen):
    a, destino, _ = almacen
    (a.cwd / 'a.txt').unlink()
    (a.cwd / 'a.txt').mkdir()
    (a.cwd / 'a.txt' / 'dentro').write_text('d\n')
    hechas, fallos = a.restaurar(['a.txt'], destino)
    assert hechas == [] and fallos == [{'ruta': 'a.txt', 'error': 'ahora es una carpeta'}]
    assert (a.cwd / 'a.txt' / 'dentro').read_text() == 'd\n'


def test_no_restaura_un_repositorio_anidado(almacen):
    a, destino, _ = almacen
    hechas, fallos = a.restaurar(['vendor/lib'], {'vendor/lib': ('160000', '0' * 40)})
    assert hechas == [] and fallos == [{'ruta': 'vendor/lib', 'error': 'es un repositorio anidado'}]
    assert not (a.cwd / 'vendor').exists()
