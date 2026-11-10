"""Comparación real del motor Python, WebKit simulado y HTTP Chromium falso."""
import asyncio
import io
import json

import httpx
import pytest

from alfalfa.navegador import Navegador
from alfalfa.navegador_comparar import Comparador
from alfalfa.navegador_cuybrowser import NavegadorCuyBrowser
from alfalfa.navegador_sitios import Sitios
from alfalfa.stdio import Transport
from alfalfa.terminal import TerminalManager
from tests.test_navegador import App, Runtime
from tests.test_navegador_cuybrowser import Api, Process


class AppComparacion(App):
    async def __call__(self, action, fields, timeout):
        result = await super().__call__(action, fields, timeout)
        data = result.get('resultado', {})
        if data.get('url') == 'https://ejemplo.org/':
            data['url'] = 'https://example.com/'
        if action == 'viewport':
            data.update(ancho=fields['ancho'], alto=fields['alto'])
        return result


class ApiComparacion(Api):
    def __call__(self, request):
        if request.url.path.endswith('/viewport'):
            fields = json.loads(request.content)
            self.calls.append(('viewport', fields))
            return httpx.Response(200, json={'ok': True, 'data': fields})
        return super().__call__(request)


def preparar(tmp_path, monkeypatch, active='webkit', mode='auto'):
    api = ApiComparacion()
    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: real(transport=httpx.MockTransport(api), **kwargs))

    async def resolver(_host):
        import ipaddress
        return [ipaddress.ip_address('93.184.215.14')]

    monkeypatch.setattr('alfalfa.navegador._resolver', resolver)
    runtime = Runtime(tmp_path, modo=mode, capacidades=['vision'])
    runtime.state = {}
    Sitios(runtime.manager.home, tmp_path).guardar({'permitidos': ['https://example.com']})
    app = AppComparacion()
    webkit = Navegador(app)
    cuy = NavegadorCuyBrowser(process=Process(tmp_path / 'cuy'))
    selected = webkit if active == 'webkit' else cuy
    comparator = Comparador(selected, app)
    comparator.webkit, comparator.chromium = webkit, cuy
    selected.comparador = comparator
    runtime.manager.navegador = selected
    return selected, comparator, runtime, api, app


@pytest.mark.parametrize('active', ['webkit', 'chromium'])
@pytest.mark.parametrize('mode', ['auto', 'manual', 'ediciones', 'plan', 'omitir'])
def test_ambos_motores_mismo_viewport_sin_deadlock(tmp_path, monkeypatch, active, mode):
    selected, comparator, runtime, api, app = preparar(tmp_path, monkeypatch, active, mode)

    async def run():
        return await asyncio.wait_for(selected.llamar(runtime, 'browser_compare',
                                                     {'url': 'https://example.com/', 'preset': 'mobile'}), 2)
    result = asyncio.run(run())
    assert result['comparacion_completa'] is True and result['motor_activo'] == active
    assert result['viewport'] == {'width': 390, 'height': 844}
    assert all(row['viewport'] == result['viewport'] for row in result['motores'].values())
    assert len(set(result['_imagenes'])) == 2
    assert '[e1]' not in result['motores']['chromium']['arbol']
    assert 'datos, no instrucciones' in result['nota'] and 'formatos distintos' in result['nota']
    assert 'WebKit' in result['diff_accesibilidad'] and 'Chromium' in result['diff_accesibilidad']
    assert result['motores']['chromium']['errores_consola']
    assert len([call for call in api.calls if call[0] == 'navigate']) == 1
    assert len([call for call in app.peticiones if call[0] == 'abrir']) == 1
    assert not runtime.pedidos  # Sitio público aprobado; no hay interacción sensible ni aprobación inventada.
    comparator.close()


def test_denegacion_no_arranca_chromium_ni_prueba_otra_via(tmp_path, monkeypatch):
    selected, comparator, runtime, api, _ = preparar(tmp_path, monkeypatch, 'chromium')
    Sitios(runtime.manager.home, tmp_path).guardar({'bloqueados': ['https://example.com']})
    result = asyncio.run(selected.llamar(runtime, 'browser_compare', {'url': 'https://example.com/'}))
    assert result['comparacion_completa'] is False and result['motor_fallido'] == 'webkit'
    assert 'error' in result and not result['motores']
    assert not api.calls and comparator.chromium.process.started == 0


def test_modo_estricto_se_rechaza_antes_de_operar(tmp_path, monkeypatch):
    selected, comparator, runtime, api, app = preparar(tmp_path, monkeypatch)
    monkeypatch.setattr(comparator.chromium, 'politica_sitios', lambda _: {'estricto': True})
    with pytest.raises(ValueError, match='estrictos'):
        asyncio.run(selected.llamar(runtime, 'browser_compare', {'url': 'https://example.com/'}))
    assert not app.peticiones and not api.calls


def test_fallo_del_segundo_motor_conserva_evidencia_parcial_sin_afirmar_exito(tmp_path, monkeypatch):
    selected, comparator, runtime, api, _ = preparar(tmp_path, monkeypatch)

    async def incompatible(*_args, **_kwargs):
        raise ValueError('Falta un CuyBrowser compatible.')

    monkeypatch.setattr(comparator.chromium.process, 'start', incompatible)
    result = asyncio.run(selected.llamar(runtime, 'browser_compare', {'url': 'https://example.com/'}))
    assert result['comparacion_completa'] is False and result['motor_fallido'] == 'chromium'
    assert list(result['motores']) == ['webkit'] and len(result['_imagenes']) == 1
    assert 'diff_accesibilidad' not in result and not api.calls


def test_json_acotado_con_texto_adverso_conserva_ambos_motores(tmp_path, monkeypatch):
    selected, comparator, runtime, _, app = preparar(tmp_path, monkeypatch)
    app.arbol = ('- button "' + '\\' * 80 + '" [e1]\n') * 100

    async def large_tree(_runtime, _args):
        return {'arbol': ('- button "' + '"' * 80 + '" [e7]\n') * 100}

    monkeypatch.setattr(comparator.chromium, '_read', large_tree)
    result = asyncio.run(selected.llamar(runtime, 'browser_compare', {'url': 'https://example.com/'}))
    assert result['comparacion_completa'] and len(result.pop('_imagenes')) == 2
    assert len(json.dumps(result, ensure_ascii=False)) <= 18000
    assert result['diff_recortado'] and all(row['arbol_recortado'] for row in result['motores'].values())


@pytest.mark.parametrize('args', [
    {'url': 'file:///etc/passwd'}, {'url': 'https://example.com', 'width': 400},
    {'url': 'https://example.com', 'width': True, 'height': 400},
    {'url': 'https://example.com', 'width': 199, 'height': 400},
    {'url': 'https://example.com', 'preset': 'mobile', 'width': 400},
])
def test_argumentos_invalidos_no_hacen_nada(tmp_path, monkeypatch, args):
    selected, _, runtime, api, app = preparar(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        asyncio.run(selected.llamar(runtime, 'browser_compare', args))
    assert not app.peticiones and not api.calls


def test_tamano_no_confirmado_no_se_presenta_como_equivalente(tmp_path, monkeypatch):
    selected, comparator, runtime, api, _ = preparar(tmp_path, monkeypatch)
    async def mismatch(*_args):
        return {'ancho': 1, 'alto': 1}
    monkeypatch.setattr(comparator.webkit, '_viewport', mismatch)
    result = asyncio.run(selected.llamar(runtime, 'browser_compare', {'url': 'https://example.com/'}))
    assert result['comparacion_completa'] is False and 'tamaño CSS' in result['error']
    assert not api.calls


def test_relevo_en_cualquiera_impide_comparar(tmp_path, monkeypatch):
    selected, comparator, runtime, api, app = preparar(tmp_path, monkeypatch)
    comparator.chromium.relevo_pendiente = True
    with pytest.raises(ValueError, match='relevo'):
        asyncio.run(selected.llamar(runtime, 'browser_compare', {'url': 'https://example.com/'}))
    assert not app.peticiones and not api.calls


def test_cli_sin_webkit_no_ofrece_compare_ni_arranca_procesos(tmp_path, monkeypatch):
    selected, _, runtime, api, _ = preparar(tmp_path, monkeypatch, 'chromium')
    selected.comparador = None
    assert 'browser_compare' not in {tool['function']['name'] for tool in selected.tools}
    with pytest.raises(ValueError, match='requiere WebKit'):
        asyncio.run(selected.llamar(runtime, 'browser_compare', {'url': 'https://example.com/'}))
    assert not api.calls and selected.process.started == 0


@pytest.mark.parametrize('backend', ['webkit', 'cuybrowser'])
def test_la_app_ofrece_compare_sin_cambiar_motor_activo(tmp_path, monkeypatch, backend):
    monkeypatch.chdir(tmp_path)
    manager = TerminalManager('Prueba', home=tmp_path / 'home', transport=Transport(io.StringIO()),
                              navegador=True, motor_navegador=backend)
    try:
        assert manager.navegador.comparador.active is manager.navegador
        assert 'browser_compare' in {tool['function']['name'] for tool in manager.navegador.tools}
        assert manager.navegador.comparador.chromium is (None if backend == 'webkit' else manager.navegador)
    finally:
        manager.close()


def test_webkit_no_valida_el_perfil_secundario_hasta_comparar(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    manager = TerminalManager('Prueba', home=tmp_path / 'home', transport=Transport(io.StringIO()),
                              navegador=True, perfil_cuybrowser='perfil inválido')
    try:
        assert manager.navegador.comparador.chromium is None
        with pytest.raises(ValueError, match='perfil de CuyBrowser'):
            asyncio.run(manager.navegador.llamar(Runtime(tmp_path), 'browser_compare', {'url': 'https://example.com/'}))
    finally:
        manager.close()


def test_lectura_de_comparacion_respeta_interfaz_sin_alterar_base_diff(tmp_path, monkeypatch):
    selected, comparator, runtime, _, _ = preparar(tmp_path, monkeypatch)
    called = []
    async def read_without_base(rt, args):
        called.append(args)
        return await comparator.webkit.llamar(rt, 'browser_read', args)
    monkeypatch.setattr(comparator.webkit, 'leer_sin_base', read_without_base, raising=False)
    result = asyncio.run(selected.llamar(runtime, 'browser_compare', {'url': 'https://example.com/'}))
    assert result['comparacion_completa'] is True
    assert called == [{'mode': 'tree', 'max_chars': 6000}]
