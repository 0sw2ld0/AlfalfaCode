"""Una comprobación que deja una caché regenerable (`.pytest_cache/`) sigue valiendo, y un cambio real no.

En un repo sin .gitignore, el primer `run_checks python3 -m pytest` creaba
`.pytest_cache/` en la raíz mientras corría: el vigilante lo veía como un cambio
del trabajo («workspace_changed_during_check»), el pytest en verde quedaba «sin
resolver» y el arnés le pedía al modelo repetirlo (prueba real con Claude Haiku
5.5, 09/11). Ahora cada aviso del vigilante se explica: sólo valen las cachés y
temporales nuevos o borrados que no tienen nada versionado dentro.
"""
import asyncio
import os
import struct
import subprocess
import sys
import time
from unittest.mock import patch

import pytest

from alfalfa.evidencia import espacio
from alfalfa.proveedores.nativo import historial
from alfalfa.proveedores.nativo.runtime import NativeRuntime
from alfalfa.terminal import TerminalManager

solo_mac = pytest.mark.skipif(sys.platform != 'darwin', reason='kqueue y Seatbelt, sólo en macOS')
solo_linux = pytest.mark.skipif(not sys.platform.startswith('linux'), reason='inotify, sólo en Linux')


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


def _esperar():
    # El hilo del vigilante lista la carpeta en cuanto avisa (cada 50 ms como mucho).
    time.sleep(0.2)


def test_los_nombres_de_cache():
    for nombre in ('.pytest_cache', '__pycache__', '.ruff_cache', 'coverage', 'pytest-cache-files-ab12',
                   '.tmp-alfalfa'):
        assert espacio.nombre_de_cache(nombre), nombre
    for nombre in ('calculo.py', 'conftest.py', 'cache.md', 'src'):
        assert not espacio.nombre_de_cache(nombre), nombre


@solo_mac
def test_crear_una_cache_no_cambia_el_trabajo_pero_otro_archivo_si(tmp_path):
    raiz = _repo(tmp_path / 'repo')
    foto = espacio._capture(raiz)
    vigia = espacio.MutationWatch([foto])
    try:
        assert not vigia.changed()
        # Como pytest: crea una carpeta temporal y la renombra a .pytest_cache.
        temporal = raiz / 'pytest-cache-files-abc'
        temporal.mkdir()
        (temporal / 'README.md').write_text('caché\n')
        temporal.rename(raiz / '.pytest_cache')
        _esperar()
        assert vigia.changed() and vigia.solo_caches()
        assert espacio.cambio_sin_trabajo(foto, vigia)
        # La foto con las cachés sigue valiendo después, aunque ya no haya vigilante.
        assert espacio.matches(espacio.con_caches(foto, vigia))
        (raiz / 'nuevo.py').write_text('x = 1\n')
        _esperar()
        assert not vigia.solo_caches() and not espacio.cambio_sin_trabajo(foto, vigia)
    finally:
        vigia.close()


@solo_mac
def test_escribir_un_archivo_vigilado_no_pasa_por_cache(tmp_path):
    raiz = _repo(tmp_path / 'repo')
    foto = espacio._capture(raiz)
    vigia = espacio.MutationWatch([foto])
    try:
        (raiz / '.pytest_cache').mkdir()
        (raiz / 'calculo.py').write_text('def media(n):\n    return 0\n')
        _esperar()
        assert vigia.changed() and not espacio.cambio_sin_trabajo(foto, vigia)
    finally:
        vigia.close()


@solo_mac
def test_una_carpeta_versionada_con_nombre_de_cache_sigue_vigilada(tmp_path):
    raiz = tmp_path / 'repo'
    raiz.mkdir()
    _git(raiz, 'init', '-q')
    (raiz / 'coverage').mkdir()
    (raiz / 'coverage' / 'report.py').write_text('INFORME = 1\n')
    _git(raiz, 'add', '-A')
    _git(raiz, 'commit', '-qm', 'inicio')
    foto = espacio._capture(raiz)
    assert any(f.endswith('coverage/report.py') for f in foto['watch']['files'])
    vigia = espacio.MutationWatch([foto])
    try:
        (raiz / 'coverage' / 'report.py').write_text('INFORME = 2\n')
        (raiz / 'coverage' / 'nuevo.py').write_text('x = 1\n')
        _esperar()
        assert not espacio.cambio_sin_trabajo(foto, vigia)
    finally:
        vigia.close()
    assert not espacio.matches(foto)


@solo_mac
def test_un_archivo_creado_y_borrado_durante_la_orden_se_ve(tmp_path):
    raiz = _repo(tmp_path / 'repo')
    foto = espacio._capture(raiz)
    for pausa in (0.2, 0):
        vigia = espacio.MutationWatch([foto])
        try:
            (raiz / 'conftest.py').write_text('import os\n')
            time.sleep(pausa)
            (raiz / 'conftest.py').unlink()
            _esperar()
            # Al final todo está igual, pero el aviso no lo explica ninguna caché.
            assert espacio.matches(foto)
            assert vigia.changed() and not espacio.cambio_sin_trabajo(foto, vigia), pausa
        finally:
            vigia.close()


@solo_mac
def test_un_chmod_de_la_carpeta_no_es_una_cache(tmp_path):
    raiz = _repo(tmp_path / 'repo')
    foto = espacio._capture(raiz)
    vigia = espacio.MutationWatch([foto])
    try:
        (raiz / '.pytest_cache').mkdir()
        os.chmod(raiz, 0o700)
        _esperar()
        assert not espacio.cambio_sin_trabajo(foto, vigia)
    finally:
        vigia.close()


def test_una_foto_de_la_version_anterior_con_pycache_sigue_valiendo(tmp_path):
    # La versión anterior ya listaba __pycache__ en las entradas de la carpeta; ésta también.
    raiz = _repo(tmp_path / 'repo')
    (raiz / '__pycache__').mkdir()
    (raiz / '__pycache__' / 'calculo.cpython-313.pyc').write_bytes(b'\0')
    foto = espacio._capture(raiz)
    assert '__pycache__' in foto['watch']['directories'][str(raiz.resolve())]
    assert espacio.matches(foto)


def _inotify(*eventos):
    """Un buffer de inotify con estos (wd, máscara, nombre)."""
    salida = b''
    for wd, mascara, nombre in eventos:
        crudo = os.fsencode(nombre)
        relleno = crudo + b'\0' * (16 - len(crudo) % 16) if crudo else b''
        salida += struct.pack('iIII', wd, mascara, 0, len(relleno)) + relleno
    return salida


def _vigia_inotify(raiz):
    """Un vigilante con el estado de inotify, sin registrar nada en el sistema."""
    vigia = object.__new__(espacio.MutationWatch)
    vigia.avisos = vigia.sucio = False
    vigia.caches, vigia.rutas = {}, {1: str(raiz), 2: str(raiz / 'calculo.py'), 3: str(raiz / 'coverage')}
    vigia.carpetas = {str(raiz), str(raiz / 'coverage')}
    vigia.vigilados = {str(raiz / 'calculo.py'), str(raiz / 'coverage' / 'report.py')}
    return vigia


def test_en_linux_inotify_trae_el_nombre_y_se_explica_igual(tmp_path):
    raiz = tmp_path
    assert espacio.leer_inotify(_inotify((1, espacio.IN_CREATE, '.pytest_cache'), (2, espacio.IN_MODIFY, ''))) == [
        (1, espacio.IN_CREATE, '.pytest_cache'), (2, espacio.IN_MODIFY, '')]
    casos = [
        ([(1, espacio.IN_CREATE | 0x40000000, 'pytest-cache-files-x'),
          (1, espacio.IN_MOVED_FROM, 'pytest-cache-files-x'), (1, espacio.IN_MOVED_TO, '.pytest_cache')], False),
        ([(1, espacio.IN_CREATE, 'conftest.py'), (1, espacio.IN_DELETE, 'conftest.py')], True),
        ([(1, espacio.IN_CREATE, '.pytest_cache'), (2, espacio.IN_MODIFY, '')], True),
        ([(1, espacio.IN_ATTRIB, '')], True),
        ([(3, espacio.IN_CREATE, 'nuevo.py')], True),
        ([(1, espacio.IN_DELETE, 'coverage')], True),
        ([(1, espacio.IN_Q_OVERFLOW, '')], True),
        ([(9, espacio.IN_CREATE, '.pytest_cache')], True),
    ]
    for eventos, sucio in casos:
        vigia = _vigia_inotify(raiz)
        vigia._inotify(espacio.leer_inotify(_inotify(*eventos)))
        assert vigia.avisos and vigia.sucio is sucio, eventos
    vigia = _vigia_inotify(raiz)
    vigia._inotify(espacio.leer_inotify(_inotify((1, espacio.IN_CREATE, '.pytest_cache'))))
    assert vigia.caches == {str(raiz): {'.pytest_cache'}}


@solo_linux
def test_en_linux_crear_una_cache_no_cambia_el_trabajo(tmp_path):
    raiz = _repo(tmp_path / 'repo')
    foto = espacio._capture(raiz)
    vigia = espacio.MutationWatch([foto])
    try:
        (raiz / '.pytest_cache').mkdir()
        assert espacio.cambio_sin_trabajo(foto, vigia)
        (raiz / 'conftest.py').write_text('x = 1\n')
        (raiz / 'conftest.py').unlink()
        assert not espacio.cambio_sin_trabajo(foto, vigia)
    finally:
        vigia.close()


def _turno(tmp_path, raiz, monkeypatch):
    monkeypatch.chdir(raiz)
    pasos = [{'role': 'assistant', 'content': '', 'tool_calls': [{'function': {'name': 'run_checks', 'arguments': {
        'argv': [sys.executable, '-m', 'pytest', '-q'], 'purpose': 'Comprobar que test_calculo.py pasa'}}}]},
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
