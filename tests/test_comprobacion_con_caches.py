"""Una comprobación que deja una caché regenerable (`.pytest_cache/`) sigue valiendo, y un cambio real no.

En un repo sin .gitignore, el primer `run_checks python3 -m pytest` creaba
`.pytest_cache/` en la raíz mientras corría: el arnés lo veía como un cambio
del trabajo («workspace_changed_during_check»), el pytest en verde quedaba «sin
resolver» y el arnés le pedía al modelo repetirlo (prueba real con Claude Haiku
5.5, 09/11). Ya no hay vigilante durante la orden: la huella de después se
compara con la de antes, y sólo valen las cachés y temporales nuevos o borrados
que no tienen nada versionado dentro.
"""
import asyncio
import os
import subprocess
import sys
from unittest.mock import patch

import pytest

from alfalfa.evidencia import espacio
from alfalfa.proveedores.nativo import historial
from alfalfa.proveedores.nativo.runtime import NativeRuntime
from alfalfa.terminal import TerminalManager

solo_mac = pytest.mark.skipif(sys.platform != 'darwin', reason='run_checks corre en Seatbelt, sólo en macOS')


def _git(raiz, *args):
    subprocess.run(['git', '-c', 'user.name=prueba', '-c', 'user.email=prueba@example.com', *args],
                   cwd=raiz, check=True, capture_output=True)


def _repo(raiz, versionar=False):
    raiz.mkdir()
    _git(raiz, 'init', '-q')
    (raiz / 'calculo.py').write_text('def media(n):\n    return sum(n) / len(n)\n')
    (raiz / 'test_calculo.py').write_text('from calculo import media\n\n\ndef test_media():\n'
                                          '    assert media([2, 4, 6]) == 4\n')
    if versionar:
        _git(raiz, 'add', '-A')
        _git(raiz, 'commit', '-qm', 'inicio')
    return raiz


def test_los_nombres_de_cache():
    for nombre in ('.pytest_cache', '__pycache__', '.ruff_cache', 'coverage', 'pytest-cache-files-ab12',
                   '.tmp-alfalfa'):
        assert espacio.nombre_de_cache(nombre), nombre
    for nombre in ('calculo.py', 'conftest.py', 'cache.md', 'src'):
        assert not espacio.nombre_de_cache(nombre), nombre


def test_crear_una_cache_no_cambia_el_trabajo_pero_otro_archivo_si(tmp_path):
    raiz = _repo(tmp_path / 'repo')
    foto = espacio._capture(raiz)
    assert espacio.tras_la_orden(foto) == (True, foto)
    # Como pytest: crea una carpeta temporal y la renombra a .pytest_cache.
    temporal = raiz / 'pytest-cache-files-abc'
    temporal.mkdir()
    (temporal / 'README.md').write_text('caché\n')
    temporal.rename(raiz / '.pytest_cache')
    vale, despues = espacio.tras_la_orden(foto)
    assert vale
    # La foto de después lleva la caché: la comprobación sigue vigente luego.
    assert '.pytest_cache' in despues['watch']['directories'][str(raiz.resolve())]
    assert espacio.matches(despues) and not espacio.matches(foto)
    (raiz / 'nuevo.py').write_text('x = 1\n')
    assert espacio.tras_la_orden(foto) == (False, foto)
    assert not espacio.matches(despues)


def test_escribir_un_archivo_del_proyecto_no_pasa_por_cache(tmp_path):
    raiz = _repo(tmp_path / 'repo')
    foto = espacio._capture(raiz)
    (raiz / '.pytest_cache').mkdir()
    (raiz / 'calculo.py').write_text('def media(n):\n    return 0\n')
    assert espacio.tras_la_orden(foto) == (False, foto)


def test_una_carpeta_versionada_con_nombre_de_cache_sigue_en_la_huella(tmp_path):
    raiz = tmp_path / 'repo'
    raiz.mkdir()
    _git(raiz, 'init', '-q')
    (raiz / 'coverage').mkdir()
    (raiz / 'coverage' / 'report.py').write_text('INFORME = 1\n')
    _git(raiz, 'add', '-A')
    _git(raiz, 'commit', '-qm', 'inicio')
    foto = espacio._capture(raiz)
    assert any(f.endswith('coverage/report.py') for f in foto['watch']['files'])
    (raiz / 'coverage' / 'nuevo.py').write_text('x = 1\n')
    assert not espacio.tras_la_orden(foto)[0]
    (raiz / 'coverage' / 'nuevo.py').unlink()
    (raiz / 'coverage' / 'report.py').write_text('INFORME = 2\n')
    assert not espacio.tras_la_orden(foto)[0]
    assert not espacio.matches(foto)


def test_un_archivo_creado_y_borrado_durante_la_orden_ya_no_se_ve(tmp_path):
    # La pérdida aceptada al quitar el vigilante: sólo cuentan la huella de antes y la de después.
    raiz = _repo(tmp_path / 'repo')
    foto = espacio._capture(raiz)
    (raiz / 'conftest.py').write_text('import os\n')
    (raiz / 'conftest.py').unlink()
    assert espacio.tras_la_orden(foto) == (True, foto)
    # Si se queda, sí cambia el trabajo.
    (raiz / 'conftest.py').write_text('import os\n')
    assert not espacio.tras_la_orden(foto)[0]


def test_un_chmod_de_un_archivo_cambia_el_trabajo(tmp_path):
    raiz = _repo(tmp_path / 'repo')
    foto = espacio._capture(raiz)
    os.chmod(raiz / 'calculo.py', 0o755)
    assert not espacio.tras_la_orden(foto)[0]


def test_borrar_una_cache_sin_versionar_no_cambia_el_trabajo(tmp_path):
    raiz = _repo(tmp_path / 'repo')
    (raiz / '.ruff_cache').mkdir()
    (raiz / '.ruff_cache' / 'CACHEDIR.TAG').write_text('x\n')
    foto = espacio._capture(raiz)
    (raiz / '.ruff_cache' / 'CACHEDIR.TAG').unlink()
    (raiz / '.ruff_cache').rmdir()
    assert espacio.tras_la_orden(foto)[0]


def test_pycache_sin_versionar_no_entra_en_la_huella(tmp_path):
    # Python reescribe los .pyc de lo que se editó: en un repo sin .gitignore eso
    # caducaba la siguiente comprobación aunque el trabajo fuera el mismo.
    raiz = _repo(tmp_path / 'repo')
    (raiz / '__pycache__').mkdir()
    (raiz / '__pycache__' / 'calculo.cpython-313.pyc').write_bytes(b'\0')
    foto = espacio._capture(raiz)
    assert not any('__pycache__' + os.sep in f for f in foto['watch']['files'])
    (raiz / '__pycache__' / 'calculo.cpython-313.pyc').write_bytes(b'\1\2')
    (raiz / '__pycache__' / 'test_calculo.cpython-313.pyc').write_bytes(b'\0')
    assert espacio.tras_la_orden(foto) == (True, foto)


def test_una_foto_de_la_version_anterior_con_pycache_sigue_valiendo(tmp_path):
    # La versión anterior ya listaba __pycache__ en las entradas de la carpeta; ésta también.
    raiz = _repo(tmp_path / 'repo')
    (raiz / '__pycache__').mkdir()
    (raiz / '__pycache__' / 'calculo.cpython-313.pyc').write_bytes(b'\0')
    foto = espacio._capture(raiz)
    assert '__pycache__' in foto['watch']['directories'][str(raiz.resolve())]
    assert espacio.matches(foto)


def test_una_foto_de_la_version_anterior_sin_tamano_sigue_valiendo(tmp_path):
    # Las fotos guardadas en sesiones de antes traen [sha, modo, inodo, mtime, ctime] y no la hora.
    raiz = _repo(tmp_path / 'repo', versionar=True)
    foto = espacio._capture(raiz)
    watch = {**foto['watch'], 'files': {k: v[:5] if v else v for k, v in foto['watch']['files'].items()}}
    watch.pop('tomada_ns')
    vieja = {**foto, 'watch': watch}
    assert espacio.matches(vieja) and espacio.tras_la_orden(vieja)[0]
    (raiz / 'calculo.py').write_text('x = 2\n')
    assert not espacio.matches(vieja)


def _turno(tmp_path, raiz, monkeypatch, argv=None, proposito='Comprobar que test_calculo.py pasa'):
    monkeypatch.chdir(raiz)
    pasos = [{'role': 'assistant', 'content': '', 'tool_calls': [{'function': {'name': 'run_checks', 'arguments': {
        'argv': argv or [sys.executable, '-m', 'pytest', '-q'], 'purpose': proposito}}}]},
             {'role': 'assistant', 'content': 'Hecho.'}]

    async def request(self, client, endpoint, payload, **kwargs):
        if endpoint == '/api/show':
            return {'capabilities': ['tools'], 'model_info': {'x.context_length': 131072}}
        return dict(message=pasos.pop(0) if pasos else {'role': 'assistant', 'content': 'Hecho.'}, done=True,
                    done_reason='stop', prompt_eval_count=900, eval_count=10)
    m = TerminalManager('Comprueba las pruebas.', home=tmp_path / 'alfalfa')
    try:
        with patch.object(NativeRuntime, 'request', request):
            codigo = asyncio.run(asyncio.wait_for(m.run('Comprueba las pruebas.'), 120))
        estado = historial.leer(m.directory / 'native' / m.task['id'] / 'session.json')
        return codigo, m.task['status'], estado
    finally:
        m.close()


@solo_mac
def test_el_primer_pytest_en_verde_vale_aunque_cree_su_cache(tmp_path, monkeypatch):
    raiz = _repo(tmp_path / 'repo')
    codigo, estado_tarea, estado = _turno(tmp_path, raiz, monkeypatch)
    check, = estado['checks']
    assert check['exit_code'] == 0 and '1 passed' in check['output']
    assert (raiz / '.pytest_cache').is_dir()
    assert check['workspace_stable'] is True
    assert check['verification_status'] == 'sufficient'
    assert not estado.get('unresolved')
    assert codigo == 0 and estado_tarea == 'completed'


@solo_mac
def test_un_pytest_en_rojo_que_crea_su_cache_sigue_siendo_un_fallo(tmp_path, monkeypatch):
    raiz = _repo(tmp_path / 'repo')
    (raiz / 'calculo.py').write_text('def media(n):\n    return 0\n')
    _, _, estado = _turno(tmp_path, raiz, monkeypatch)
    check = estado['checks'][0]
    assert check['exit_code'] != 0 and (raiz / '.pytest_cache').is_dir()
    assert check['classification']['disposition'] == 'failure'
    assert estado['unresolved_records'] and all(r['original']['exit_code'] != 0
                                                for r in estado['unresolved_records'].values())


@solo_mac
def test_una_orden_que_cambia_un_archivo_del_proyecto_no_vale(tmp_path, monkeypatch):
    raiz = _repo(tmp_path / 'repo')
    orden = [sys.executable, '-c', "import pathlib; pathlib.Path('calculo.py').write_text('x = 1\\n'); assert True"]
    _, _, estado = _turno(tmp_path, raiz, monkeypatch, orden, 'Comprobar el cálculo')
    check, = estado['checks']
    assert check['exit_code'] == 0 and (raiz / 'calculo.py').read_text() == 'x = 1\n'
    assert check['workspace_stable'] is False
    assert check['verification_status'] == 'workspace_changed_during_check'
