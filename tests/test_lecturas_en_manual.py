"""En Manual y «Aceptar ediciones», las órdenes que sólo leen no preguntan.

La guía dice que esos modos preguntan por «cada orden que no sólo lee», pero `solo_lectura` sólo
reconocía pwd, tres formas de git status, git diff con dos banderas, rg --files y cat/head/wc/ls
con muy pocas opciones y sin nombres que empiecen por punto. git log -1, git diff, ls -lh,
cat .gitignore o rg -n hola preguntaban cada vez.
"""
import asyncio
import os
import subprocess
import sys

import pytest

from alfalfa.permisos.terminal import solo_lectura
from alfalfa.seatbelt import SANDBOX_EXEC
from alfalfa.terminal import TerminalManager

real = pytest.mark.skipif(not (sys.platform == 'darwin' and os.access(SANDBOX_EXEC, os.X_OK)),
                          reason='requiere /usr/bin/sandbox-exec (macOS)')
GIT = ['git', '-c', 'user.name=prueba', '-c', 'user.email=prueba@example.com']


@pytest.fixture
def proyecto(tmp_path):
    raiz = tmp_path / 'proyecto'
    (raiz / 'src').mkdir(parents=True)
    (raiz / 'src' / 'mod.py').write_text('hola = 1\n')
    (raiz / 'README.md').write_text('# Demo\n')
    (raiz / '.gitignore').write_text('*.pyc\n')
    (raiz / '.env').write_text('CLAVE=secreta\n')
    (raiz / 'secrets').mkdir()
    (raiz / 'secrets' / 'x.txt').write_text('no\n')
    (tmp_path / 'fuera.txt').write_text('fuera\n')
    os.symlink(tmp_path / 'fuera.txt', raiz / 'enlace.txt')
    subprocess.run(['git', 'init', '-q'], cwd=raiz, check=True)
    subprocess.run(GIT + ['add', '-A'], cwd=raiz, check=True)
    subprocess.run(GIT + ['commit', '-qm', 'inicio'], cwd=raiz, check=True)
    return raiz


LEEN = [
    ['git', 'log', '-1'], ['git', 'log', '--oneline', '-n', '5'], ['git', 'diff'], ['git', 'diff', '--stat'],
    ['git', 'show', 'HEAD'], ['git', 'show', 'HEAD:README.md'], ['git', 'status', '-s'], ['git', 'status', '-sb'],
    ['git', 'branch'], ['git', 'branch', '-a'], ['git', 'branch', '--show-current'], ['git', 'tag'],
    ['git', 'tag', '-l', 'v1.*'], ['git', 'blame', 'README.md'], ['git', 'rev-parse', 'HEAD'],
    ['git', 'ls-files'], ['git', 'grep', '-n', 'hola'],
    ['ls', '-lh'], ['ls', '-al'], ['ls', '-la', 'src'], ['cat', '.gitignore'], ['cat', '-n', 'README.md'],
    ['head', '-n', '20', 'README.md'], ['head', '-n20', 'README.md'], ['head', '-5', 'README.md'],
    ['tail', 'README.md'], ['tail', '-n', '+2', 'README.md'], ['wc', '-l', 'src/mod.py'],
    ['rg', '-n', 'hola'], ['rg', '--files'], ['rg', '-n', '-g', '*.py', 'hola', 'src'], ['rg', '-e', 'hola', 'src'],
    ['grep', '-rn', 'hola', 'src'], ['grep', '-rn', '--include=*.py', 'hola', '.'],
    ['find', '.', '-name', '*.py'], ['find', 'src', '-type', 'f'],
    ['sed', '-n', '1,5p', 'README.md'], ['du', '-sh', 'src'], ['file', 'README.md'], ['stat', 'README.md'],
    ['pwd'], ['echo', 'hola'], ['which', 'git'],
]

PREGUNTAN = [
    # git que escribe, ejecuta o lee fuera
    ['git', 'branch', 'nueva'], ['git', 'branch', '-D', 'main'], ['git', 'tag', 'v1'], ['git', '-C', '..', 'log'],
    ['git', '-c', 'core.pager=evil', 'log'], ['git', 'diff', '--output=x.txt'], ['git', 'log', '--ext-diff'],
    ['git', 'diff', '--no-index', '/etc/passwd', 'README.md'], ['git', 'grep', '-O', 'hola'], ['git', 'config', '-l'],
    ['git', 'commit', '-m', 'x'], ['git', 'push'], ['git', 'stash'],
    # búsquedas que ejecutan programas
    ['rg', '--pre', 'cat', 'hola'], ['rg', '--pre=cat', 'hola'], ['rg', '-z', 'hola'],
    ['grep', '-f', '/etc/passwd', 'src'],
    ['find', '.', '-delete'], ['find', '.', '-exec', 'rm', '{}', ';'], ['find', '/', '-name', 'x'],
    ['find', '-L', '/', '-name', 'x'],
    # rutas fuera, privadas, con secretos o por enlaces
    ['cat', '.env'], ['cat', '../fuera.txt'], ['cat', '/etc/passwd'], ['cat', '.git/config'], ['cat', 'enlace.txt'],
    ['head', 'secrets/x.txt'], ['grep', 'CLAVE', '.env'], ['sed', '-n', '1p', '../fuera.txt'],
    # que no terminan, escriben o no se conocen
    ['tail', '-f', 'README.md'], ['sed', '-i', 's/a/b/', 'README.md'], ['sort', '-o', 'x', 'README.md'],
    ['ls', '--hyperlink=always'], ['cat', '--nada', 'README.md'], ['/bin/cat', 'README.md'],
    ['./README.md'], ['touch', 'x'], ['python3', '-c', 'print(1)'], ['date', '-s', '1200'],
]


@pytest.mark.parametrize('argv', LEEN, ids=' '.join)
def test_estas_solo_leen(proyecto, argv):
    assert solo_lectura(argv, str(proyecto)) is True


@pytest.mark.parametrize('argv', PREGUNTAN, ids=' '.join)
def test_estas_preguntan(proyecto, argv):
    assert solo_lectura(argv, str(proyecto)) is False


def test_desde_una_subcarpeta_las_rutas_se_miden_desde_ahi(proyecto):
    assert solo_lectura(['cat', 'mod.py'], str(proyecto), proyecto / 'src') is True
    assert solo_lectura(['cat', '../README.md'], str(proyecto), proyecto / 'src') is False


class Recorder:
    def __init__(self):
        self.calls, self.denied = [], 0

    async def __call__(self, args):
        self.calls.append(dict(args))


@real
@pytest.mark.parametrize('modo', ['manual', 'ediciones'])
@pytest.mark.parametrize('argv', [['git', 'log', '-1'], ['ls', '-lh'], ['cat', '.gitignore'], ['rg', '-n', 'hola']],
                         ids=' '.join)
def test_en_manual_corren_sin_tarjeta(proyecto, tmp_path, monkeypatch, modo, argv):
    if argv[0] == 'rg' and not __import__('shutil').which('rg'):
        pytest.skip('rg no está instalado')
    monkeypatch.chdir(proyecto)
    m = TerminalManager('Leer', home=tmp_path / 'alfalfa')
    try:
        m.task['modo'] = modo
        rt = m.runtime()
        rt.approval_policy = recorder = Recorder()
        resultado = asyncio.run(rt.tools.call('run_command', {'argv': argv, 'purpose': 'Leer'}))
    finally:
        m.close()
    assert resultado['exit_code'] == 0, resultado
    assert recorder.calls == []


@real
def test_en_manual_lo_que_escribe_sigue_preguntando(proyecto, tmp_path, monkeypatch):
    monkeypatch.chdir(proyecto)
    m = TerminalManager('Rama', home=tmp_path / 'alfalfa')
    try:
        m.task['modo'] = 'manual'
        rt = m.runtime()
        rt.approval_policy = recorder = Recorder()
        asyncio.run(rt.tools.call('run_command', {'argv': ['git', 'branch', 'nueva'], 'purpose': 'Rama'}))
    finally:
        m.close()
    assert [c['motivo'] for c in recorder.calls] == ['modo']
