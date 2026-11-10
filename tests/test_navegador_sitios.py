"""Confianza exacta, alcances, restricciones y permisos de sitios correlacionados."""
import asyncio
import io
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from alfalfa.navegador import Navegador
from alfalfa.navegador_sitios import Sitios, origen
from alfalfa.navegador_sitios_orden import orden
from alfalfa.stdio import Transport


@pytest.mark.parametrize(('url', 'expected'), [
    ('https://EXAMPLE.com./ruta', 'https://example.com:443'),
    ('http://example.com:443/', 'http://example.com:443'),
    ('http://127.1:8000/', 'http://127.0.0.1:8000'),
    ('http://[0:0:0:0:0:0:0:1]:8000/', 'http://[::1]:8000'),
    ('https://faß.de/', 'https://xn--fa-hia.de:443'),
])
def test_exact_origin(url, expected):
    assert origen(url) == expected


@pytest.mark.parametrize('url', ['file:///etc/passwd', 'https://user@example.com',
                                  'https://example.com\\@evil.test', 'https://example.com\n', 'http://x:99999',
                                  'http://example.com:0'])
def test_invalid_origin(url):
    with pytest.raises(ValueError):
        origen(url)


def test_project_can_only_restrict_and_denial_wins(tmp_path):
    root = tmp_path / 'proyecto'
    (root / '.alfalfa').mkdir(parents=True)
    sites = Sitios(tmp_path / 'hogar', root)
    sites.guardar({'permitidos': ['https://a.test', 'https://b.test'], 'estricto': True})
    (root / '.alfalfa/ajustes.json').write_text(json.dumps({'navegador': {'sitios': {
        'permitidos': ['https://a.test', 'https://inventado.test'], 'bloqueados': ['https://a.test'],
        'estricto': False}}}))
    policy = sites.politica()
    assert policy['permitidos'] == []
    assert policy['bloqueados'] == ['https://a.test:443']
    assert policy['estricto'] is True
    assert sites.comprobar('https://inventado.test') == ('https://inventado.test:443', False)
    with pytest.raises(ValueError, match='bloqueado'):
        sites.conceder('https://b.test', 'sesion')
    with pytest.raises(ValueError, match='bloqueado'):
        sites.conceder('https://a.test', 'proyecto')


def test_scopes_do_not_leak_to_other_projects_or_processes(tmp_path):
    home, root = tmp_path / 'home', tmp_path / 'one'
    sites = Sitios(home, root)
    sites.conceder('https://visit.test', 'una_vez')
    sites.conceder('https://session.test', 'sesion')
    sites.conceder('https://saved.test', 'proyecto')
    assert len(sites.politica()['permitidos']) == 2
    assert sites.politica()['visita']['origen'] == 'https://visit.test:443'
    assert Sitios(home, root).politica()['permitidos'] == ['https://saved.test:443']
    assert Sitios(home, tmp_path / 'two').politica()['permitidos'] == []
    assert not sites.comprobar('http://saved.test')[1]
    assert not sites.comprobar('https://saved.test:444')[1]


def test_bad_configuration_fails_closed_and_symlink_is_not_followed(tmp_path):
    sites = Sitios(tmp_path / 'home', tmp_path / 'project')
    sites.guardar({})
    sites.path.write_text('{bad')
    with pytest.raises(ValueError, match='no es válida'):
        sites.politica()
    sites.path.unlink()
    outside = tmp_path / 'outside.json'
    outside.write_text('{"permitidos":["https://evil.test"]}')
    sites.path.symlink_to(outside)
    with pytest.raises(ValueError, match='segura'):
        sites.politica()


def test_recheck_project_after_pending_permission(tmp_path):
    async def exercise():
        root = tmp_path / 'project'
        (root / '.alfalfa').mkdir(parents=True)
        sites = Sitios(tmp_path / 'home', root)

        async def approve(_):
            (root / '.alfalfa/ajustes.json').write_text(
                '{"navegador":{"sitios":{"bloqueados":["https://a.test"]}}}')
            return 'proyecto'

        with pytest.raises(ValueError, match='bloqueado'):
            await sites.autorizar(SimpleNamespace(approve=approve), 'https://a.test')
        assert not sites.path.exists()
    asyncio.run(exercise())


def test_transport_site_scope_cannot_be_added_to_another_permission():
    async def exercise():
        transport = Transport(io.StringIO())
        approval = transport.approval('.', False)
        task = asyncio.create_task(approval({'argv': ['x'], 'purpose': 'prueba'}))
        await asyncio.sleep(0)
        ident = next(iter(transport.pending))
        transport.receive(json.dumps({'tipo': 'permiso', 'id': ident, 'aprobado': True, 'sitio': 'proyecto'}))
        assert not task.done()
        transport.receive(json.dumps({'tipo': 'permiso', 'id': ident, 'aprobado': True}))
        assert await task is None
        task = asyncio.create_task(approval({'argv': ['x'], 'purpose': 'sitio', 'sitio': {'origen': 'https://a.test'}}))
        await asyncio.sleep(0)
        ident = next(iter(transport.pending))
        transport.receive(json.dumps({'tipo': 'permiso', 'id': ident, 'aprobado': True, 'sitio': 'proyecto'}))
        assert await task == 'proyecto'
    asyncio.run(exercise())


def test_redirect_resumes_token_without_repeating_effect_and_keeps_network_separate(tmp_path):
    async def exercise():
        calls, permissions = [], []

        async def approve(request):
            permissions.append(request)
            return 'sesion'

        async def app(action, fields, _):
            calls.append((action, fields))
            if action == 'pulsar':
                return {'ok': False, 'permiso': {'tipo': 'sitio', 'url': 'https://new.test/path', 'token': 'pending'},
                        'resultado': {'url': 'https://old.test/'}}
            return {'ok': True, 'resultado': {'url': 'https://new.test/path', 'titulo': 'Nuevo'}}

        runtime = SimpleNamespace(manager=SimpleNamespace(home=tmp_path / 'home', read_only=False),
                                  tools=SimpleNamespace(root=tmp_path), task={'modo': 'auto'}, approve=approve)
        browser = Navegador(app)
        browser.url = 'https://old.test/'
        browser.sitios(runtime).conceder(browser.url, 'sesion')
        with patch('alfalfa.navegador._resolver', return_value=[]):
            reply = await browser.llamar(runtime, 'browser_click', {'ref': 'e1'})
        assert reply['titulo'] == 'Nuevo'
        assert [action for action, _ in calls] == ['pulsar', 'reanudar_sitio']
        assert calls[-1][1]['token'] == 'pending'
        assert calls[-1][1]['red_local'] == []
        assert permissions[0]['sitio']['origen'] == 'https://new.test:443'
    asyncio.run(exercise())


def test_site_denial_precedes_dns_and_page_contact(tmp_path):
    async def exercise():
        async def app(*_):
            pytest.fail('No debe contactar con la app para un sitio denegado.')

        async def approve(_):
            raise ValueError('Rechazado')

        runtime = SimpleNamespace(manager=SimpleNamespace(home=tmp_path / 'home', read_only=False),
                                  tools=SimpleNamespace(root=tmp_path), task={'modo': 'plan'}, approve=approve)
        with patch('alfalfa.navegador._resolver') as dns:
            with pytest.raises(ValueError, match='Rechazado'):
                await Navegador(app).llamar(runtime, 'browser_navigate', {'url': 'https://a.test'})
            dns.assert_not_called()
    asyncio.run(exercise())


def test_settings_write_requires_matching_approval_and_rechecks_snapshot(tmp_path):
    for approved in (False, True):
        output = io.StringIO()
        lines = iter([json.dumps({'tipo': 'configurar_sitios', 'permitidos': ['https://saved.test']}) + '\n',
                      json.dumps({'tipo': 'permiso', 'id': 'fixed', 'aprobado': approved}) + '\n', ''])
        entry = SimpleNamespace(readline=lambda *_: next(lines))
        with patch('alfalfa.navegador_sitios_orden.uuid4', return_value=SimpleNamespace(hex='fixed')):
            assert orden(SimpleNamespace(stdio=True), tmp_path / str(approved), entrada=entry, salida=output,
                         root=tmp_path) == 0
        sites = Sitios(tmp_path / str(approved), tmp_path)
        assert sites.configuracion()['permitidos'] == (['https://saved.test:443'] if approved else [])
        assert len([line for line in output.getvalue().splitlines() if '"tipo": "permiso"' in line]) == 1


@pytest.mark.parametrize('mode', ['auto', 'manual', 'ediciones', 'plan', 'omitir'])
def test_hard_denial_is_enforced_in_every_mode_before_dns(tmp_path, mode):
    async def exercise():
        async def app(*_):
            pytest.fail('Un sitio bloqueado no debe llegar al navegador.')

        async def approve(_):
            pytest.fail('Un sitio bloqueado no ofrece una excepción al agente.')

        runtime = SimpleNamespace(manager=SimpleNamespace(home=tmp_path / 'home', read_only=False),
                                  tools=SimpleNamespace(root=tmp_path), task={'modo': mode}, approve=approve)
        browser = Navegador(app)
        browser.sitios(runtime).guardar({'bloqueados': ['https://denied.test']})
        with patch('alfalfa.navegador._resolver') as dns:
            with pytest.raises(ValueError, match='bloqueado'):
                await browser.llamar(runtime, 'browser_navigate', {'url': 'https://denied.test'})
            dns.assert_not_called()
    asyncio.run(exercise())


def test_rejected_redirect_cancels_only_its_pending_token(tmp_path):
    async def exercise():
        calls = []

        async def app(action, fields, _):
            calls.append((action, fields))
            if action == 'cancelar_sitio':
                return {'ok': True}
            return {'ok': False, 'permiso': {'tipo': 'sitio', 'url': 'https://next.test/', 'token': 'token-propio'}}

        async def approve(_):
            raise ValueError('Rechazado por la persona')

        runtime = SimpleNamespace(manager=SimpleNamespace(home=tmp_path / 'home', read_only=False),
                                  tools=SimpleNamespace(root=tmp_path), task={'modo': 'auto'}, approve=approve)
        browser = Navegador(app)
        with pytest.raises(ValueError, match='Rechazado'):
            await browser.llamar(runtime, 'browser_click', {'ref': 'e1'})
        assert [action for action, _ in calls] == ['pulsar', 'cancelar_sitio']
        assert calls[-1][1] == {'token': 'token-propio'}
    asyncio.run(exercise())


def test_site_approval_does_not_approve_local_network(tmp_path):
    async def exercise():
        requests = []

        async def approve(request):
            requests.append(request)
            if 'sitio' in request:
                return 'proyecto'
            raise ValueError('No autoriza la red local')

        async def app(*_):
            pytest.fail('No se aprobó el acceso a la red local.')

        runtime = SimpleNamespace(manager=SimpleNamespace(home=tmp_path / 'home', read_only=False),
                                  tools=SimpleNamespace(root=tmp_path), task={'modo': 'auto'}, approve=approve)
        browser = Navegador(app)
        with pytest.raises(ValueError, match='usuario no lo aprobó'):
            await browser.llamar(runtime, 'browser_navigate', {'url': 'http://127.0.0.1:18486'})
        assert len(requests) == 2
        assert browser.origenes_red_local(runtime) == []
        assert browser.politica_sitios(runtime)['permitidos'] == ['http://127.0.0.1:18486']
    asyncio.run(exercise())


def test_consumed_visit_from_executor_forces_new_human_decision(tmp_path):
    async def exercise():
        decisions, calls = [], []
        lease = None

        async def approve(request):
            decisions.append(request)
            return 'una_vez'

        async def app(action, fields, _):
            calls.append((action, fields))
            assert fields['sitios']['permitidos'] == [], 'Un lease no entra en los permisos permanentes.'
            if action == 'abrir':
                return {'ok': False, 'permiso': {'tipo': 'sitio', 'url': 'https://once.test/again', 'token': 'pending'},
                        'resultado': {'url': 'https://other.test/', 'visita_consumida': lease}}
            assert fields['sitios']['visita']['id'] != lease
            return {'ok': True, 'resultado': {'url': 'https://once.test/again'}}

        runtime = SimpleNamespace(manager=SimpleNamespace(home=tmp_path / 'home', read_only=False),
                                  tools=SimpleNamespace(root=tmp_path), task={'modo': 'auto'}, approve=approve)
        browser = Navegador(app)
        browser.sitios(runtime).conceder('https://once.test', 'una_vez')
        lease = browser.sitios(runtime).visita_id
        with patch('alfalfa.navegador._resolver', return_value=[]):
            await browser.llamar(runtime, 'browser_navigate', {'url': 'https://once.test/again'})
        assert len(decisions) == 1
        assert [action for action, _ in calls] == ['abrir', 'reanudar_sitio']
    asyncio.run(exercise())


def test_rejecting_a_different_site_preserves_the_current_visit(tmp_path):
    async def exercise():
        async def approve(_):
            raise ValueError('Rechazado')

        async def app(*_):
            pytest.fail('No debe intentar la navegación rechazada.')

        runtime = SimpleNamespace(manager=SimpleNamespace(home=tmp_path / 'home', read_only=False),
                                  tools=SimpleNamespace(root=tmp_path), task={'modo': 'auto'}, approve=approve)
        browser = Navegador(app)
        browser.sitios(runtime).conceder('https://once.test', 'una_vez')
        lease = browser.sitios(runtime).visita_id
        with pytest.raises(ValueError, match='Rechazado'):
            await browser.llamar(runtime, 'browser_navigate', {'url': 'https://other.test'})
        assert browser.politica_sitios(runtime)['visita'] == {'id': lease, 'origen': 'https://once.test:443'}
    asyncio.run(exercise())


def test_invalid_browser_project_configuration_fails_closed(tmp_path):
    (tmp_path / '.alfalfa').mkdir()
    (tmp_path / '.alfalfa/ajustes.json').write_text('{"navegador": []}')
    with pytest.raises(ValueError, match='debe ser un objeto'):
        Sitios(tmp_path / 'home', tmp_path).politica()


def test_oversized_rules_do_not_replace_previous_configuration(tmp_path):
    sites = Sitios(tmp_path / 'home', tmp_path)
    sites.guardar({'permitidos': ['https://saved.test']})
    long_host = '.' + 'b' * 60 + '.' + 'c' * 60 + '.test'
    with pytest.raises(ValueError, match='64 KiB'):
        sites.guardar({'permitidos': ['https://a' + str(i) + 'a' * 55 + long_host for i in range(200)],
                       'bloqueados': ['https://d' + str(i) + 'd' * 55 + long_host for i in range(200)]})
    assert sites.configuracion()['permitidos'] == ['https://saved.test:443']
