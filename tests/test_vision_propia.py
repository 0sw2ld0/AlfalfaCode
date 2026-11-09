"""inspect_image mira con el modelo de la sesión, como `view_image` en Codex.

Quien integra el arnés puede delegarlo (ajustes.vision_delegada), por ejemplo
en una sesión de Codex.
"""
import asyncio
import base64

import pytest

from alfalfa import ajustes
from alfalfa.proveedores.nativo import herramientas
from alfalfa.proveedores.nativo.runtime import NativeRuntime
from alfalfa.terminal import TerminalManager

PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5E'
                       'rkJggg==')


@pytest.fixture
def m(tmp_path, monkeypatch):
    (tmp_path / 'project').mkdir()
    (tmp_path / 'project' / 'captura.png').write_bytes(PNG)
    monkeypatch.chdir(tmp_path / 'project')
    manager = TerminalManager('Encargo.', home=tmp_path / 'alfalfa')
    yield manager
    manager.close()


def _runtime(m, capacidades):
    rt = m.runtime()
    rt.task['runtime_capabilities'] = capacidades
    rt._ollama = (object(), 'gemma4:cloud', None)
    return rt


def test_mira_el_modelo_de_la_sesion_con_la_imagen_en_images(m, monkeypatch):
    rt = _runtime(m, ['tools', 'vision'])
    enviados = []

    async def request(self, client, endpoint, payload, session_deadline=None):
        enviados.append((endpoint, payload))
        return {'done': True, 'message': {'content': 'Un punto negro sobre fondo blanco.'}}

    monkeypatch.setattr(NativeRuntime, 'request', request)
    path = rt.tools.path('captura.png')
    result = asyncio.run(rt.vision(path, '¿Qué se ve?'))
    assert result['analysis'] == 'Un punto negro sobre fondo blanco.'
    assert result['model'] == 'gemma4:cloud'
    endpoint, payload = enviados[0]
    assert endpoint == '/api/chat' and payload['model'] == 'gemma4:cloud' and 'tools' not in payload
    assert payload['messages'][-1]['images'] == [base64.b64encode(PNG).decode()]
    assert '¿Qué se ve?' in payload['messages'][-1]['content']
    # La segunda vez sale de la caché, sin otra llamada.
    assert asyncio.run(rt.vision(path, '¿Qué se ve?')) == result and len(enviados) == 1


def test_sin_vision_lo_dice_y_no_llama_ni_a_codex(m, monkeypatch):
    rt = _runtime(m, ['tools'])
    monkeypatch.setattr(ajustes, 'ejecutable', lambda nombre: pytest.fail('no debe buscar Codex'))
    with pytest.raises(ValueError, match='gemma4:cloud no admite imágenes'):
        asyncio.run(rt.vision(rt.tools.path('captura.png'), '¿Qué se ve?'))
    assert not rt._puede_ver()
    assert _runtime(m, ['tools', 'vision'])._puede_ver()


def test_quien_integra_puede_delegarla_en_codex(m, monkeypatch):
    rt = _runtime(m, ['tools'])
    monkeypatch.setattr(ajustes, 'vision_delegada',
                        lambda: {'modelo': 'gpt-6-luna', 'esfuerzo': 'max', 'nombre': 'Luna'})
    monkeypatch.setattr(ajustes, 'ejecutable', lambda nombre: None)
    assert rt._puede_ver()
    with pytest.raises(ValueError, match='Luna requiere Codex instalado'):
        asyncio.run(rt.vision(rt.tools.path('captura.png'), '¿Qué se ve?'))
    hijo = [t for t in m.tasks.values() if t.get('role') == 'vision_router'][-1]
    assert hijo['model'] == 'gpt-6-luna' and hijo['effort'] == 'max' and hijo['provider'] == 'codex'


def test_la_descripcion_no_nombra_un_modelo_concreto():
    propia = next(t for t in herramientas.ON_REQUEST_TOOLS if t['function']['name'] == 'inspect_image')
    texto = propia['function']['description']
    for nombre in ('Luna', 'GPT'):
        assert nombre not in texto
    del_integrador = next(t for t in herramientas.TOOLS if t['function']['name'] == 'inspect_image')
    assert del_integrador['function']['description'].startswith('Ask the configured vision model')


def test_ollama_se_configura_con_sus_variables(monkeypatch):
    for nombre in ('ALFALFA_OLLAMA_URL', 'OLLAMA_API_KEY'):
        monkeypatch.delenv(nombre, raising=False)
    assert ajustes.ollama_url() == 'http://127.0.0.1:11434' and ajustes.ollama_clave() is None
    monkeypatch.setenv('ALFALFA_OLLAMA_URL', 'https://ollama.com')
    monkeypatch.setenv('OLLAMA_API_KEY', 'clave-estandar')
    assert ajustes.ollama_url() == 'https://ollama.com' and ajustes.ollama_clave() == 'clave-estandar'
