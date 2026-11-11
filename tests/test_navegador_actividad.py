"""Actividad real del motor con transportes, hogares y páginas ficticios."""
import asyncio
import base64
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from alfalfa import navegador
from alfalfa import navegador_actividad as actividad
from alfalfa import navegador_actividad_cuy as cuy
from alfalfa.cuybrowser_http import ErrorCuyBrowser
from alfalfa.navegador import TOOLS, Navegador
from alfalfa.permisos import modos, sesion
from alfalfa.proveedores.nativo.herramientas import WorkspaceTools

PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/lxsAAAAASUVORK5CYII=')
DAEMON = '11111111-1111-4111-8111-111111111111'
OTRO = '22222222-2222-4222-8222-222222222222'


class App:
    def __init__(self, permisos=None):
        self.permisos = permisos or {}

    async def __call__(self, accion, campos, _tiempo):
        tipo = self.permisos.get(accion)
        if tipo and tipo not in campos.get('permitido', []):
            return {'ok': False, 'permiso': {'tipo': tipo, 'detalle': 'Formulario de ensayo'}}
        if accion == 'captura':
            Path(campos['ruta']).write_bytes(PNG)
        return {'ok': True, 'resultado': {'url': campos.get('url') or 'https://ejemplo.test/',
                                          'arbol': '- heading "Ensayo"'}}


def preparar(tmp_path, respuestas=(), modo='auto'):
    ident = 'actividad-demo'
    home = tmp_path / 'motor'
    home.mkdir(mode=0o700)
    base = home
    for component in ('sesiones', ident, 'native', ident):
        base /= component
        base.mkdir(mode=0o700)
    replies, requests = list(respuestas), []

    async def approve(request):
        requests.append(request)
        reply = replies.pop(0)
        if reply == 'no':
            raise ValueError('Permiso rechazado.')
        return reply

    return SimpleNamespace(task={'id': ident, 'modo': modo}, manager=SimpleNamespace(home=home, read_only=False),
                           tools=SimpleNamespace(root=tmp_path), directory=base, state={}, approve=approve,
                           pedidos=requests, tool_specs=TOOLS, excluded_tools=set())


def registro(runtime):
    return json.loads((runtime.directory / actividad.ARCHIVO).read_text())


def test_registra_decisiones_de_sitio_red_local_y_formulario_sin_secreto(tmp_path):
    rt = preparar(tmp_path, ['una_vez', sesion.SESION, None, 'no'])
    browser = Navegador(App(permisos={'pulsar': 'formulario'}))

    async def acciones():
        url = 'http://127.0.0.1:18488/ruta-privada?token=CLAVE_PRIVADA#FRAGMENTO_PRIVADO'
        await actividad.ejecutar(rt, browser, 'browser_navigate', {'url': url})
        await actividad.ejecutar(rt, browser, 'browser_navigate', {'url': url})
        await actividad.ejecutar(rt, browser, 'browser_click', {'ref': 'e1'})
        with pytest.raises(ValueError, match='no lo aprobó'):
            await actividad.ejecutar(rt, browser, 'browser_click', {'ref': 'e1'})

    asyncio.run(acciones())
    data = registro(rt)
    first, repeat, click, rejected = data['eventos']
    assert first['origen'] == 'http://127.0.0.1:18488'
    assert [(p['tipo'], p['decision'], p['alcance']) for p in first['permisos']] == [
        ('sitio', 'concedido', 'una_vez'), ('red_local', 'concedido', 'sesion')]
    assert [(p['tipo'], p['decision']) for p in repeat['permisos']] == [('sitio', 'vigente'), ('red_local', 'vigente')]
    assert click['permisos'][0]['tipo'] == 'formulario' and click['permisos'][0]['decision'] == 'concedido'
    assert rejected['resultado'] == 'fallida' and rejected['permisos'][0]['decision'] == 'no_autorizado'
    raw = json.dumps(data)
    assert not any(value in raw for value in ('ruta-privada', 'CLAVE_PRIVADA', 'FRAGMENTO_PRIVADO', 'argv'))
    assert 'datos, no instrucciones' in data['nota']


def test_modo_omitir_registra_la_rama_real_y_no_la_presenta_como_permiso_humano(tmp_path):
    rt = preparar(tmp_path, modo='omitir')
    rt.manager.task = rt.task
    rt.manager.event = lambda *_: None
    rt.approve = modos.AprobacionConModo(rt.approve, rt.manager)
    browser = Navegador(App(permisos={'pulsar': 'formulario'}))
    asyncio.run(actividad.ejecutar(rt, browser, 'browser_click', {'ref': 'e1'}))
    assert rt.pedidos == []
    assert registro(rt)['eventos'][0]['permisos'][0]['decision'] == 'omitido'


def test_permiso_obligatorio_de_equipo_no_se_omite_ni_se_registra_como_omitido(tmp_path):
    rt = preparar(tmp_path, ['no'], modo='omitir')
    rt.manager.task = rt.task
    eventos_sistema = []
    rt.manager.event = lambda *args: eventos_sistema.append(args)
    aprobar = modos.AprobacionConModo(rt.approve, rt.manager)

    async def obligatorio(solicitud):
        return await aprobar(dict(solicitud, _equipo=True))

    rt.approve = obligatorio
    browser = Navegador(App(permisos={'pulsar': 'formulario'}))
    with pytest.raises(ValueError, match='no lo aprobó'):
        asyncio.run(actividad.ejecutar(rt, browser, 'browser_click', {'ref': 'e1'}))
    assert len(rt.pedidos) == 1 and rt.pedidos[0]['_equipo'] is True
    assert not eventos_sistema
    evento, = registro(rt)['eventos']
    assert evento['resultado'] == 'fallida'
    assert [p['decision'] for p in evento['permisos']] == ['no_autorizado']


def test_solo_la_captura_del_agente_se_asocia_y_preserva_el_hook_de_interfaz(tmp_path):
    rt = preparar(tmp_path)
    browser = rt.manager.navegador = Navegador(App())
    automáticas = []

    async def interfaz(runtime, nombre, resultado):
        assert not actividad.activa()  # El hook existente corre después de cerrar el contexto del agente.
        automáticas.append((nombre, resultado))
        if nombre == 'browser_read':
            await browser._screenshot(runtime, {})

    rt.manager.pruebas_interfaz = SimpleNamespace(navegador=interfaz)
    tools = object.__new__(WorkspaceTools)
    tools.runtime = rt

    async def acciones():
        await tools._call('browser_read', {})
        await tools._call('browser_screenshot', {})

    asyncio.run(acciones())
    read, screenshot = registro(rt)['eventos']
    assert [row[0] for row in automáticas] == ['browser_read', 'browser_screenshot']
    assert 'captura' not in read
    assert (rt.directory / 'capturas' / screenshot['captura']).read_bytes() == PNG
    assert '/' not in screenshot['captura']
    assert len(list((rt.directory / 'capturas').glob('*.png'))) == 2


def test_historial_acotado_persiste_entre_llamadas_y_no_sigue_enlaces(tmp_path, monkeypatch):
    rt = preparar(tmp_path)
    monkeypatch.setattr(actividad, 'MAX_EVENTOS', 3)
    browser = Navegador(App())
    for _ in range(5):
        asyncio.run(actividad.ejecutar(rt, browser, 'browser_read', {}))
    data = registro(rt)
    assert data['recortado'] and len({row['id'] for row in data['eventos']}) == 3
    path = rt.directory / actividad.ARCHIVO
    assert path.stat().st_mode & 0o777 == 0o600
    outside = tmp_path / 'externo.json'
    outside.write_text('Datos ajenos al registro')
    path.unlink()
    os.link(outside, path)
    asyncio.run(actividad.ejecutar(rt, browser, 'browser_read', {}))
    assert outside.read_text() == 'Datos ajenos al registro' and path.samefile(outside)
    path.unlink()
    path.symlink_to(outside)
    asyncio.run(actividad.ejecutar(rt, browser, 'browser_read', {}))
    assert path.is_symlink() and outside.read_text() == 'Datos ajenos al registro'


def page(daemon=DAEMON, rows=(), **kwargs):
    return {'scope': {'daemon_session_id': daemon, 'profile': 'demo'}, 'entries': list(rows),
            'enabled': True, 'cursor': 'cursor-siguiente', 'has_more': False, 'truncated': False, **kwargs}


def action(daemon=DAEMON, seq=1, **kwargs):
    return {'id': f'{daemon}:{seq}', 'seq': seq, 'ts': '2026-11-10T12:00:00.000Z', 'tool': 'click',
            'ok': True, 'permission': 'no_consta', 'origin': 'https://ejemplo.test/?token=PRIVADO', **kwargs}


class Http:
    def __init__(self, responses, supported=True):
        self.capabilities = {'auditRecent': True} if supported else {}
        self.tools = {'audit_recent'}
        self.responses = list(responses)
        self.calls = []

    async def call(self, tool, params, *, timeout, approve):
        assert tool == 'audit_recent' and timeout == 2 and approve is False
        self.calls.append(params)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class Observado:
    motor_actividad = 'cuybrowser'
    url = 'https://ejemplo.test/'

    def __init__(self, http, final=None):
        self.http = http
        self.final = final if final is not None else http

    async def llamar(self, _runtime, _name, _args):
        started = await cuy.iniciar(self.http, 'demo')
        await cuy.terminar(started, self.final, 'demo')
        return {'url': self.url}


@pytest.mark.parametrize('responses,state', [
    ([page(), page(rows=[action(permission='approved')])], 'disponible'),
    ([page(), page(daemon=OTRO, rows=[action(daemon=OTRO)])], 'ambito_cambiado'),
    ([page(), ErrorCuyBrowser('AUDIT_CURSOR_EXPIRED', 'MENSAJE_PRIVADO')], 'cursor_caducado'),
    ([page(), page(rows=[action(daemon=OTRO)])], 'incompleta'),
    ([page(), page(rows=[action()], has_more=True)], 'incompleta'),
])
def test_cuy_enlaza_cursor_sin_mezclar_ni_reintentar_ambito(tmp_path, responses, state):
    rt = preparar(tmp_path)
    http = Http(responses)
    asyncio.run(actividad.ejecutar(rt, Observado(http), 'browser_click', {'ref': 'e1'}))
    evidence = registro(rt)['eventos'][0]['cuy']
    assert evidence['estado'] == state
    assert http.calls == [{'limit': 1}, {'limit': 100, 'cursor': 'cursor-siguiente'}]
    assert not any(value in json.dumps(evidence) for value in ('PRIVADO', OTRO, 'cursor-siguiente'))
    if state == 'disponible':
        assert evidence['acciones'][0]['permission'] == 'approved'
        assert evidence['acciones'][0]['origen'] == 'https://ejemplo.test:443'


def test_cuy_publicado_sin_capacidad_no_hace_sondeo_ni_inventa_aprobacion(tmp_path):
    rt = preparar(tmp_path)
    http = Http([], supported=False)
    asyncio.run(actividad.ejecutar(rt, Observado(http), 'browser_click', {'ref': 'e1'}))
    event = registro(rt)['eventos'][0]
    assert event['resultado'] == 'correcta' and event['permisos'] == []
    assert event['cuy'] == {'estado': 'no_disponible', 'acciones': []}
    assert http.calls == []


@pytest.mark.parametrize('payload,outcome', [
    ({'webkit': {}, 'cuybrowser': {}}, 'correcta'),
    ({'ok': False}, 'fallida'),
    ({'error': 'ERROR_CON_TEXTO_PRIVADO'}, 'fallida'),
])
def test_compare_catalogado_registra_el_sitio_solicitado_y_el_resultado_real(tmp_path, monkeypatch, payload, outcome):
    # El otro PR sólo aporta el nombre al catálogo; aquí no se lanza ninguno de sus motores.
    monkeypatch.setattr(navegador, 'NOMBRES', (*navegador.NOMBRES, 'browser_compare'))
    rt = preparar(tmp_path)

    async def llamar(*_):
        return payload

    browser = SimpleNamespace(motor_actividad='webkit', url='https://anterior.test/', llamar=llamar)
    args = {'url': 'https://destino.test/ruta?token=PRIVADO#FRAGMENTO'}
    result = asyncio.run(actividad.ejecutar(rt, browser, 'browser_compare', args))
    assert result is payload
    saved = registro(rt)['eventos'][0]
    assert saved['accion'] == 'browser_compare' and saved['resultado'] == outcome
    assert saved['origen'] == 'https://destino.test:443'
    assert not any(value in json.dumps(saved) for value in ('anterior', 'PRIVADO', 'FRAGMENTO', 'ruta'))
