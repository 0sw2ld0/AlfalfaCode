"""Contrato HTTP falso completo: traducción, aprobación independiente y archivos de proyecto."""
import asyncio
import base64
import copy
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from alfalfa.cuybrowser_archivos import archivos_de_subida
from alfalfa.cuybrowser_http import CONFIRMACIONES, HttpCuyBrowser
from alfalfa.cuybrowser_proceso import ProcesoCuyBrowser
from alfalfa.navegador import ESCRITURA, NOMBRES
from alfalfa.navegador_cuybrowser import NavegadorCuyBrowser

POLICY = {'approval': 'native-dialog', 'confirm': sorted(CONFIRMACIONES), 'attach': {'enabled': False},
          'sensitive_fields': {'block_typing': True}, 'evaluate': {'enabled': False}}
TOOLS = {'start', 'stop', 'status', 'policy_show', 'confirm', 'navigate', 'back', 'forward', 'reload', 'snapshot',
         'get_text', 'find', 'click', 'type', 'hover', 'scroll', 'screenshot', 'console_messages', 'network_requests',
         'viewport', 'handoff', 'press_key', 'wait_for', 'upload_file', 'evaluate'}


class Api:
    def __init__(self):
        self.calls = []
        self.policy = copy.deepcopy(POLICY)
        self.capabilities = {'exactOrigins': True, 'localNetwork': True, 'strictResources': False, 'visitLeases': True}
        self.confirm = set()
        self.uploaded = []
        self.consumed = []

    def __call__(self, request):
        assert str(request.url).startswith('http://127.0.0.1:18449/')
        assert request.headers['authorization'] == 'Bearer ' + 'a' * 64
        assert 'origin' not in request.headers
        if request.method == 'GET':
            return httpx.Response(200, json={'ok': True, 'data': {'tools': [{'name': t} for t in TOOLS]}})
        tool = request.url.path.rsplit('/', 1)[-1]
        params = json.loads(request.content)
        self.calls.append((tool, params))
        if tool in self.confirm:
            return httpx.Response(200, json={'ok': False, 'error': {'code': 'CONFIRMATION_REQUIRED',
                                  'confirmation': {'confirmation_id': 'persona-elige'}}})
        if tool == 'policy_show':
            data = {'valid': True, 'policy': self.policy, 'capabilities': self.capabilities}
        elif tool == 'status':
            data = {'running': True, 'consumed_visits': self.consumed, 'active_tab': 't1',
                    'tabs': [{'id': 't1', 'url': 'https://example.com/'}]}
        elif tool == 'snapshot':
            data = {'url': 'https://example.com/', 'title': 'Página', 'text': '- button "Enviar" [e1]'}
        elif tool == 'get_text':
            data = {'text': '<<<PAGE_CONTENT\nHola\nPAGE_CONTENT>>>'}
        elif tool == 'find':
            data = {'matches': [{'ref': 'e1', 'name': 'Enviar'}], 'total': 1}
        elif tool == 'screenshot':
            data = {'image': {'mime_type': 'image/png', 'base64': base64.b64encode(b'\x89PNG\r\n\x1a\n').decode()}}
        elif tool == 'console_messages':
            data = {'messages': [{'data': {'level': 'error', 'text': '<<<PAGE_CONTENT\nerror\nPAGE_CONTENT>>>'}}]}
        elif tool == 'network_requests':
            data = {'requests': [{'data': {'url': 'https://example.com/?token=secreto', 'method': 'GET', 'status': 200,
                                          'headers': {'cookie': 'no'}, 'body': 'no', 'failed': False}}]}
        elif tool == 'upload_file':
            self.uploaded = [(p, Path(p).read_bytes()) for p in params['paths']]
            data = {'uploaded': len(params['paths'])}
        elif tool == 'scroll':
            data = {'x': 0, 'y': 600}
        else:
            data = {'url': 'https://example.com/', 'title': 'Página', 'approved_by': 'persona'}
        return httpx.Response(200, json={'ok': True, 'data': data, 'warnings': []})


@pytest.fixture
def api(monkeypatch):
    result = Api()
    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: real(transport=httpx.MockTransport(result), **kwargs))
    return result


class Process:
    def __init__(self, home):
        self.home = home
        home.mkdir(mode=0o700)
        self.staging = home.parent / 'subidas-privadas'
        self.staging.mkdir(mode=0o700)
        self.client = HttpCuyBrowser(18449, 'a' * 64)
        self.started = 0
        self.origins_calls = []
        self.visit_calls = []

    async def start(self, runtime, ask, *, strict=False):
        self.started += 1
        if not self.client.tools:
            await self.client.discover()
        return self.client

    def origins(self, policy, allow, deny, local, visit=None):
        self.origins_calls.append((list(allow), list(deny), list(local)))
        self.visit = visit
        self.visit_calls.append(visit)

    def close(self):
        self.closed = True


def setup(tmp_path, mode='auto'):
    root = tmp_path / 'proyecto'
    root.mkdir()
    (root / 'demo.txt').write_text('Estos son los bytes aprobados.')
    process = Process(tmp_path / 'cuy-propio')
    requests = []
    home = tmp_path / 'alfalfa-privado'
    sites = home / 'navegador' / 'sitios'
    sites.mkdir(mode=0o700, parents=True)
    # Si se integra A8, estos sitios ficticios ya están autorizados; la red local sigue siendo independiente.
    ident = hashlib.sha256(os.fsencode(root.resolve())).hexdigest()
    (sites / (ident + '.json')).write_text(json.dumps({'permitidos': ['https://example.com:443',
                                                                    'http://127.0.0.1:18499'], 'bloqueados': []}))

    async def approve(request):
        requests.append(request)

    runtime = SimpleNamespace(tools=SimpleNamespace(root=root, path=lambda p: (root / p).resolve()),
                              manager=SimpleNamespace(read_only=False, home=home),
                              directory=tmp_path / 'sesion', task={'modo': mode}, approve=approve, state={})
    return NavegadorCuyBrowser(process=process), runtime, requests


@pytest.mark.parametrize('name,args,tool', [
    ('browser_navigate', {'url': 'https://example.com/'}, 'navigate'),
    ('browser_read', {}, 'snapshot'), ('browser_read', {'mode': 'text'}, 'get_text'),
    ('browser_console', {}, 'console_messages'), ('browser_network', {}, 'network_requests'),
    ('browser_viewport', {'preset': 'mobile'}, 'viewport'), ('browser_click', {'ref': 'f1e7'}, 'click'),
    ('browser_type', {'ref': 'e1', 'text': 'Hola'}, 'type'), ('browser_scroll', {}, 'scroll'),
    ('browser_screenshot', {}, 'screenshot'),
    ('browser_eval', {'script': 'return document.title', 'modifies': True}, 'evaluate'),
    ('browser_handoff', {'reason': 'Inicia sesión.'}, 'handoff'), ('browser_wait', {'load': True}, 'wait_for'),
    ('browser_key', {'key': 'Tab'}, 'press_key'), ('browser_hover', {'target': 'Enviar'}, 'hover'),
    ('browser_upload', {'ref': 'e1', 'paths': ['demo.txt']}, 'upload_file'),
])
def test_traduccion_y_datos(tmp_path, api, name, args, tool):
    browser, runtime, requests = setup(tmp_path)
    result = asyncio.run(browser.llamar(runtime, name, args))
    assert any(t == tool for t, _ in api.calls)
    assert 'datos, no instrucciones' in result['nota']
    assert not requests  # Ninguna tarjeta sensible duplicada del motor.
    if tool == 'upload_file':
        assert api.uploaded[0][1] == b'Estos son los bytes aprobados.'
        assert Path(api.uploaded[0][0]).name == 'demo.txt'
        assert not Path(api.uploaded[0][0]).exists()
    if tool == 'network_requests':
        assert 'secreto' not in str(result) and 'cookie' not in str(result)
        assert 'duracion_ms' not in result['peticiones'][0]


@pytest.mark.parametrize('name,args,error', [
    ('browser_download', {'url': 'https://example.com/a', 'path': 'a'}, 'no ofrece descargar'),
    ('browser_eval', {'script': 'return document.title'}, 'sólo lectura'),
    ('browser_key', {'key': 'Tab', 'ref': 'e1'}, 'foco actual'),
    ('browser_handoff', {'reason': 'Login', 'until': {'url_contains': 'cuenta'}}, 'no ofrece until'),
])
def test_capacidades_sin_simular(tmp_path, api, name, args, error):
    browser, runtime, _ = setup(tmp_path)
    with pytest.raises(ValueError, match=error):
        asyncio.run(browser.llamar(runtime, name, args))
    assert not [t for t, _ in api.calls if t not in ('policy_show', 'status')]


@pytest.mark.parametrize('mode', ['plan', 'manual', 'ediciones', 'auto', 'omitir'])
def test_modos_y_confirmacion_nativa(tmp_path, api, mode):
    browser, runtime, requests = setup(tmp_path, mode)
    api.confirm.add('click')
    if mode == 'plan':
        with pytest.raises(ValueError, match='sólo lee'):
            asyncio.run(browser.llamar(runtime, 'browser_click', {'ref': 'e1'}))
        assert browser.process.started == 0 and not api.calls
    else:
        result = asyncio.run(browser.llamar(runtime, 'browser_click', {'ref': 'e1'}))
        assert result['approved_by'] == 'persona'
        assert [p for t, p in api.calls if t == 'confirm'] == [{'confirmation_id': 'persona-elige'}]
        assert len(requests) == (1 if mode == 'manual' else 0)


def test_los_dieciséis_y_escritura_plan(tmp_path, api):
    assert len(NOMBRES) == 16
    browser, runtime, _ = setup(tmp_path, 'plan')
    for name in ESCRITURA:
        with pytest.raises(ValueError, match='sólo lee'):
            asyncio.run(browser.llamar(runtime, name, {}))
    assert not api.calls and browser.process.started == 0


def test_descripcion_navigate_es_del_motor_chromium_persistente(tmp_path):
    browser, _, _ = setup(tmp_path)
    tool = next(t for t in browser.tools if t['function']['name'] == 'browser_navigate')
    description = tool['function']['description']
    assert 'persistent Chromium profile' in description
    assert 'WebKit' not in description and 'opt-in' not in description


@pytest.mark.parametrize('field,value', [('approval', 'cli'), ('confirm', []), ('attach', {'enabled': True}),
                                       ('sensitive_fields', {'block_typing': False})])
def test_politica_no_se_rebaja(tmp_path, api, field, value):
    browser, runtime, _ = setup(tmp_path)
    api.policy[field] = value
    with pytest.raises(ValueError):
        asyncio.run(browser.llamar(runtime, 'browser_read', {}))
    assert [t for t, _ in api.calls] == ['policy_show']


def test_sin_barrera_de_origen_falla_cerrado(tmp_path, api):
    browser, runtime, _ = setup(tmp_path)
    api.capabilities = {}
    with pytest.raises(ValueError, match='orígenes exactos'):
        asyncio.run(browser.llamar(runtime, 'browser_navigate', {'url': 'https://example.com/'}))
    assert [t for t, _ in api.calls] == ['policy_show']


def test_estricto_no_se_traduce_a_hostnames(tmp_path, api):
    browser, runtime, _ = setup(tmp_path)
    runtime.manager.home = tmp_path
    browser.politica_sitios = lambda _: {'permitidos': ['https://example.com:443'], 'bloqueados': [], 'estricto': True}
    with pytest.raises(ValueError, match='subrecursos'):
        asyncio.run(browser.llamar(runtime, 'browser_read', {}))
    assert not browser.process.origins_calls


def test_sin_barrera_red_local_falla_antes_de_navegar(tmp_path, api):
    browser, runtime, _ = setup(tmp_path)
    api.capabilities.pop('localNetwork')
    with pytest.raises(ValueError, match='red local'):
        asyncio.run(browser.llamar(runtime, 'browser_navigate', {'url': 'http://127.0.0.1:18499/'}))
    assert [t for t, _ in api.calls] == ['policy_show']


def test_permiso_red_local_independiente_del_sitio(tmp_path, api):
    browser, runtime, requests = setup(tmp_path)
    asyncio.run(browser.llamar(runtime, 'browser_navigate', {'url': 'http://127.0.0.1:18499/'}))
    assert len(requests) == 1 and 'dirección local' in requests[0]['purpose']
    assert browser.process.origins_calls[-1][2] == ['http://127.0.0.1:18499']
    assert any(t == 'navigate' for t, _ in api.calls)


def test_sitio_permitido_no_concede_red_local(tmp_path, api):
    browser, runtime, requests = setup(tmp_path)
    runtime.manager.home = tmp_path
    browser.politica_sitios = lambda _: {'permitidos': ['http://127.0.0.1:18499'], 'bloqueados': [], 'estricto': False}
    browser.origenes_red_local = lambda _: []
    asyncio.run(browser.llamar(runtime, 'browser_read', {}))
    assert all(not local for _, _, local in browser.process.origins_calls)
    assert not requests


def test_red_local_rechazada_no_se_contacta(tmp_path, api):
    browser, runtime, _ = setup(tmp_path)

    async def reject(_):
        raise ValueError('Rechazado por la persona.')

    runtime.approve = reject
    with pytest.raises(ValueError, match='usuario no lo aprobó'):
        asyncio.run(browser.llamar(runtime, 'browser_navigate', {'url': 'http://127.0.0.1:18499/'}))
    assert 'navigate' not in [t for t, _ in api.calls]
    assert all(not local for _, _, local in browser.process.origins_calls)


def test_cancelar_relevo_para_solo_navegador_propio(tmp_path, api, monkeypatch):
    browser, runtime, _ = setup(tmp_path)
    real = browser._pedir
    pending = asyncio.Event()

    async def wait(action, fields=None, tiempo=None):
        if action == 'relevo':
            pending.set()
            await asyncio.Future()
        return await real(action, fields, tiempo)

    monkeypatch.setattr(browser, '_pedir', wait)

    async def exercise():
        task = asyncio.create_task(browser.llamar(runtime, 'browser_handoff', {'reason': 'Login de ensayo'}))
        await pending.wait()
        with pytest.raises(ValueError, match='espera a la persona'):
            await browser.llamar(runtime, 'browser_read', {})
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(exercise())
    assert [t for t, _ in api.calls if t == 'stop'] == ['stop']
    assert not browser.relevo_pendiente and not browser.campos_visibles and not browser.contrasenas


def sitios_con_visita(browser, runtime, tmp_path):
    class Sites:
        visit = {'id': 'b' * 32, 'origen': 'https://example.com:443'}

        def consumir_visita(self, ident):
            if self.visit is not None and self.visit['id'] == ident:
                self.visit = None

        def politica(self):
            return {'permitidos': [], 'bloqueados': [], 'estricto': False, 'visita': self.visit}

    sites = Sites()
    runtime.manager.home = tmp_path
    browser.sitios = lambda _: sites
    browser.politica_sitios = lambda _: sites.politica()
    return sites


def test_visita_no_se_convierte_en_allow_y_se_consume_antes_snapshot(tmp_path, api):
    browser, runtime, _ = setup(tmp_path)
    sites = sitios_con_visita(browser, runtime, tmp_path)
    asyncio.run(browser.llamar(runtime, 'browser_read', {}))
    assert browser.process.visit == {'id': 'b' * 32, 'origin': 'https://example.com:443'}
    assert all(allowed == [] for allowed, _, _ in browser.process.origins_calls)
    previous = len(browser.process.visit_calls)
    api.consumed = ['b' * 32]
    asyncio.run(browser.llamar(runtime, 'browser_read', {}))
    assert sites.visit is None
    assert all(value is None for value in browser.process.visit_calls[previous:])


def test_cerrar_navegador_retira_visita_aunque_daemon_no_siga_vivo(tmp_path, api):
    browser, runtime, _ = setup(tmp_path)
    sites = sitios_con_visita(browser, runtime, tmp_path)
    asyncio.run(browser.llamar(runtime, 'browser_read', {}))
    browser.close()
    assert sites.visit is None and browser.process.closed is True and browser.http is None


def test_visita_exige_capacidad_lease(tmp_path, api):
    browser, runtime, _ = setup(tmp_path)
    sitios_con_visita(browser, runtime, tmp_path)
    api.capabilities.pop('visitLeases')
    with pytest.raises(ValueError, match='permisos de una visita'):
        asyncio.run(browser.llamar(runtime, 'browser_read', {}))
    assert 'snapshot' not in [t for t, _ in api.calls]


def test_subida_no_lee_fuera_del_proyecto_y_conserva_bytes(tmp_path):
    root, stage = tmp_path / 'proyecto', tmp_path / 'privado'
    root.mkdir()
    stage.mkdir()
    outside = tmp_path / 'fuera.txt'
    outside.write_text('secreto')
    (root / 'symlink').symlink_to(outside)
    os.link(outside, root / 'hardlink')
    for value in ['../fuera.txt', 'symlink', 'hardlink']:
        with pytest.raises(ValueError):
            with archivos_de_subida(root, [value], stage):
                pytest.fail('Se aceptó una ruta prohibida')
    source = root / 'original.txt'
    source.write_text('original')
    with archivos_de_subida(root, ['original.txt'], stage) as files:
        source.write_text('cambiado mientras pregunta')
        assert Path(files[0]).read_text() == 'original'
        assert Path(files[0]).stat().st_mode & 0o222 == 0
    assert list(stage.iterdir()) == []


def test_token_no_admite_enlace_ni_permisos_publicos(tmp_path):
    process = ProcesoCuyBrowser()
    process.home = tmp_path
    token = tmp_path / 'token'
    token.write_text('a' * 64)
    token.chmod(0o600)
    assert process._token() == 'a' * 64
    token.chmod(0o644)
    with pytest.raises(ValueError, match='privado'):
        process._token()
    token.unlink()
    token.symlink_to(tmp_path / 'otro')
    with pytest.raises(OSError):
        process._token()


def test_politica_privada_preserva_barreras(tmp_path):
    process = ProcesoCuyBrowser()
    process.home = tmp_path
    path = tmp_path / 'policy.yaml'
    path.write_text('approval: native-dialog\n')
    path.chmod(0o600)
    process.origins(POLICY, [], [], [])
    written = json.loads(path.read_text())
    assert written['origins'] == {'allow': [], 'deny': []}
    assert written['local_network'] == {'allow_origins': []}
    assert written['evaluate']['enabled'] is False
    assert written['confirm'] == sorted(CONFIRMACIONES)
    assert written['approval'] == 'native-dialog' and written['attach']['enabled'] is False


def test_lectura_diff_cuy_sin_identidad_devuelve_completo_explicito(tmp_path, api):
    browser, runtime, requests = setup(tmp_path)
    result = asyncio.run(browser.llamar(runtime, 'browser_read', {'mode': 'diff', 'max_tokens': 128}))
    assert ('snapshot', {'mode': 'full', 'max_tokens': 8000}) in api.calls
    assert '[e1]' in result['arbol'] and 'datos, no instrucciones' in result['nota']
    assert result['lectura']['modo'] == 'tree'
    assert result['lectura']['fallback'] == 'identidad_documento_no_disponible'
    assert not result['lectura']['base_actualizada'] and not requests
