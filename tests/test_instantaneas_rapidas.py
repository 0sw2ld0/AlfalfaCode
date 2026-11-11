"""Instantáneas rápidas: objetos prestados del proyecto, índice sembrado de HEAD, en paralelo al modelo."""
import asyncio
import json
import os
import subprocess
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from alfalfa import deshacer
from alfalfa.deshacer import instantaneas
from alfalfa.deshacer.instantaneas import Almacen, TiempoAgotado
from tests.test_deshacer import Sesion, escribir

GIT = {'GIT_AUTHOR_NAME': 'demo', 'GIT_AUTHOR_EMAIL': 'demo@localhost', 'GIT_COMMITTER_NAME': 'demo',
       'GIT_COMMITTER_EMAIL': 'demo@localhost', 'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_NOSYSTEM': '1'}


def git(cwd, *args):
    return subprocess.run(['git', *args], cwd=cwd, check=True, capture_output=True,
                          env={**os.environ, **GIT}).stdout.decode()


def sueltos(objetos):
    """Cuántos objetos sueltos hay en un almacén de objetos."""
    return sum(1 for carpeta in Path(objetos).iterdir() if len(carpeta.name) == 2 for _ in carpeta.iterdir())


@pytest.fixture
def proyecto(tmp_path, monkeypatch):
    """Un repositorio con un commit (muchos archivos) y un hogar de AlfalfaCode aparte."""
    hogar = tmp_path / 'hogar'
    cwd = tmp_path / 'trabajo' / 'proyecto'
    (cwd / 'src').mkdir(parents=True)
    for i in range(40):
        (cwd / 'src' / f'm{i}.py').write_text(f'valor = {i}\n' * 20)
    (cwd / 'notas.md').write_text('notas\n')
    git(cwd, 'init', '-q')
    git(cwd, 'add', '-A')
    git(cwd, 'commit', '-q', '-m', 'inicio')
    monkeypatch.setenv('ALFALFA_HOME', str(hogar))
    return hogar, cwd


def huella_del_git(cwd):
    """Lo que no debe cambiar en el .git del proyecto: índice, refs y objetos."""
    raiz = cwd / '.git'
    return ((raiz / 'index').read_bytes(), git(cwd, 'for-each-ref'), sueltos(raiz / 'objects'),
            sorted(p.name for p in (raiz / 'objects' / 'pack').iterdir()))


def test_toma_prestados_los_objetos_y_solo_escribe_lo_cambiado(proyecto):
    hogar, cwd = proyecto
    (cwd / 'src' / 'm3.py').write_text('cambiado\n')
    (cwd / 'nuevo.py').write_text('nuevo\n')
    (cwd / 'notas.md').unlink()
    antes = huella_del_git(cwd)
    almacen = Almacen(hogar, cwd)
    fila = almacen.guardar('s1', 't1-antes')
    assert (almacen.dir / 'objects' / 'info' / 'alternates').read_text().strip() == str(
        (cwd / '.git' / 'objects').resolve())
    # Los 40 archivos de HEAD no se copian: sólo los dos blobs nuevos, sus árboles y el commit.
    assert sueltos(almacen.dir / 'objects') <= 6
    arbol = almacen.arbol(fila['commit'])
    assert set(arbol) == {f'src/m{i}.py' for i in range(40)} | {'nuevo.py'}
    assert almacen.contenido(arbol['src/m3.py'][1]) == b'cambiado\n'
    assert almacen.contenido(arbol['src/m4.py'][1]) == b'valor = 4\n' * 20
    assert huella_del_git(cwd) == antes, 'el .git del proyecto no se toca'


def test_desde_una_subcarpeta_y_desde_un_worktree(proyecto, tmp_path):
    hogar, cwd = proyecto
    sub = Almacen(hogar, cwd / 'src')
    arbol = sub.arbol(sub.guardar('s1', 'uno')['commit'])
    assert set(arbol) == {f'm{i}.py' for i in range(40)}
    otro = tmp_path / 'otro-worktree'
    git(cwd, 'worktree', 'add', '-q', str(otro))
    (otro / 'src' / 'm1.py').write_text('en el worktree\n')
    almacen = Almacen(hogar, otro)
    arbol = almacen.arbol(almacen.guardar('s1', 'uno')['commit'])
    # El worktree comparte objetos con el repositorio principal (`commondir`).
    assert (almacen.dir / 'objects' / 'info' / 'alternates').read_text().strip() == str(
        (cwd / '.git' / 'objects').resolve())
    assert almacen.contenido(arbol['src/m1.py'][1]) == b'en el worktree\n'
    assert sueltos(almacen.dir / 'objects') <= 5


def test_lo_grande_rastreado_no_se_lee_ni_queda_con_su_version_vieja(proyecto, monkeypatch):
    hogar, cwd = proyecto
    monkeypatch.setattr(instantaneas, 'TOPE_ARCHIVO', 1000)
    (cwd / 'grande.bin').write_bytes(b'x' * 5000)
    (cwd / 'crece.txt').write_text('poco\n')
    git(cwd, 'add', '-A')
    git(cwd, 'commit', '-q', '-m', 'grandes')
    almacen = Almacen(hogar, cwd)
    primera = almacen.guardar('s1', 'uno')
    assert 'grande.bin' in primera['grandes']
    assert 'grande.bin' not in almacen.arbol(primera['commit'])
    assert 'crece.txt' in almacen.arbol(primera['commit'])
    (cwd / 'crece.txt').write_text('mucho\n' * 1000)
    segunda = almacen.guardar('s1', 'dos')
    # Ni la versión pequeña de antes: la instantánea no puede decir que sigue así.
    assert 'crece.txt' not in almacen.arbol(segunda['commit'])
    assert 'crece.txt' in almacen.excluidos(segunda['commit'])['grandes']


def test_una_carpeta_sin_git_sigue_igual(tmp_path, monkeypatch):
    hogar, cwd = tmp_path / 'hogar', tmp_path / 'trabajo' / 'suelta'
    cwd.mkdir(parents=True)
    (cwd / 'a.txt').write_text('a\n')
    monkeypatch.setenv('ALFALFA_HOME', str(hogar))
    almacen = Almacen(hogar, cwd)
    fila = almacen.guardar('s1', 'uno')
    assert not (almacen.dir / 'objects' / 'info' / 'alternates').exists()
    assert set(almacen.arbol(fila['commit'])) == {'a.txt'}


def test_deshacer_con_objetos_prestados_y_si_el_proyecto_los_pierde(proyecto):
    hogar, cwd = proyecto
    sesion = Sesion(hogar, cwd)
    t = sesion.turno('uno', lambda c, e: (escribir(e, c, 'src/m1.py', 'nuevo 1\n'),
                                          escribir(e, c, 'src/m2.py', 'nuevo 2\n')))
    vista = deshacer.vista_deshacer(hogar, sesion.task, sesion.estado, t)
    assert {f['ruta'] for f in vista['restaurables']} == {'src/m1.py', 'src/m2.py'}
    # El proyecto reescribe su historia y un gc borra el blob de m2 que la instantánea tomaba prestado.
    blob = git(cwd, 'rev-parse', 'HEAD:src/m2.py').strip()
    (cwd / '.git' / 'objects' / blob[:2] / blob[2:]).unlink()
    vista = deshacer.vista_deshacer(hogar, sesion.task, sesion.estado, t)
    assert vista['disponible']
    assert {f['ruta'] for f in vista['restaurables']} == {'src/m1.py'}
    assert {'ruta': 'src/m2.py', 'motivo': 'perdido'} in vista['no_cubiertos']
    deshacer.deshacer(hogar, sesion.task, sesion.estado, t, vista=vista)
    assert (cwd / 'src' / 'm1.py').read_text() == 'valor = 1\n' * 20
    assert (cwd / 'src' / 'm2.py').read_text() == 'nuevo 2\n', 'sin su blob no se toca'


def test_si_se_agota_el_tiempo_no_se_reintenta_cada_turno_y_avisa_una_vez(proyecto, monkeypatch):
    hogar, cwd = proyecto
    llamadas = []

    def lenta(self, sesion, nombre, mensaje=''):
        llamadas.append(nombre)
        raise TiempoAgotado('La instantánea tardó más de 90 s.')

    monkeypatch.setattr(Almacen, 'guardar', lenta)
    task = {'id': 's1', 'cwd': str(cwd)}
    primera = deshacer.tomar(hogar, cwd, 's1', 't1', 'antes')
    assert primera.get('tiempo') and 'gitignore' in primera['error']
    texto = deshacer.aviso(task, primera)
    assert texto.startswith('Deshacer no está disponible en esta carpeta: La instantánea tardó más de 90 s')
    assert 'mañana' in texto
    segunda = deshacer.tomar(hogar, cwd, 's1', 't1', 'despues')
    assert segunda.get('desactivada') and llamadas == ['t1-antes'], 'no se reintenta a ciegas'
    assert deshacer.aviso(task, segunda) is None, 'se avisa una vez por sesión'
    # Pasado el descanso, se vuelve a intentar.
    marca = Almacen(hogar, cwd).dir / instantaneas.MARCA_SIN_DESHACER
    datos = json.loads(marca.read_text())
    marca.write_text(json.dumps(dict(datos, fecha=datos['fecha'] - instantaneas.DESCANSO - 1)))
    deshacer.tomar(hogar, cwd, 's1', 't2', 'antes')
    assert llamadas == ['t1-antes', 't2-antes']


def test_el_plazo_es_de_toda_la_instantanea(proyecto, monkeypatch):
    hogar, cwd = proyecto
    almacen = Almacen(hogar, cwd)
    almacen.guardar('s1', 'uno')
    monkeypatch.setattr(instantaneas, 'TIEMPO', 0)
    with pytest.raises(TiempoAgotado):
        almacen.guardar('s1', 'dos')


def test_que_herramientas_esperan_a_la_instantanea():
    for nombre in ('write_file', 'edit_file', 'apply_patch', 'run_command', 'spawn_agent', 'mcp__demo__x',
                   'browser_click'):
        assert deshacer.espera(nombre), nombre
    for nombre in ('read_file', 'search', 'list_files', 'update_plan', 'wrap_up', 'browser_read'):
        assert not deshacer.espera(nombre), nombre


def test_el_modelo_responde_sin_esperar_y_lo_que_cambia_archivos_si_espera(proyecto, monkeypatch, capsys):
    from alfalfa.proveedores.nativo.runtime import NativeRuntime
    from alfalfa.terminal import TerminalManager
    hogar, cwd = proyecto
    monkeypatch.chdir(cwd)
    monkeypatch.setenv('ALFALFA_MANTENER_DESPIERTO', '0')
    monkeypatch.setattr(TerminalManager, 'PREPARANDO_TRAS', 0.05)
    soltar, orden = threading.Event(), []
    tomar = deshacer.tomar

    def lenta(*args, **kwargs):
        if args[4] == 'antes':
            orden.append('instantánea empieza')
            (kwargs.get('avance') or args[5])('40 archivos, 1 KB')
            soltar.wait(10)
            orden.append('instantánea termina')
        return tomar(*args, **kwargs)

    async def request(rt, client, endpoint, payload, **kwargs):
        if endpoint == '/api/show':
            return {'capabilities': ['tools']}
        orden.append('modelo')
        await asyncio.sleep(0.2)
        soltar.set()
        await rt.antes_de_cambiar()
        orden.append('herramienta')
        (cwd / 'src' / 'm1.py').write_text('del agente\n')
        return dict(message={'role': 'assistant', 'content': 'Hecho.'}, done=True, done_reason='stop',
                    prompt_eval_count=1, eval_count=1)

    monkeypatch.setattr(deshacer, 'tomar', lenta)
    m = TerminalManager('x', home=hogar)
    try:
        async def run():
            with patch.object(NativeRuntime, 'request', request):
                return await m.run('Hola.')
        asyncio.run(asyncio.wait_for(run(), 60))
    finally:
        m.close()
    assert orden.index('modelo') < orden.index('instantánea termina') < orden.index('herramienta')
    datos = m.task['turnos'][0]['instantanea']
    almacen = Almacen(hogar, cwd)
    assert almacen.contenido(almacen.arbol(datos['antes'])['src/m1.py'][1]) == b'valor = 1\n' * 20
    assert set(almacen.cambios(datos['antes'], datos['despues'])) == {'src/m1.py'}
    salida = capsys.readouterr().out
    assert 'AlfalfaCode · Preparando deshacer… (40 archivos, 1 KB)' in salida
    assert 'AlfalfaCode · Deshacer listo' in salida
    assert 'Pensando' not in salida


def test_la_app_recibe_preparando_y_su_fin(proyecto, monkeypatch):
    from alfalfa.terminal import TerminalManager
    hogar, cwd = proyecto
    monkeypatch.chdir(cwd)
    m = TerminalManager('x', home=hogar)
    emitidos = []
    m.transport = type('Transporte', (), {'emit': lambda self, tipo, **campos: emitidos.append((tipo, campos))})()
    try:
        m._preparacion = {'detalle': None, 'visible': False, 'desde': 0}
        m._preparando('8306 archivos, 1,2 GB', False)
        assert emitidos == [], 'una instantánea rápida no se anuncia'
        m._preparando()
        m._preparado()
        assert emitidos == [('preparando', {'texto': 'Preparando deshacer… (8306 archivos, 1,2 GB)'}),
                            ('preparando', {'texto': None})]
    finally:
        m.transport = None
        m.close()
