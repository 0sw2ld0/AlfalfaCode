"""El git que lanza el propio arnés (fuera del sandbox) no ejecuta programas del proyecto.

Antes ese git leía la configuración del repositorio y ejecutaba lo que mandara: `core.fsmonitor`, un
hook, un filtro `clean` o un `textconv`. Bastaba un `.git/config` que llegara con la carpeta o un
repositorio que el agente montara dentro del sandbox (`vendor/x`, sin llamarse `.git`) y una
comprobación con `cwd` ahí: el programa corría con todo el entorno del usuario, fuera del sandbox.

Cada programa de estas pruebas escribe una marca en la carpeta de AlfalfaCode, que el sandbox no
deja tocar: si aparece, lo ejecutó el arnés.
"""
import asyncio
import os
import subprocess
import sys

import pytest

from alfalfa import git_arnes
from alfalfa.evidencia.espacio import capture
from alfalfa.proveedores.nativo import runtime as nativo
from alfalfa.seatbelt import SANDBOX_EXEC
from alfalfa.terminal import TerminalManager

real = pytest.mark.skipif(not (sys.platform == 'darwin' and os.access(SANDBOX_EXEC, os.X_OK)),
                          reason='requiere /usr/bin/sandbox-exec (macOS)')
GIT = ['git', '-c', 'user.name=prueba', '-c', 'user.email=prueba@example.com']


def guion(ruta, marca, nombre):
    ruta.write_text('#!/bin/sh\necho "' + nombre + ' SECRETO=${ALFALFA_PRUEBA_SECRETO:-no}" >> "' + str(marca)
                    + '"\ncat\nexit 0\n')
    ruta.chmod(0o755)
    return ruta


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """Un repositorio cuyo .git/config, como llegaría en un zip, trae fsmonitor, un hook, un filtro y un
    textconv que escriben una marca."""
    monkeypatch.setenv('ALFALFA_PRUEBA_SECRETO', 'clave-de-prueba')
    raiz = tmp_path / 'proyecto'
    raiz.mkdir()
    marca = tmp_path / 'alfalfa' / 'MARCA.txt'
    marca.parent.mkdir()
    subprocess.run(['git', 'init', '-q'], cwd=raiz, check=True)
    (raiz / 'a.txt').write_text('uno\n')
    (raiz / '.gitattributes').write_text('*.txt filter=malo diff=malo\n')
    subprocess.run(GIT + ['add', '-A'], cwd=raiz, check=True)
    subprocess.run(GIT + ['commit', '-qm', 'inicio'], cwd=raiz, check=True)
    programa = guion(tmp_path / 'programa.sh', marca, 'programa')
    hooks = tmp_path / 'hooks'
    hooks.mkdir()
    guion(hooks / 'post-index-change', marca, 'hook')
    for clave, valor in (('core.fsmonitor', programa), ('core.hooksPath', hooks), ('filter.malo.clean', programa),
                         ('filter.malo.smudge', programa), ('diff.malo.textconv', programa)):
        subprocess.run(['git', 'config', clave, str(valor)], cwd=raiz, check=True)
    marca.unlink(missing_ok=True)
    (raiz / 'a.txt').write_text('dos\n')  # un cambio: status tiene que mirar el contenido
    return raiz, marca


def test_el_entorno_del_primer_mensaje_no_ejecuta_nada(repo):
    raiz, marca = repo
    texto = nativo.entorno(raiz)
    assert 'Git: rama' in texto and '1 archivos con cambios' in texto
    assert not marca.exists(), marca.read_text()


def test_la_huella_de_cada_orden_no_ejecuta_nada(repo):
    raiz, marca = repo
    huella = asyncio.run(capture(raiz))
    assert huella and not marca.exists(), marca.read_text() if marca.exists() else ''


def test_run_no_hereda_las_claves_del_usuario(repo, monkeypatch):
    raiz, _ = repo
    monkeypatch.setenv('GIT_DIR', '/nada')
    salida = git_arnes.run(raiz, 'rev-parse', '--show-toplevel', capture_output=True, text=True)
    assert salida.returncode == 0 and os.path.realpath(salida.stdout.strip()) == os.path.realpath(raiz)
    assert 'ALFALFA_PRUEBA_SECRETO' not in git_arnes.entorno() and 'GIT_DIR' not in git_arnes.entorno()


def test_las_opciones_anulan_lo_que_define_el_repositorio(repo):
    raiz, _ = repo
    opciones = git_arnes.opciones(raiz)
    for esperado in ('core.fsmonitor=false', 'core.hooksPath=/dev/null', 'filter.malo.clean=',
                     'filter.malo.smudge=', 'diff.malo.textconv='):
        assert esperado in opciones


def test_el_mismo_git_de_antes_si_ejecutaba(repo):
    """Control: sin las opciones, el git corriente ejecuta el programa (si no, la prueba no prueba nada)."""
    raiz, marca = repo
    subprocess.run(['git', '-C', str(raiz), 'status', '--short'], capture_output=True, timeout=30)
    assert marca.exists()


@real
def test_una_comprobacion_con_cwd_en_un_repositorio_montado_por_el_agente(tmp_path, monkeypatch):
    raiz = tmp_path / 'proyecto'
    raiz.mkdir()
    subprocess.run(['git', 'init', '-q'], cwd=raiz, check=True)
    monkeypatch.chdir(raiz)
    m = TerminalManager('Comprobar', home=tmp_path / 'alfalfa', auto=True)
    marca = m.home / 'MARCA.txt'
    # Lo que el agente puede crear dentro del sandbox: un repositorio que no se llama .git.
    sub = raiz / 'vendor' / 'x'
    for carpeta in ('objects', 'refs', 'wt'):
        (sub / carpeta).mkdir(parents=True)
    (sub / 'HEAD').write_text('ref: refs/heads/main\n')
    guion(sub / 'm.sh', marca, 'fsmonitor')
    (sub / 'config').write_text('[core]\n\trepositoryformatversion = 0\n\tbare = false\n\tworktree = wt\n'
                                '\tfsmonitor = ' + str(sub / 'm.sh') + '\n')
    try:
        rt = m.runtime()
        asyncio.run(rt.tools.call('run_checks', {'argv': ['git', 'status', '--short'], 'cwd': 'vendor/x',
                                                 'purpose': 'Comprobar el estado'}))
    finally:
        m.close()
    assert not marca.exists(), marca.read_text()
