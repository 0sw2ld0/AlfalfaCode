"""run_checks en proyectos grandes: la huella no abre un descriptor por archivo y no relee lo que no cambió.

macOS da 256 archivos abiertos a lo que se abre desde el Finder (`launchctl limit
maxfiles`). Con el vigilante de antes (kqueue, un descriptor por archivo y
carpeta), en un proyecto de miles de archivos toda comprobación, hasta
`/bin/echo ok`, fallaba con «No se pudo vigilar el workspace durante la
comprobación» mientras la misma orden iba bien por run_command (CuyWorld, 11/11).
Ya no hay vigilante: una huella antes y otra después, que como git sólo lee los
archivos cuyo stat cambió. Y si la huella no se puede tomar, la orden corre igual
y queda sin verificar.
"""
import asyncio
import hashlib
import resource
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


@pytest.fixture
def lecturas(monkeypatch):
    """Cuenta los archivos que la huella lee para calcular su hash, sin hashes guardados de antes."""
    leidos = []
    original = hashlib.file_digest

    def contar(archivo, digest):
        leidos.append(archivo.name)
        return original(archivo, digest)
    monkeypatch.setattr(hashlib, 'file_digest', contar)
    espacio.olvidar_hashes()
    yield leidos
    espacio.olvidar_hashes()


def test_un_proyecto_de_mil_archivos_con_el_limite_del_finder(tmp_path, limite_del_finder):
    raiz = _repo_grande(tmp_path / 'repo')
    foto = espacio._capture(raiz)
    assert len(foto['watch']['files']) > 1000
    assert espacio.tras_la_orden(foto)[0]
    (raiz / 'd0' / 'f0.txt').write_text('cambiado\n')
    assert not espacio.tras_la_orden(foto)[0]


def test_la_huella_repetida_no_relee_lo_que_no_cambio(tmp_path, lecturas, monkeypatch):
    # Sin margen de racy mtime: los archivos de la prueba se acaban de escribir.
    monkeypatch.setattr(espacio, 'MARGEN_RACY_NS', 0)
    raiz = _repo_grande(tmp_path / 'repo', carpetas=60, archivos=50)
    foto = espacio._capture(raiz)
    assert len(lecturas) >= 3000
    lecturas.clear()
    assert espacio.tras_la_orden(foto)[0]
    foto = espacio._capture(raiz)
    assert espacio.tras_la_orden(foto)[0]
    assert lecturas == []
    (raiz / 'd7' / 'f3.txt').write_text('otro contenido\n')
    assert not espacio.tras_la_orden(foto)[0]
    lecturas.clear()
    espacio._capture(raiz)
    assert lecturas == [str(raiz.resolve() / 'd7' / 'f3.txt')]


def test_racy_mtime_un_stat_igual_cerca_de_la_foto_no_basta(tmp_path, lecturas):
    # Como git: un archivo escrito en el mismo tick del reloj que la foto puede
    # cambiar sin que cambie su stat. Cerca de la foto decide el contenido.
    archivo = tmp_path / 'a.txt'
    archivo.write_text('uno\n')
    esperado = espacio._file(archivo)
    falso = ['0' * 64, *esperado[1:]]  # mismo stat, otro contenido
    estado = archivo.stat()
    lejos = max(estado.st_mtime_ns, estado.st_ctime_ns) + espacio.MARGEN_RACY_NS + 1
    assert espacio._same_file(str(archivo), falso, tomada=lejos)
    assert espacio._same_file(str(archivo), falso)  # fotos de antes, sin hora: decide el stat
    cerca = max(estado.st_mtime_ns, estado.st_ctime_ns)
    assert not espacio._same_file(str(archivo), falso, tomada=cerca)
    assert espacio._same_file(str(archivo), esperado, tomada=cerca)


def test_un_hash_guardado_cerca_de_la_fecha_del_archivo_se_vuelve_a_calcular(tmp_path, lecturas):
    archivo = tmp_path / 'a.txt'
    archivo.write_text('uno\n')
    espacio._file(archivo)
    espacio._file(archivo)
    # Recién escrito: el hash guardado podría ser de otra escritura en el mismo tick.
    assert len(lecturas) == 2


def test_una_carpeta_enorme_que_no_es_un_repositorio_no_tiene_huella(tmp_path, monkeypatch):
    monkeypatch.setattr(espacio, 'MAX_ARCHIVOS_SIN_GIT', 5)
    for i in range(10):
        (tmp_path / f'f{i}.txt').write_text('x\n')
    with pytest.raises(ValueError, match='no es un repositorio git'):
        espacio._capture(tmp_path)


def _turno(tmp_path, raiz, monkeypatch, argv, proposito='Ver el estado del repositorio'):
    monkeypatch.chdir(raiz)
    pasos = [{'role': 'assistant', 'content': '', 'tool_calls': [{'function': {'name': 'run_checks', 'arguments': {
        'argv': argv, 'purpose': proposito}}}]},
             {'role': 'assistant', 'content': 'Hecho.'}]
    vistos = []

    async def request(self, client, endpoint, payload, **kwargs):
        if endpoint == '/api/show':
            return {'capabilities': ['tools'], 'model_info': {'x.context_length': 131072}}
        vistos.append(payload)
        return dict(message=pasos.pop(0) if pasos else {'role': 'assistant', 'content': 'Hecho.'}, done=True,
                    done_reason='stop', prompt_eval_count=900, eval_count=10)
    m = TerminalManager('Mira el estado.', home=tmp_path / 'alfalfa')
    try:
        with patch.object(NativeRuntime, 'request', request):
            asyncio.run(asyncio.wait_for(m.run('Mira el estado.'), 120))
        return historial.leer(m.directory / 'native' / m.task['id'] / 'session.json'), vistos
    finally:
        m.close()


@solo_mac
def test_run_checks_en_un_proyecto_grande_con_el_limite_del_finder(tmp_path, monkeypatch, limite_del_finder):
    raiz = _repo_grande(tmp_path / 'repo')
    estado, _ = _turno(tmp_path, raiz, monkeypatch, ['git', 'status', '--short'])
    check, = estado['checks']
    assert check['exit_code'] == 0, check
    assert check['workspace_stable'] is True


@solo_mac
def test_si_la_huella_falla_la_orden_corre_y_queda_sin_verificar(tmp_path, monkeypatch):
    raiz = _repo_grande(tmp_path / 'repo', carpetas=2, archivos=2)
    marca = tmp_path / 'repo' / '.tmp-alfalfa' / 'corrio.txt'

    def sin_permiso(root):
        raise PermissionError(13, 'Permission denied', str(root))
    monkeypatch.setattr(espacio, '_capture', sin_permiso)
    orden = [sys.executable, '-c', "import pathlib; p = pathlib.Path('.tmp-alfalfa/corrio.txt'); "
             "p.parent.mkdir(exist_ok=True); p.write_text('sí'); assert True"]
    estado, vistos = _turno(tmp_path, raiz, monkeypatch, orden, 'Comprobar que la orden corre')
    assert marca.read_text() == 'sí'
    check, = estado['checks']
    assert check['exit_code'] == 0
    assert check['workspace_stable'] is False and 'workspace_context' not in check
    assert 'No se pudo tomar la huella del workspace antes de la orden' in check['workspace_unverified']
    assert 'Permission denied' in check['workspace_unverified']
    assert check['verification_status'] == 'workspace_unverified'
    # El modelo ve el motivo en el resultado de la herramienta.
    resultado = [m['content'] for m in vistos[-1]['messages'] if m.get('role') == 'tool']
    assert any('No se pudo tomar la huella' in str(c) for c in resultado)
