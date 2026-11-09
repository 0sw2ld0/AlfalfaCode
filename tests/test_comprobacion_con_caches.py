"""Una comprobación que deja una caché regenerable (`.pytest_cache/`) sigue valiendo.

En un repo sin .gitignore, el primer `run_checks python3 -m pytest` creaba
`.pytest_cache/` en la raíz mientras corría: el vigilante lo veía como un cambio
del trabajo («workspace_changed_during_check»), el pytest en verde quedaba «sin
resolver» y el arnés le pedía al modelo repetirlo (prueba real con Claude Haiku
5.5, 09/11). Las cachés regenerables cuentan como los temporales `.tmp-…`: no son
el trabajo.
"""
import asyncio
import json
import subprocess
import sys
from unittest.mock import patch

import pytest

from alfalfa.evidencia import espacio
from alfalfa.proveedores.nativo.runtime import NativeRuntime
from alfalfa.terminal import TerminalManager

solo_mac = pytest.mark.skipif(sys.platform != 'darwin', reason='run_checks y kqueue, sólo en macOS')


def _repo(raiz):
    raiz.mkdir()
    subprocess.run(['git', 'init', '-q'], cwd=raiz, check=True)
    (raiz / 'calculo.py').write_text('def media(n):\n    return sum(n) / len(n)\n')
    (raiz / 'test_calculo.py').write_text('from calculo import media\n\n\ndef test_media():\n'
                                          '    assert media([2, 4, 6]) == 4\n')
    return raiz


def test_las_caches_regenerables_no_son_el_trabajo():
    assert espacio.en_cache('.pytest_cache')
    assert espacio.en_cache('src/__pycache__/calculo.cpython-313.pyc')
    assert espacio.en_cache('.ruff_cache/0.12/abc')
    assert not espacio.en_cache('calculo.py')
    assert not espacio.en_cache('docs/cache.md')


@solo_mac
def test_crear_una_cache_no_cambia_el_trabajo_pero_otro_archivo_si(tmp_path):
    raiz = _repo(tmp_path / 'repo')
    foto = espacio._capture(raiz)
    vigia = espacio.MutationWatch([foto])
    try:
        assert not vigia.changed()
        (raiz / '.pytest_cache' / 'v').mkdir(parents=True)
        (raiz / '__pycache__').mkdir()
        assert vigia.changed()
        assert espacio.cambio_sin_trabajo(foto, vigia)
        # Un archivo nuevo de verdad sí cambia el trabajo.
        (raiz / 'nuevo.py').write_text('x = 1\n')
        assert not espacio.cambio_sin_trabajo(foto, vigia)
    finally:
        vigia.close()
    # Ni siquiera hace falta el vigilante: después, la foto sigue valiendo sin contar las cachés.
    (raiz / 'nuevo.py').unlink()
    assert espacio.matches(foto)


@solo_mac
def test_escribir_un_archivo_vigilado_no_pasa_por_cache(tmp_path):
    raiz = _repo(tmp_path / 'repo')
    foto = espacio._capture(raiz)
    vigia = espacio.MutationWatch([foto])
    try:
        (raiz / '.pytest_cache').mkdir()
        (raiz / 'calculo.py').write_text('def media(n):\n    return 0\n')
        assert vigia.changed() and not espacio.cambio_sin_trabajo(foto, vigia)
    finally:
        vigia.close()


@solo_mac
def test_el_primer_pytest_en_verde_vale_aunque_cree_su_cache(tmp_path, monkeypatch):
    raiz = _repo(tmp_path / 'repo')
    monkeypatch.chdir(raiz)
    pasos = [{'role': 'assistant', 'content': '', 'tool_calls': [{'function': {'name': 'run_checks', 'arguments': {
        'argv': [sys.executable, '-m', 'pytest', '-q'], 'purpose': 'Comprobar que test_calculo.py pasa'}}}]},
             {'role': 'assistant', 'content': 'La prueba pasa.'}]
    vistos = []

    async def request(self, client, endpoint, payload, **kwargs):
        if endpoint == '/api/show':
            return {'capabilities': ['tools'], 'model_info': {'x.context_length': 131072}}
        vistos.append(payload['messages'])
        return dict(message=pasos.pop(0), done=True, done_reason='stop', prompt_eval_count=900, eval_count=10)
    m = TerminalManager('Comprueba las pruebas.', home=tmp_path / 'alfalfa')
    try:
        with patch.object(NativeRuntime, 'request', request):
            codigo = asyncio.run(asyncio.wait_for(m.run('Comprueba las pruebas.'), 120))
        estado = json.loads((m.directory / 'native' / m.task['id'] / 'session.json').read_text())
        check, = estado['checks']
        assert check['exit_code'] == 0 and '1 passed' in check['output']
        assert (raiz / '.pytest_cache').is_dir()
        assert check['verification_status'] == 'sufficient'
        assert not estado.get('unresolved')
        # El turno terminó sin el aviso de «operaciones sin resolver».
        assert codigo == 0 and m.task['status'] == 'completed'
        assert len(vistos) == 2 and not pasos
    finally:
        m.close()
