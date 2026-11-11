"""run_checks vigila con kqueue un descriptor por archivo y carpeta del proyecto.

macOS da 256 archivos abiertos a lo que se abre desde el Finder (`launchctl limit
maxfiles`), y en un proyecto de miles de archivos toda comprobación, hasta
`/bin/echo ok`, fallaba con «No se pudo vigilar el workspace durante la
comprobación» mientras la misma orden iba bien por run_command (CuyWorld, 11/11).
Ahora el vigilante sube el límite blando lo que necesita, y si el duro no alcanza
lo dice.
"""
import asyncio
import errno
import resource
import subprocess
import sys
from unittest.mock import patch

import pytest

from alfalfa.evidencia import espacio
from alfalfa.proveedores.nativo import historial
from alfalfa.proveedores.nativo.runtime import NativeRuntime
from alfalfa.terminal import TerminalManager

solo_mac = pytest.mark.skipif(sys.platform != 'darwin', reason='kqueue, sólo en macOS')


def _git(raiz, *args):
    subprocess.run(['git', '-c', 'user.name=prueba', '-c', 'user.email=prueba@example.com', *args],
                   cwd=raiz, check=True, capture_output=True)


def _repo_grande(raiz, carpetas=40, archivos=25):
    raiz.mkdir()
    _git(raiz, 'init', '-q')
    for i in range(carpetas):
        carpeta = raiz / f'd{i}'
        carpeta.mkdir()
        for j in range(archivos):
            (carpeta / f'f{j}.txt').write_text(f'{i} {j}\n')
    _git(raiz, 'add', '-A')
    _git(raiz, 'commit', '-qm', 'inicio')
    return raiz


@pytest.fixture
def limite_del_finder():
    """El límite blando de una app abierta desde el Finder; el duro no se toca y se restaura al acabar."""
    blando, duro = resource.getrlimit(resource.RLIMIT_NOFILE)
    resource.setrlimit(resource.RLIMIT_NOFILE, (256, duro))
    try:
        yield
    finally:
        resource.setrlimit(resource.RLIMIT_NOFILE, (blando, duro))


@solo_mac
def test_un_proyecto_de_mil_archivos_se_vigila_con_el_limite_del_finder(tmp_path, limite_del_finder):
    raiz = _repo_grande(tmp_path / 'repo')
    vigia = espacio.MutationWatch([espacio._capture(raiz)])
    try:
        assert len(vigia.files) > 1000
        assert resource.getrlimit(resource.RLIMIT_NOFILE)[0] >= len(vigia.files) + espacio.MARGEN_DESCRIPTORES
        (raiz / 'd0' / 'f0.txt').write_text('cambiado\n')
        assert vigia.changed()
    finally:
        vigia.close()


def test_no_baja_un_limite_que_ya_alcanza():
    blando = resource.getrlimit(resource.RLIMIT_NOFILE)[0]
    assert espacio.subir_limite_de_archivos(10) == blando
    assert resource.getrlimit(resource.RLIMIT_NOFILE)[0] == blando


@solo_mac
def test_si_el_limite_duro_no_alcanza_el_aviso_dice_por_que(tmp_path, monkeypatch):
    raiz = _repo_grande(tmp_path / 'repo')
    monkeypatch.setattr(espacio, 'subir_limite_de_archivos', lambda necesarios: 300)
    with pytest.raises(OSError) as fallo:
        espacio.MutationWatch([espacio._capture(raiz)])
    assert fallo.value.errno == errno.EMFILE
    assert 'sólo puede abrir 300' in fallo.value.strerror


def _turno(tmp_path, raiz, monkeypatch, argv):
    monkeypatch.chdir(raiz)
    pasos = [{'role': 'assistant', 'content': '', 'tool_calls': [{'function': {'name': 'run_checks', 'arguments': {
        'argv': argv, 'purpose': 'Ver el estado del repositorio'}}}]},
             {'role': 'assistant', 'content': 'Hecho.'}]

    async def request(self, client, endpoint, payload, **kwargs):
        if endpoint == '/api/show':
            return {'capabilities': ['tools'], 'model_info': {'x.context_length': 131072}}
        return dict(message=pasos.pop(0) if pasos else {'role': 'assistant', 'content': 'Hecho.'}, done=True,
                    done_reason='stop', prompt_eval_count=900, eval_count=10)
    m = TerminalManager('Mira el estado.', home=tmp_path / 'alfalfa')
    try:
        with patch.object(NativeRuntime, 'request', request):
            asyncio.run(asyncio.wait_for(m.run('Mira el estado.'), 120))
        return historial.leer(m.directory / 'native' / m.task['id'] / 'session.json')
    finally:
        m.close()


@solo_mac
def test_run_checks_en_un_proyecto_grande_con_el_limite_del_finder(tmp_path, monkeypatch, limite_del_finder):
    raiz = _repo_grande(tmp_path / 'repo')
    estado = _turno(tmp_path, raiz, monkeypatch, ['git', 'status', '--short'])
    check, = estado['checks']
    assert check['exit_code'] == 0, check
