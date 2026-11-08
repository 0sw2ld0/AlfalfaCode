"""OpenAI con la clave del usuario: traducción a la Responses API y vuelta."""
import asyncio
import base64
import json

import httpx
import pytest

from alfalfa import proveedores
from alfalfa.proveedores import configuracion, llavero, openai


def test_la_historia_de_ollama_se_traduce_a_responses():
    png = base64.b64encode(b'\x89PNG\r\n\x1a\n' + b'0' * 40).decode()
    jpg = base64.b64encode(b'\xff\xd8\xff\xe0' + b'0' * 40).decode()
    payload = {
        'model': 'gpt-6-luna', 'stream': False, 'think': True,
        'options': {'num_ctx': 272000, 'num_predict': 32000, 'temperature': 0.7, 'top_p': 1.0},
        'tools': [{'type': 'function', 'function': {'name': 'read_file', 'description': 'Lee',
                                                    'parameters': {'type': 'object'}}}],
        'messages': [
            {'role': 'system', 'content': 'Reglas.'},
            {'role': 'user', 'content': 'Mira esto', 'images': [png, jpg]},
            {'role': 'assistant', 'content': 'Voy.', 'tool_calls': [
                {'id': 'call_A', 'function': {'name': 'read_file', 'arguments': {'path': 'a'}}},
                {'function': {'name': 'list_files', 'arguments': {}}}]},
            {'role': 'tool', 'tool_name': 'list_files', 'content': 'a b'},
            {'role': 'tool', 'tool_name': 'read_file', 'content': 'contenido'},
            {'role': 'system', 'content': 'Aviso a mitad.'},
            {'role': 'user', 'content': 'Sigue'},
        ]}
    cuerpo = openai.traducir_peticion(payload, 'xhigh')
    assert cuerpo['model'] == 'gpt-6-luna' and cuerpo['store'] is False
    assert cuerpo['instructions'] == 'Reglas.'
    assert cuerpo['max_output_tokens'] == 32000
    assert cuerpo['reasoning'] == {'effort': 'xhigh', 'summary': 'auto'}
    assert 'temperature' not in json.dumps(cuerpo)
    assert cuerpo['tools'] == [{'type': 'function', 'name': 'read_file', 'description': 'Lee',
                                'parameters': {'type': 'object'}, 'strict': False}]
    entrada = cuerpo['input']
    usuario = entrada[0]['content']
    assert usuario[0] == {'type': 'input_text', 'text': 'Mira esto'}
    assert usuario[1]['image_url'].startswith('data:image/png;base64,')
    assert usuario[2]['image_url'].startswith('data:image/jpeg;base64,')
    assert entrada[1] == {'role': 'assistant', 'content': 'Voy.'}
    assert entrada[2] == {'type': 'function_call', 'call_id': 'call_A', 'name': 'read_file',
                          'arguments': '{"path": "a"}'}
    segunda = entrada[3]['call_id']
    assert entrada[3]['name'] == 'list_files' and segunda != 'call_A'
    # Cada resultado va a su llamada por el nombre de la herramienta, no por el orden.
    assert entrada[4] == {'type': 'function_call_output', 'call_id': segunda, 'output': 'a b'}
    assert entrada[5] == {'type': 'function_call_output', 'call_id': 'call_A', 'output': 'contenido'}
    assert entrada[6] == {'role': 'developer', 'content': 'Aviso a mitad.'}
    assert entrada[7]['role'] == 'user'


def test_una_llamada_sin_resultado_se_cierra_y_un_resultado_suelto_es_texto():
    cuerpo = openai.traducir_peticion({'model': 'gpt-6-luna', 'think': False, 'messages': [
        {'role': 'tool', 'tool_name': 'shell', 'content': 'suelto'},
        {'role': 'assistant', 'content': '', 'tool_calls': [{'function': {'name': 'shell', 'arguments': '{}'}}]},
        {'role': 'user', 'content': 'otra cosa'}]})
    entrada = cuerpo['input']
    assert entrada[0]['role'] == 'user' and 'suelto' in entrada[0]['content']
    assert entrada[1]['type'] == 'function_call' and entrada[1]['arguments'] == '{}'
    assert entrada[2]['type'] == 'function_call_output' and entrada[2]['call_id'] == entrada[1]['call_id']
    assert cuerpo['reasoning'] == {'effort': 'none'}


def test_el_formato_json_y_el_esquema_pasan_a_text_format():
    assert openai.traducir_peticion({'messages': [], 'format': 'json'})['text'] == {
        'format': {'type': 'json_object'}}
    esquema = {'type': 'object', 'properties': {'ok': {'type': 'boolean'}}}
    assert openai.traducir_peticion({'messages': [], 'format': esquema})['text']['format']['schema'] == esquema


def test_la_respuesta_vuelve_al_formato_de_ollama():
    datos = {'status': 'completed', 'usage': {'input_tokens': 1200, 'output_tokens': 80}, 'output': [
        {'type': 'reasoning', 'summary': [{'type': 'summary_text', 'text': 'Pienso.'}]},
        {'type': 'message', 'content': [{'type': 'output_text', 'text': 'Leo '},
                                        {'type': 'output_text', 'text': 'el archivo.'}]},
        {'type': 'function_call', 'call_id': 'call_1', 'name': 'read_file', 'arguments': '{"path":"a"}'},
    ]}
    r = openai.traducir_respuesta(datos, 'gpt-6-luna')
    assert r['done'] is True and r['done_reason'] == 'stop'
    assert r['prompt_eval_count'] == 1200 and r['eval_count'] == 80
    assert r['message'] == {'role': 'assistant', 'content': 'Leo el archivo.', 'thinking': 'Pienso.',
                            'tool_calls': [{'id': 'call_1', 'function': {'name': 'read_file',
                                                                         'arguments': {'path': 'a'}}}]}
    cortada = openai.traducir_respuesta({'status': 'incomplete',
                                         'incomplete_details': {'reason': 'max_output_tokens'}, 'output': []}, 'm')
    assert cortada['done'] is True and cortada['done_reason'] == 'length'


def test_show_da_ventana_y_capacidades_de_nube():
    info = openai.mostrar('gpt-6-luna')
    assert {'tools', 'vision', 'thinking'} <= set(info['capabilities'])
    assert info['model_info']['openai.context_length'] == 272000
    from alfalfa.proveedores.nativo.runtime import is_cloud
    assert is_cloud('gpt-6-luna', info)


def test_sin_clave_openai_no_sale_en_la_lista_y_con_clave_si():
    assert not proveedores.obtener('openai').configurado()
    llavero.guardar('openai', 'sk-prueba-123')
    filas = proveedores.obtener('openai').modelos()
    assert filas == [{'name': 'openai/gpt-6-luna', 'model': 'openai/gpt-6-luna', 'proveedor': 'openai',
                      'nombre': 'GPT-6 Luna'}]
    configuracion.cambiar('openai', 'modelos', ['gpt-6-sol'])
    assert [f['name'] for f in proveedores.obtener('openai').modelos()][-1] == 'openai/gpt-6-sol'


def test_la_url_de_openai_es_configurable_pero_con_https():
    assert proveedores.obtener('openai').url() == 'https://api.openai.com/v1'
    configuracion.cambiar('openai', 'url', 'http://proxy.example.com/v1')
    with pytest.raises(ValueError, match='HTTPS'):
        proveedores.obtener('openai').url()
    configuracion.cambiar('openai', 'url', 'http://localhost:8080/v1')
    assert proveedores.obtener('openai').url() == 'http://localhost:8080/v1'


class OpenAIFalso:
    """La Responses API, en memoria."""

    def __init__(self, respuestas):
        self.respuestas, self.cuerpos, self.cabeceras = list(respuestas), [], []

    def __call__(self, request):
        assert request.url.path == '/v1/responses'
        self.cuerpos.append(json.loads(request.content))
        self.cabeceras.append(dict(request.headers))
        estado, datos = self.respuestas.pop(0)
        return httpx.Response(estado, json=datos, headers={'retry-after': '0'} if estado == 429 else {})


@pytest.fixture
def falso(monkeypatch):
    def con(respuestas):
        api = OpenAIFalso(respuestas)
        real = openai.Transporte.__init__

        def init(self, url, clave, tarea=None, timeout=None, transporte=None):
            real(self, url, clave, tarea, timeout, httpx.MockTransport(api))
        monkeypatch.setattr(openai.Transporte, '__init__', init)
        return api
    return con


def test_un_turno_completo_con_herramientas_contra_openai(tmp_path, monkeypatch, falso):
    from alfalfa.terminal import TerminalManager
    (tmp_path / 'a.txt').write_text('hola desde a\n')
    monkeypatch.chdir(tmp_path)
    llavero.guardar('openai', 'sk-prueba-123')
    api = falso([
        (200, {'status': 'completed', 'usage': {'input_tokens': 500, 'output_tokens': 20}, 'output': [
            {'type': 'function_call', 'call_id': 'call_X', 'name': 'read_file', 'arguments': '{"path": "a.txt"}'}]}),
        (200, {'status': 'completed', 'usage': {'input_tokens': 600, 'output_tokens': 10}, 'output': [
            {'type': 'message', 'content': [{'type': 'output_text', 'text': 'Dice hola desde a.'}]}]}),
    ])
    m = TerminalManager('Lee a.txt', home=tmp_path / 'home', model='openai/gpt-6-luna', effort='high', auto=True)
    try:
        codigo = asyncio.run(asyncio.wait_for(m.run('Lee a.txt'), 60))
        assert codigo == 0, m.task.get('summary')
        assert m.task['provider'] == 'openai'
        assert api.cabeceras[0]['authorization'] == 'Bearer sk-prueba-123'
        primera, segunda = api.cuerpos
        assert primera['model'] == 'gpt-6-luna' and primera['reasoning']['effort'] == 'high'
        salida = [i for i in segunda['input'] if i.get('type') == 'function_call_output']
        assert salida and salida[0]['call_id'] == 'call_X' and 'hola desde a' in salida[0]['output']
        assert 'Dice hola desde a.' in json.dumps(m.task, ensure_ascii=False) or \
            'Dice hola desde a.' in (tmp_path / 'home').joinpath('sesiones', m.task['id'], 'events.jsonl').read_text()
    finally:
        m.close()


def test_los_errores_de_openai_llegan_como_los_de_ollama(tmp_path, monkeypatch, falso):
    from alfalfa.terminal import TerminalManager
    monkeypatch.chdir(tmp_path)
    llavero.guardar('openai', 'sk-prueba-123')
    falso([(401, {'error': {'message': 'Incorrect API key provided: sk-prueba-123', 'type': 'invalid_request_error',
                            'code': 'invalid_api_key'}})])
    m = TerminalManager('Hola', home=tmp_path / 'home', model='openai/gpt-6-luna', auto=True)
    try:
        codigo = asyncio.run(asyncio.wait_for(m.run('Hola'), 60))
        assert codigo != 0
        error = m.task['runtime_provider_error']
        assert error['provider'] == 'openai' and error['status_code'] == 401
        assert 'sk-prueba-123' not in json.dumps(m.task)
    finally:
        m.close()


def test_sin_clave_el_motor_lo_explica(tmp_path, monkeypatch):
    from alfalfa.terminal import TerminalManager
    monkeypatch.chdir(tmp_path)
    m = TerminalManager('Hola', home=tmp_path / 'home', model='openai/gpt-6-luna', auto=True)
    try:
        asyncio.run(asyncio.wait_for(m.run('Hola'), 60))
        assert 'no está configurado' in m.task.get('summary', '') + json.dumps(m.task, ensure_ascii=False)
    finally:
        m.close()


def test_el_proveedor_por_defecto_elige_su_modelo(tmp_path, monkeypatch):
    from alfalfa.terminal import TerminalManager, modelo_por_defecto
    monkeypatch.chdir(tmp_path)
    configuracion.cambiar(None, 'por_defecto', 'openai')
    # Sin clave no se puede usar: se queda el de Ollama.
    assert modelo_por_defecto() == 'deepseek-v4.1-flash:cloud'
    llavero.guardar('openai', 'sk-prueba-123')
    assert modelo_por_defecto() == 'openai/gpt-6-luna'
    m = TerminalManager('x', home=tmp_path / 'home')
    try:
        assert m.task['model'] == 'openai/gpt-6-luna' and m.task['meta']['proveedor'] == 'openai'
    finally:
        m.close()
