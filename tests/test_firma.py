"""La firma de AlfalfaCode en los `git commit` del agente (alfalfa/firma.py)."""
import asyncio
import io
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from alfalfa import ajustes, firma
from alfalfa.firma import FIRMA, Firma, firmar
from alfalfa.proveedores.nativo.herramientas import ON_REQUEST_TOOLS, WorkspaceTools
from alfalfa.stdio import Transport
from alfalfa.terminal import INSTRUCTIONS, signature


@pytest.fixture(autouse=True)
def git_moderno(monkeypatch):
    """Las pruebas no dependen del git instalado, salvo la que lo usa de verdad."""
    monkeypatch.setattr(firma, '_versiones', {'git': True, '/usr/bin/git': True})
    monkeypatch.delenv('ALFALFA_FIRMA', raising=False)


@pytest.mark.parametrize('argv,esperado', [
    (['git', 'commit', '-m', 'Arreglo'], ['git', 'commit', '--trailer', FIRMA, '-m', 'Arreglo']),
    (['/usr/bin/git', 'commit', '-m', 'x', '--', 'a.py'],
     ['/usr/bin/git', 'commit', '--trailer', FIRMA, '-m', 'x', '--', 'a.py']),
    (['git', '-C', 'server', 'commit', '-am', 'x'], ['git', '-C', 'server', 'commit', '--trailer', FIRMA, '-am', 'x']),
    (['git', '-c', 'user.name=Yo', '--no-pager', 'commit', '--amend', '--no-edit'],
     ['git', '-c', 'user.name=Yo', '--no-pager', 'commit', '--trailer', FIRMA, '--amend', '--no-edit']),
])
def test_firma_un_git_commit(argv, esperado):
    assert firmar(argv, FIRMA) == esperado


@pytest.mark.parametrize('argv', [
    ['git', 'status', '--short'],
    ['git', 'log', '--grep', 'commit'],
    ['git', '-C', 'commit', 'status'],                 # «commit» es la carpeta de -C, no la suborden
    ['git'],
    ['echo', 'git', 'commit'],
    ['gitx', 'commit'],
    ['git', 'commit', '--dry-run', '-m', 'x'],
    ['git', 'commit', '-h'],
    ['git', 'commit', '-m', 'x\n\n' + FIRMA],          # ya la lleva
    ['git', 'commit', '-m', 'x', '--trailer', 'Co-Authored-By: Otro <ALFALFACODE@cuycoders.dev>'],
])
def test_no_toca_lo_que_no_es_un_commit_nuevo_sin_firma(argv):
    assert firmar(argv, FIRMA) is argv


def test_sin_linea_o_con_git_antiguo_no_firma(monkeypatch):
    argv = ['git', 'commit', '-m', 'x']
    assert firmar(argv, None) is argv
    monkeypatch.setattr(firma, '_versiones', {'git': False})
    assert firmar(argv, FIRMA) is argv


def test_la_version_de_git_se_lee_una_vez(monkeypatch):
    llamadas = []

    def run(argv, **_):
        llamadas.append(argv)
        return SimpleNamespace(stdout='git version 2.31.9 (Apple Git-1)\n')

    monkeypatch.setattr(firma, '_versiones', {})
    monkeypatch.setattr(firma.subprocess, 'run', run)
    assert firmar(['git', 'commit', '-m', 'x'], FIRMA) == ['git', 'commit', '-m', 'x']
    assert firmar(['git', 'commit', '-m', 'y'], FIRMA) == ['git', 'commit', '-m', 'y']
    assert llamadas == [['git', '--version']]
    monkeypatch.setattr(firma, '_versiones', {})
    monkeypatch.setattr(firma.subprocess, 'run', lambda *a, **k: SimpleNamespace(stdout='git version 2.39.5\n'))
    assert '--trailer' in firmar(['git', 'commit', '-m', 'x'], FIRMA)


def test_el_interruptor_el_entorno_y_el_integrador(monkeypatch):
    con = SimpleNamespace(firma=Firma())
    assert firma.texto(con) == FIRMA
    con.firma.cambiar(False)
    assert firma.texto(con) is None
    con.firma.cambiar(True)
    monkeypatch.setenv('ALFALFA_FIRMA', '0')
    assert firma.texto(con) == FIRMA                   # lo elegido en la sesión manda sobre el entorno
    assert firma.texto(SimpleNamespace(firma=Firma())) is None
    assert firma.texto(SimpleNamespace()) is None      # un integrador sin interruptor sigue al entorno
    monkeypatch.delenv('ALFALFA_FIRMA')
    assert firma.texto(SimpleNamespace()) == FIRMA
    monkeypatch.setattr(ajustes, 'firma_de_commits', lambda: 'Co-Authored-By: Chiyo <chiyo@example.com>')
    assert firma.texto(SimpleNamespace()) == 'Co-Authored-By: Chiyo <chiyo@example.com>'
    for mala in (None, '', '   ', 'Co-Authored-By: A <a@b>\nOtra: línea', 7):
        monkeypatch.setattr(ajustes, 'firma_de_commits', lambda mala=mala: mala)
        assert firma.texto(SimpleNamespace()) is None


def test_run_command_pide_permiso_para_la_orden_firmada():
    pedidos, lanzados = [], []

    async def approve(args):
        pedidos.append(args['argv'])

    runtime = SimpleNamespace(task={'cwd': '.'}, manager=SimpleNamespace(firma=Firma()), excluded_tools=(),
                              tool_specs=ON_REQUEST_TOOLS, state={}, command_policy=None, approve=approve)
    tools = WorkspaceTools(runtime)

    async def sandboxed(args, argv):
        return None, None

    async def launch(args, argv, *resto):
        lanzados.append((args['argv'], argv))
        return {'exit_code': 0}

    tools._sandboxed, tools._launch = sandboxed, launch
    orden = {'argv': ['git', 'commit', '-m', 'Arreglo'], 'purpose': 'Guardar el arreglo'}
    asyncio.run(tools.command(orden))
    firmada = ['git', 'commit', '--trailer', FIRMA, '-m', 'Arreglo']
    assert pedidos == [firmada]
    assert lanzados == [(firmada, firmada)]
    assert orden['argv'] == ['git', 'commit', '-m', 'Arreglo']    # no cambia lo que pidió el modelo
    runtime.manager.firma.cambiar(False)
    pedidos.clear()
    asyncio.run(tools.command(orden))
    assert pedidos == [['git', 'commit', '-m', 'Arreglo']]


@pytest.mark.skipif(shutil.which('git') is None, reason='requiere git')
def test_git_de_verdad_firma_una_vez(tmp_path, monkeypatch):
    monkeypatch.setattr(firma, '_versiones', {})
    git = shutil.which('git')
    if not firma._admite_trailer(git):
        pytest.skip('git anterior a 2.32')
    entorno = {'GIT_AUTHOR_NAME': 'Prueba', 'GIT_AUTHOR_EMAIL': 'prueba@example.com',
               'GIT_COMMITTER_NAME': 'Prueba', 'GIT_COMMITTER_EMAIL': 'prueba@example.com',
               'HOME': str(tmp_path), 'GIT_CONFIG_NOSYSTEM': '1', 'PATH': '/usr/bin:/bin'}
    subprocess.run([git, 'init', '-q', str(tmp_path / 'r')], check=True, env=entorno)
    (tmp_path / 'r' / 'a.txt').write_text('a\n')
    subprocess.run([git, '-C', str(tmp_path / 'r'), 'add', 'a.txt'], check=True, env=entorno)
    orden = firmar([git, '-C', str(tmp_path / 'r'), 'commit', '-q', '-m', 'Primero'], FIRMA)
    subprocess.run(orden, check=True, env=entorno)
    mensaje = subprocess.run([git, '-C', str(tmp_path / 'r'), 'log', '-1', '--format=%B'], check=True,
                             env=entorno, capture_output=True, text=True).stdout
    assert mensaje.strip() == 'Primero\n\n' + FIRMA
    # Un --amend sin editar no la repite.
    orden = firmar([git, '-C', str(tmp_path / 'r'), 'commit', '-q', '--amend', '--no-edit'], FIRMA)
    subprocess.run(orden, check=True, env=entorno)
    mensaje = subprocess.run([git, '-C', str(tmp_path / 'r'), 'log', '-1', '--format=%B'], check=True,
                             env=entorno, capture_output=True, text=True).stdout
    assert mensaje.count(FIRMA) == 1


def test_cli_stdio_e_instrucciones(capsys):
    assert signature(SimpleNamespace(sin_firma=True)) is False
    assert signature(SimpleNamespace(sin_firma=False)) is None
    out = io.StringIO()
    t = Transport(out)
    t.receive('{"tipo":"firma","activo":false}')       # sin sesión aún: inválido
    pedidos = []
    t.signature = pedidos.append
    t.receive('{"tipo":"firma","activo":false}')
    t.receive('{"tipo":"firma","activo":"no"}')
    assert pedidos == [False]
    assert out.getvalue().count('inválido') == 2
    assert 'Co-Authored-By de AlfalfaCode' in INSTRUCTIONS and '--sin-firma' in INSTRUCTIONS
    from alfalfa.terminal import main
    with pytest.raises(SystemExit):
        main(['--help'])
    assert '--sin-firma' in capsys.readouterr().out
