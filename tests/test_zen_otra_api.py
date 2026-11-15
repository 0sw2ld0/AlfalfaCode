"""OpenCode Zen: un modelo que habla otra API no se manda a /chat/completions.

En Zen, Claude va por la API de Anthropic, Gemini por la de Google y Mistral por la suya (el catálogo
lo dice en `paquete`). Antes se mandaban igual a `/chat/completions` de la URL de OpenAI y el error
del proveedor no decía por qué; 26 de los 83 modelos de Zen.
"""
import asyncio

import httpx
import pytest

from alfalfa import proveedores
from alfalfa.proveedores import llavero


def pedir(modelo):
    llavero.guardar('opencode', 'clave-inventada-opencode-000')
    rutas = []

    def responder(peticion):
        rutas.append(peticion.url.path)
        if peticion.url.path.endswith('/responses'):
            return httpx.Response(200, json={'output': [{'type': 'message', 'role': 'assistant',
                                                          'content': [{'type': 'output_text', 'text': 'Hola.'}]}],
                                             'status': 'completed'})
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop',
                                                      'message': {'role': 'assistant', 'content': 'Hola.'}}]})

    async def ir():
        async with proveedores.obtener('opencode').cliente(30, transporte=httpx.MockTransport(responder)) as cliente:
            hecha = await cliente.post('/api/chat', json={'model': modelo,
                                                          'messages': [{'role': 'user', 'content': 'hola'}]})
            return hecha.status_code, hecha.json()
    codigo, cuerpo = asyncio.run(ir())
    return codigo, cuerpo, rutas


@pytest.mark.parametrize('modelo, api', [('claude-opus-4-8', 'Anthropic'), ('gemini-3-flash', 'Google'),
                                         ('mistral-large-4', 'Mistral')])
def test_otra_api_se_rechaza_sin_llamar(modelo, api):
    codigo, cuerpo, rutas = pedir(modelo)
    assert codigo == 400 and rutas == []
    assert 'habla la API de ' + api in cuerpo['error'] and 'Elige otro modelo de OpenCode Zen' in cuerpo['error']


@pytest.mark.parametrize('modelo, ruta', [('deepseek-v4-pro', '/chat/completions'), ('gpt-5', '/responses')])
def test_las_de_openai_siguen_yendo(modelo, ruta):
    codigo, _, rutas = pedir(modelo)
    assert codigo == 200 and len(rutas) == 1 and rutas[0].endswith(ruta)
