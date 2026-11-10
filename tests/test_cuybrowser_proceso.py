"""Lifecycle sin procesos reales: pertenencia, exclusión y política cerrada antes de Chromium."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from alfalfa import cuybrowser_proceso as module
from alfalfa.cuybrowser_proceso import ProcesoCuyBrowser
from alfalfa.navegador import Navegador
from tests.test_navegador_cuybrowser import Api


@pytest.fixture
def api(monkeypatch):
    result = Api()
    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: real(transport=httpx.MockTransport(result), **kwargs))
    return result


def runtime(tmp_path):
    root = tmp_path / 'proyecto'
    root.mkdir()
    home = tmp_path / 'alfalfa-propio'
    home.mkdir(mode=0o700)
    approvals = []

    async def approve(request):
        approvals.append(request)

    return SimpleNamespace(tools=SimpleNamespace(root=root), manager=SimpleNamespace(home=home),
                           task={}, approve=approve), approvals


def test_no_adopta_ni_detiene_daemon_anterior(tmp_path, monkeypatch):
    rt, approvals = runtime(tmp_path)
    calls = []

    def ipc(home, kind='ping', expected=None):
        calls.append(kind)
        return {'pid': 12345, 'type': 'pong'}

    monkeypatch.setattr(module, '_ipc', ipc)
    monkeypatch.setattr(module, '_port', lambda: 18449)
    process = ProcesoCuyBrowser()
    with pytest.raises(ValueError, match='no se adoptará ni detendrá'):
        asyncio.run(process.start(rt, Navegador._preguntar))
    assert calls == ['ping'] and not approvals
    assert process.lock is None and process.pid is None


@pytest.fixture
def launched(monkeypatch, api):
    state = {'running': False, 'shutdown': [], 'children': []}

    class Child:
        def __init__(self, argv, **kwargs):
            state['children'].append(self)
            state['running'] = True
            self.returncode = None
            self.argv = argv
            self.env = kwargs['env']
            home = Path(self.env['CUYBROWSER_HOME'])
            (home / 'token').write_text('a' * 64)
            (home / 'token').chmod(0o600)
            (home / 'policy.yaml').write_text('approval: native-dialog\n')
            (home / 'policy.yaml').chmod(0o600)

        def poll(self):
            return self.returncode

        def terminate(self):
            self.returncode = 0

        def wait(self, timeout):
            return self.returncode

    def ipc(home, kind='ping', expected=None):
        if kind == 'shutdown':
            state['shutdown'].append(expected)
            state['running'] = False
        return {'pid': 6789, 'type': 'pong'} if state['running'] else None

    monkeypatch.setattr(module, '_ipc', ipc)
    monkeypatch.setattr(module.shutil, 'which', lambda _: '/opt/homebrew/bin/cuybrowser')
    monkeypatch.setattr(module.subprocess, 'Popen', Child)
    monkeypatch.setattr(module, '_port', lambda: 18449)
    return state


def test_cierra_solo_sus_procesos_y_conserva_perfil(tmp_path, launched, api):
    rt, approvals = runtime(tmp_path)
    process = ProcesoCuyBrowser('propio')
    asyncio.run(process.start(rt, Navegador._preguntar))
    assert len(approvals) == 1 and approvals[0]['motivo'] == 'navegador'
    assert launched['children'][0].argv[1:3] == ['serve', '--http']
    assert launched['children'][0].env['CUYBROWSER_HOME'] == str(process.home)
    assert json.loads((process.home / 'policy.yaml').read_text())['origins'] == {'allow': [], 'deny': []}
    assert process.home != process.staging and not process.staging.is_relative_to(process.home)
    assert [p for t, p in api.calls if t == 'start'] == [{'mode': 'launch', 'profile': 'propio', 'headless': False}]
    marker = process.home / 'login-falso'
    marker.write_text('estado propio persistente')
    asyncio.run(process.start(rt, Navegador._preguntar))
    assert len(approvals) == 1 and len(launched['children']) == 1
    process.close()
    assert launched['shutdown'] == [6789]
    assert launched['children'][0].returncode == 0 and process.lock is None
    assert marker.read_text() == 'estado propio persistente'


def test_lock_rechaza_segunda_sesion_y_no_toca_primera(tmp_path, launched, api):
    rt, approvals = runtime(tmp_path)
    first, second = ProcesoCuyBrowser(), ProcesoCuyBrowser()
    asyncio.run(first.start(rt, Navegador._preguntar))
    try:
        with pytest.raises(ValueError, match='Otra sesión ocupa'):
            asyncio.run(second.start(rt, Navegador._preguntar))
        assert not launched['shutdown'] and len(launched['children']) == 1 and len(approvals) == 1
    finally:
        first.close()


def test_falta_capacidad_no_abre_chromium(tmp_path, launched, api):
    rt, _ = runtime(tmp_path)
    api.capabilities = {}
    process = ProcesoCuyBrowser()
    with pytest.raises(ValueError, match='no se abrió Chromium'):
        asyncio.run(process.start(rt, Navegador._preguntar))
    assert 'start' not in [t for t, _ in api.calls]
    assert launched['shutdown'] == [6789] and process.lock is None


def test_estricto_incompatible_no_abre_chromium(tmp_path, launched, api):
    rt, _ = runtime(tmp_path)
    process = ProcesoCuyBrowser()
    with pytest.raises(ValueError, match='estricto de subrecursos'):
        asyncio.run(process.start(rt, Navegador._preguntar, strict=True))
    assert 'start' not in [t for t, _ in api.calls]
    assert launched['shutdown'] == [6789] and process.lock is None


def test_no_reutiliza_permisos_ni_perfil_entre_proyectos(tmp_path, launched, api):
    rt, _ = runtime(tmp_path)
    process = ProcesoCuyBrowser()
    asyncio.run(process.start(rt, Navegador._preguntar))
    process.close()
    other = tmp_path / 'otro-proyecto'
    other.mkdir()
    rt.tools.root = other
    with pytest.raises(ValueError, match='otro proyecto'):
        asyncio.run(process.start(rt, Navegador._preguntar))
    assert len(launched['children']) == 1


def test_binario_del_proyecto_no_se_ejecuta(tmp_path, monkeypatch):
    rt, approvals = runtime(tmp_path)
    monkeypatch.setattr(module, '_ipc', lambda *_: None)
    monkeypatch.setattr(module.shutil, 'which', lambda _: str(rt.tools.root / 'cuybrowser'))
    process = ProcesoCuyBrowser()
    with pytest.raises(ValueError, match='procedente del proyecto'):
        asyncio.run(process.start(rt, Navegador._preguntar))
    assert not approvals and process.lock is None


@pytest.mark.parametrize('expected,shutdowns', [(123, 1), (456, 0)])
def test_shutdown_comprueba_pid_en_misma_conexion(tmp_path, monkeypatch, expected, shutdowns):
    sent = []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def settimeout(self, _):
            pass

        def connect(self, path):
            assert path == str(tmp_path / 'daemon.sock')

        def makefile(self, mode):
            return self

        def write(self, value):
            sent.append(json.loads(value))

        def flush(self):
            pass

        def readline(self, limit):
            return b'{"id":1,"type":"pong","pid":123}\n'

    monkeypatch.setattr(module.socket, 'socket', lambda *_: Connection())
    module._ipc(tmp_path, 'shutdown', expected)
    assert len([m for m in sent if m['type'] == 'shutdown']) == shutdowns
