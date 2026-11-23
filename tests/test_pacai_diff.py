"""`diff_updated` sin instantáneas de deshacer: el diff de la ejecución sale de git, sin tocar el índice, la rama ni
el árbol del usuario, respetando `.gitignore` y sin archivos nuevos enormes. Fuera de un repo git, no hay diff."""
import subprocess
import time
from types import SimpleNamespace

import pytest

from alfalfa.pacai.diff_git import MAX_NUEVO, PuntoDePartida
from tests.pacai_comun import Cliente, abrir, cerrar


def git(carpeta, *args):
    return subprocess.run(['git', *args], cwd=carpeta, check=True, capture_output=True).stdout


@pytest.fixture
def repo(tmp_path):
    carpeta = tmp_path / 'repo'
    carpeta.mkdir()
    git(carpeta, 'init', '-q', '-b', 'main')
    (carpeta / '.gitignore').write_text('secreto.env\nbuild/\n')
    (carpeta / 'a.py').write_text('uno\ndos\ntres\n')
    (carpeta / 'borrar.txt').write_text('adiós\n')
    git(carpeta, 'add', '-A')
    git(carpeta, 'commit', '-q', '-m', 'inicio')
    (carpeta / 'a.py').write_text('uno\ndos\ntres\ncuatro\n')          # cambio previo sin confirmar: ya estaba
    (carpeta / 'previo.txt').write_text('de antes\n')                  # nuevo sin seguimiento: ya estaba
    return carpeta


def estado_del_usuario(carpeta):
    """Lo que el usuario ve de su repo: índice, rama, HEAD y `git status`."""
    return ((carpeta / '.git' / 'index').read_bytes(), git(carpeta, 'symbolic-ref', 'HEAD'),
            git(carpeta, 'rev-parse', 'HEAD'), git(carpeta, 'status', '--porcelain', '-uall'))


def cambiar(carpeta):
    (carpeta / 'a.py').write_text('uno\nDOS\ntres\ncuatro\n')
    (carpeta / 'borrar.txt').unlink()
    (carpeta / 'nuevo.md').write_text('hola\n')
    (carpeta / 'secreto.env').write_text('CLAVE=1\n')                  # ignorado
    (carpeta / 'build').mkdir()
    (carpeta / 'build' / 'salida.o').write_bytes(b'\0' * 10)           # ignorado
    (carpeta / 'enorme.bin').write_bytes(b'\0' * (MAX_NUEVO + 1))      # nuevo y enorme
    (carpeta / 'logo.png').write_bytes(b'\x89PNG\0\0\1')               # binario pequeño


def test_el_diff_de_git_cuenta_solo_lo_que_cambio_la_ejecucion_y_no_toca_nada_del_usuario(repo):
    antes = estado_del_usuario(repo)
    partida = PuntoDePartida.tomar(repo)
    cambiar(repo)
    durante = estado_del_usuario(repo)
    diff = partida.diff()
    partida.olvidar()
    assert diff['files'] == [{'path': 'a.py', 'added': 1, 'removed': 1}, {'path': 'borrar.txt', 'added': 0,
                             'removed': 1}, {'path': 'logo.png'}, {'path': 'nuevo.md', 'added': 1, 'removed': 0}]
    assert '-dos\n+DOS\n' in diff['patch'] and '+hola\n' in diff['patch'] and 'Binary files' in diff['patch']
    assert '+cuatro' not in diff['patch']                               # lo que ya estaba antes no cuenta
    assert 'CLAVE' not in diff['patch'] and 'enorme' not in diff['patch'] and 'previo' not in diff['patch']
    assert estado_del_usuario(repo)[:3] == antes[:3]                   # índice, rama y HEAD intactos
    assert estado_del_usuario(repo) == durante                          # ni `git status` cambia por el diff
    assert not partida.temporal.exists()


def test_lo_nuevo_en_tmp_alfalfa_no_entra_aunque_el_repo_no_lo_ignore(repo):
    partida = PuntoDePartida.tomar(repo)
    (repo / '.tmp-alfalfa' / 'gradle-home' / 'daemon').mkdir(parents=True)
    (repo / '.tmp-alfalfa' / 'gradle-home' / 'daemon' / 'daemon-1.out.log').write_text('registro\n')
    (repo / 'sub' / '.tmp-alfalfa').mkdir(parents=True)
    (repo / 'sub' / '.tmp-alfalfa' / 'captura.txt').write_text('antes\n')
    (repo / '.tmp-alfalfa.md').write_text('no es la carpeta\n')
    diff = partida.diff()
    partida.olvidar()
    assert diff['files'] == [{'path': '.tmp-alfalfa.md', 'added': 1, 'removed': 0}]


def _al_principio_de_un_segundo():
    while time.time() % 1 > 0.3:
        time.sleep(0.01)


def test_un_cambio_del_mismo_tamano_en_el_mismo_segundo_cuenta(repo):
    """«Racy git»: si el diff se calcula en un segundo posterior, un cambio del mismo tamaño hecho en el mismo
    segundo que la foto no debe pasar por intacto. Hacía fallar a veces la prueba de arriba en la suite."""
    _al_principio_de_un_segundo()
    (repo / 'a.py').write_text('uno\ndos\ntres\ncinco\n')
    partida = PuntoDePartida.tomar(repo)
    (repo / 'a.py').write_text('uno\nDOS\ntres\ncinco\n')            # mismo tamaño, mismo segundo
    time.sleep(1.1)
    diff = partida.diff()
    partida.olvidar()
    assert diff is not None and [f['path'] for f in diff['files']] == ['a.py'], diff


def test_un_cambio_previo_del_mismo_tamano_no_se_atribuye_a_la_ejecucion(repo):
    """Lo mismo con el índice del usuario: un cambio suyo de antes de la ejecución, del mismo tamaño y en el mismo
    segundo que su `git add`, ya estaba; la ejecución no lo hizo."""
    _al_principio_de_un_segundo()
    git(repo, 'add', 'a.py')
    (repo / 'a.py').write_text('uno\nDOS\ntres\ncuatro\n')           # mismo tamaño que lo añadido
    time.sleep(1.1)
    partida = PuntoDePartida.tomar(repo)
    (repo / 'nuevo.md').write_text('hola\n')
    diff = partida.diff()
    partida.olvidar()
    assert diff is not None and [f['path'] for f in diff['files']] == ['nuevo.md'], diff


def test_en_una_subcarpeta_el_diff_es_de_esa_carpeta_con_rutas_relativas(repo):
    sub = repo / 'sub'
    sub.mkdir()
    (sub / 'x.txt').write_text('1\n')
    partida = PuntoDePartida.tomar(sub)
    (sub / 'x.txt').write_text('2\n')
    (repo / 'a.py').write_text('fuera de la carpeta\n')
    assert partida.diff()['files'] == [{'path': 'x.txt', 'added': 1, 'removed': 1}]
    partida.olvidar()


def test_sin_cambios_o_fuera_de_un_repo_no_hay_diff(repo, tmp_path):
    partida = PuntoDePartida.tomar(repo)
    assert partida.diff() is None
    partida.olvidar()
    suelta = tmp_path / 'suelta'
    suelta.mkdir()
    assert PuntoDePartida.tomar(suelta) is None


def _cliente(carpeta):
    c = Cliente()
    c.t.conectar(SimpleNamespace(task={'cwd': str(carpeta), 'turnos': []}))     # un turno sin instantáneas
    return c


async def test_sin_instantaneas_diff_updated_sale_de_git(repo):
    c = _cliente(repo)
    run = await abrir(c)
    await c.t.antes_del_turno()
    cambiar(repo)
    cierre = await cerrar(c)
    diff = next(m for m in cierre if m['type'] == 'diff_updated')['payload']
    assert diff['run_id'] == run and [f['path'] for f in diff['files']] == ['a.py', 'borrar.txt', 'logo.png',
                                                                            'nuevo.md']
    assert '+DOS' in diff['patch']
    c.comprobar()


async def test_un_parche_enorme_sale_solo_con_la_lista_de_archivos(repo, monkeypatch):
    from alfalfa.pacai import diff_git
    monkeypatch.setattr(diff_git, 'MAX_PARCHE', 10)
    c = _cliente(repo)
    await abrir(c)
    await c.t.antes_del_turno()
    cambiar(repo)
    diff = next(m for m in await cerrar(c) if m['type'] == 'diff_updated')['payload']
    assert 'patch' not in diff and len(diff['files']) == 4
    c.comprobar()


async def test_fuera_de_un_repo_git_o_sin_el_perfil_work_no_hay_diff(tmp_path, repo):
    suelta = tmp_path / 'suelta'
    suelta.mkdir()
    c = _cliente(suelta)
    await abrir(c)
    await c.t.antes_del_turno()
    (suelta / 'x.txt').write_text('hola\n')
    assert 'diff_updated' not in [m['type'] for m in await cerrar(c)]
    sin_trabajo = Cliente(perfiles=())
    sin_trabajo.t.conectar(SimpleNamespace(task={'cwd': str(repo), 'turnos': []}))
    await abrir(sin_trabajo)
    await sin_trabajo.t.antes_del_turno()
    assert sin_trabajo.t._partida is None
    cambiar(repo)
    assert 'diff_updated' not in [m['type'] for m in await cerrar(sin_trabajo)]


def test_una_sesion_real_sin_instantaneas_manda_el_diff_de_git(tmp_path, monkeypatch):
    from tests.pacai_comun import problemas
    from tests.test_pacai_argumentos import CONTENIDO, sesion_pacai
    monkeypatch.setenv('ALFALFA_INSTANTANEAS', '0')
    git(tmp_path, 'init', '-q', '-b', 'main')
    (tmp_path / '.gitignore').write_text('home/\n')                    # el hogar de AlfalfaCode de la prueba
    git(tmp_path, 'add', '-A')
    git(tmp_path, 'commit', '-q', '-m', 'inicio')
    log = sesion_pacai(tmp_path)
    assert problemas(log) == []
    diff = next(m for m in log if m['type'] == 'diff_updated')['payload']
    assert diff['files'] == [{'path': 'largo.txt', 'added': 1, 'removed': 0}]
    assert '+' + CONTENIDO in diff['patch']
    assert git(tmp_path, 'status', '--porcelain') == b'?? largo.txt\n'    # nada quedó añadido al índice
