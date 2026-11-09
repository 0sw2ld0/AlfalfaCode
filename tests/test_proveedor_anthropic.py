"""Claude con la clave del usuario: traducción a la API de mensajes de Anthropic y vuelta, sin salir a internet."""
import asyncio
import base64
import io
import json
from types import SimpleNamespace

import httpx
import pytest

from alfalfa import catalogo, proveedores
from alfalfa.proveedores import anthropic, configuracion, llavero, orden

CLAVE = 'sk-ant-prueba-123456'


def sse(*eventos):
    """El cuerpo de un stream de `/v1/messages` con estos eventos."""
    return ''.join('event: ' + e['type'] + '\ndata: ' + json.dumps(e) + '\n\n' for e in eventos).encode()


def mensaje(*bloques, stop='end_turn', entrada=500, salida=20, leida=0, escrita=0):
    """Los eventos de una respuesta entera: cada bloque es ('text', t), ('thinking', t, firma) o
    ('tool_use', id, nombre, argumentos)."""
    eventos = [{'type': 'message_start', 'message': {
        'id': 'msg_1', 'type': 'message', 'role': 'assistant', 'model': 'claude-haiku-5-5', 'content': [],
        'usage': {'input_tokens': entrada, 'cache_creation_input_tokens': escrita,
                  'cache_read_input_tokens': leida, 'output_tokens': 1}}}]
    for i, bloque in enumerate(bloques):
        if bloque[0] == 'text':
            eventos += [{'type': 'content_block_start', 'index': i, 'content_block': {'type': 'text', 'text': ''}},
                        {'type': 'content_block_delta', 'index': i, 'delta': {'type': 'text_delta',
                                                                              'text': bloque[1][:3]}},
                        {'type': 'content_block_delta', 'index': i, 'delta': {'type': 'text_delta',
                                                                              'text': bloque[1][3:]}}]
        elif bloque[0] == 'thinking':
            eventos += [{'type': 'content_block_start', 'index': i,
                         'content_block': {'type': 'thinking', 'thinking': '', 'signature': ''}},
                        {'type': 'content_block_delta', 'index': i, 'delta': {'type': 'thinking_delta',
                                                                              'thinking': bloque[1]}},
                        {'type': 'content_block_delta', 'index': i, 'delta': {'type': 'signature_delta',
                                                                              'signature': bloque[2]}}]
        else:
            texto = json.dumps(bloque[3])
            eventos += [{'type': 'content_block_start', 'index': i, 'content_block': {
                'type': 'tool_use', 'id': bloque[1], 'name': bloque[2], 'input': {}}},
                {'type': 'content_block_delta', 'index': i, 'delta': {'type': 'input_json_delta',
                                                                      'partial_json': texto[:5]}},
                {'type': 'content_block_delta', 'index': i, 'delta': {'type': 'input_json_delta',
                                                                      'partial_json': texto[5:]}}]
        eventos.append({'type': 'content_block_stop', 'index': i})
    eventos += [{'type': 'message_delta', 'delta': {'stop_reason': stop, 'stop_sequence': None},
                 'usage': {'output_tokens': salida}},
                {'type': 'message_stop'}]
    return eventos


def test_la_historia_de_ollama_se_traduce_a_mensajes_de_anthropic():
    png = base64.b64encode(b'\x89PNG\r\n\x1a\n' + b'0' * 40).decode()
    payload = {
        'model': 'claude-haiku-5-5', 'stream': False, 'think': True,
        'options': {'num_ctx': 100000, 'num_predict': 32000, 'temperature': 0.7, 'top_p': 1.0},
        'tools': [{'type': 'function', 'function': {'name': 'read_file', 'description': 'Lee',
                                                    'parameters': {'type': 'object'}}},
                  {'type': 'function', 'function': {'name': 'list_files', 'description': 'Lista'}}],
        'messages': [
            {'role': 'system', 'content': 'Reglas.'},
            {'role': 'system', 'content': 'Más reglas.'},
            {'role': 'user', 'content': 'Mira esto', 'images': [png]},
            {'role': 'assistant', 'content': 'Voy.', 'tool_calls': [
                {'id': 'toolu_A', 'function': {'name': 'read_file', 'arguments': {'path': 'a'}}},
                {'function': {'name': 'list_files', 'arguments': '{}'}}]},
            {'role': 'tool', 'tool_name': 'list_files', 'content': 'a b'},
            {'role': 'tool', 'tool_name': 'read_file', 'content': 'contenido'},
            {'role': 'user', 'content': 'Sigue'},
            {'role': 'system', 'content': 'Aviso a mitad.'},
        ]}
    cuerpo, betas = anthropic.traducir_peticion(payload, 'xhigh', 128000)
    assert cuerpo['model'] == 'claude-haiku-5-5' and cuerpo['stream'] is True
    assert cuerpo['max_tokens'] == 32000
    # Caché: el final del prompt del sistema, la última herramienta y la automática para la conversación.
    assert cuerpo['system'] == [{'type': 'text', 'text': 'Reglas.'},
                                {'type': 'text', 'text': 'Más reglas.', 'cache_control': {'type': 'ephemeral'}}]
    assert cuerpo['cache_control'] == {'type': 'ephemeral'}
    assert [t.get('cache_control') for t in cuerpo['tools']] == [None, {'type': 'ephemeral'}]
    assert cuerpo['tools'][0] == {'name': 'read_file', 'description': 'Lee', 'input_schema': {'type': 'object'}}
    assert cuerpo['tools'][1]['input_schema'] == {'type': 'object', 'properties': {}}
    # Razonamiento adaptativo con el esfuerzo de la sesión; los parámetros de muestreo no se mandan.
    assert cuerpo['thinking'] == {'type': 'adaptive', 'display': 'summarized',
                                  'block_binding': {'prefix_mismatch_behavior': 'drop_block'}}
    assert cuerpo['output_config'] == {'effort': 'xhigh'}
    assert betas == [anthropic.BETA_BLOQUES]
    assert 'temperature' not in json.dumps(cuerpo) and 'top_p' not in json.dumps(cuerpo)
    usuario, asistente, resultados, aviso = cuerpo['messages']
    assert usuario['role'] == 'user'
    assert usuario['content'][0]['type'] == 'image'
    assert usuario['content'][0]['source'] == {'type': 'base64', 'media_type': 'image/png', 'data': png}
    assert usuario['content'][1] == {'type': 'text', 'text': 'Mira esto'}
    assert asistente['content'][0] == {'type': 'text', 'text': 'Voy.'}
    assert asistente['content'][1] == {'type': 'tool_use', 'id': 'toolu_A', 'name': 'read_file',
                                       'input': {'path': 'a'}}
    segunda = asistente['content'][2]['id']
    assert asistente['content'][2]['name'] == 'list_files' and segunda != 'toolu_A'
    # Cada resultado va a su llamada por el nombre de la herramienta; el texto nuevo va detrás, en el mismo turno.
    assert resultados['role'] == 'user'
    assert resultados['content'][0] == {'type': 'tool_result', 'tool_use_id': segunda,
                                        'content': [{'type': 'text', 'text': 'a b'}]}
    assert resultados['content'][1]['tool_use_id'] == 'toolu_A'
    assert resultados['content'][2] == {'type': 'text', 'text': 'Sigue'}
    # Haiku 5.5 admite mensajes `system` a mitad: al final, tras el usuario.
    assert aviso == {'role': 'system', 'content': 'Aviso a mitad.'}


def test_un_aviso_del_sistema_donde_no_cabe_va_dentro_del_turno_del_usuario():
    mensajes = [{'role': 'system', 'content': 'Reglas.'},
                {'role': 'user', 'content': 'Hola'},
                {'role': 'system', 'content': 'Aviso.'},
                {'role': 'user', 'content': 'Sigue'}]
    cuerpo, _ = anthropic.traducir_peticion({'model': 'claude-haiku-5-5', 'messages': mensajes})
    assert [m['role'] for m in cuerpo['messages']] == ['user']
    textos = [b['text'] for b in cuerpo['messages'][0]['content']]
    assert textos[0] == 'Hola' and 'Aviso.' in textos[1] and textos[2] == 'Sigue'
    # Sonnet 5 no admite `system` a mitad: aunque fuese el último, va como texto.
    cuerpo, _ = anthropic.traducir_peticion({'model': 'claude-sonnet-5', 'messages': mensajes[:3]})
    assert [m['role'] for m in cuerpo['messages']] == ['user']


def test_una_llamada_sin_resultado_se_cierra_y_un_resultado_suelto_es_texto():
    cuerpo, _ = anthropic.traducir_peticion({'model': 'claude-opus-5-5', 'think': False, 'tools': [
        {'type': 'function', 'function': {'name': 'shell'}}], 'messages': [
        {'role': 'tool', 'tool_name': 'shell', 'content': 'suelto'},
        {'role': 'assistant', 'content': '', 'tool_calls': [{'id': 'call.raro/1',
                                                             'function': {'name': 'shell', 'arguments': '{}'}}]},
        {'role': 'user', 'content': 'otra cosa'}]})
    primero, llamada, cierre = cuerpo['messages']
    assert primero['role'] == 'user' and 'suelto' in primero['content'][0]['text']
    # Un id de otro proveedor con caracteres que Anthropic no acepta se limpia.
    assert llamada['content'] == [{'type': 'tool_use', 'id': 'call_raro_1', 'name': 'shell', 'input': {}}]
    assert cierre['content'][0] == {'type': 'tool_result', 'tool_use_id': 'call_raro_1', 'is_error': True,
                                    'content': '(sin resultado: la herramienta no llegó a ejecutarse)'}
    assert cierre['content'][1] == {'type': 'text', 'text': 'otra cosa'}
    # `think: false` no apaga el razonamiento (Opus 5.5 no deja): baja el esfuerzo.
    assert cuerpo['output_config'] == {'effort': 'low'} and cuerpo['thinking']['type'] == 'adaptive'


def test_el_razonamiento_firmado_vuelve_tal_cual():
    bloques = [{'type': 'thinking', 'thinking': 'Pienso.', 'signature': 'firma-1'},
               {'type': 'text', 'text': 'Leo.'},
               {'type': 'tool_use', 'id': 'toolu_1', 'name': 'read_file', 'input': {'path': 'a'}}]
    historia = [{'role': 'user', 'content': 'Hola'},
                {'role': 'assistant', 'content': 'Leo.', 'alfalfa_bloques': bloques,
                 'tool_calls': [{'id': 'toolu_1', 'function': {'name': 'read_file', 'arguments': {'path': 'a'}}}]},
                {'role': 'tool', 'tool_name': 'read_file', 'content': 'hola'}]
    herramientas = [{'type': 'function', 'function': {'name': 'read_file'}}]
    cuerpo, _ = anthropic.traducir_peticion({'model': 'claude-sonnet-5-5', 'messages': historia,
                                             'tools': herramientas})
    assert cuerpo['messages'][1]['content'] == bloques
    assert cuerpo['messages'][2]['content'][0]['tool_use_id'] == 'toolu_1'
    # Si el motor cambió las llamadas, los bloques ya no cuadran: se rehace sin el razonamiento.
    historia[1]['tool_calls'][0]['id'] = 'toolu_2'
    cuerpo, _ = anthropic.traducir_peticion({'model': 'claude-sonnet-5-5', 'messages': historia,
                                             'tools': herramientas})
    assert [b['type'] for b in cuerpo['messages'][1]['content']] == ['text', 'tool_use']


def test_el_formato_json_va_por_salidas_estructuradas_o_por_instruccion():
    esquema = {'type': 'object', 'additionalProperties': False, 'required': ['ok'],
               'properties': {'ok': {'type': 'boolean'}, 'texto': {'type': 'string', 'minLength': 12},
                              'lista': {'type': 'array', 'maxItems': 3, 'items': {
                                  'type': 'object', 'properties': {'x': {'type': 'string'}}}}}}
    cuerpo, _ = anthropic.traducir_peticion({'model': 'claude-haiku-5-5', 'format': esquema,
                                             'messages': [{'role': 'user', 'content': 'hola'}]})
    formato = cuerpo['output_config']['format']
    assert formato['type'] == 'json_schema'
    assert 'minLength' not in json.dumps(formato) and 'maxItems' not in json.dumps(formato)
    assert formato['schema']['properties']['lista']['items']['additionalProperties'] is False
    # Sin esquema (o con uno que no se puede adaptar), se pide JSON en el turno del usuario.
    cuerpo, _ = anthropic.traducir_peticion({'model': 'claude-haiku-5-5', 'format': 'json',
                                             'messages': [{'role': 'user', 'content': 'hola'}]})
    assert 'format' not in cuerpo.get('output_config', {})
    assert cuerpo['messages'][-1]['content'][-1]['text'] == anthropic.SOLO_JSON
    abierto = {'type': 'object', 'additionalProperties': True}
    cuerpo, _ = anthropic.traducir_peticion({'model': 'claude-haiku-5-5', 'format': abierto,
                                             'messages': [{'role': 'user', 'content': 'hola'}]})
    assert 'format' not in cuerpo.get('output_config', {})
    assert 'esquema JSON' in cuerpo['messages'][-1]['content'][-1]['text']
    r = anthropic.traducir_respuesta({'content': [{'type': 'text', 'text': '```json\n{"ok": true}\n```'}],
                                      'stop_reason': 'end_turn'}, 'm', con_formato=True)
    assert json.loads(r['message']['content']) == {'ok': True}


def test_un_modelo_antiguo_no_recibe_esfuerzo_ni_razonamiento():
    cuerpo, betas = anthropic.traducir_peticion({'model': 'claude-haiku-4-5', 'think': True,
                                                 'messages': [{'role': 'user', 'content': 'hola'}]}, 'high')
    assert 'thinking' not in cuerpo and 'output_config' not in cuerpo and betas == []
    cuerpo, betas = anthropic.traducir_peticion({'model': 'claude-opus-4-6',
                                                 'messages': [{'role': 'user', 'content': 'hola'}]}, 'xhigh')
    assert cuerpo['output_config'] == {'effort': 'high'} and 'block_binding' not in cuerpo['thinking']
    assert betas == []


def test_el_stream_se_junta_y_vuelve_al_formato_de_ollama():
    async def lineas(cuerpo):
        for linea in cuerpo.decode().split('\n'):
            yield linea

    eventos = mensaje(('thinking', 'Pienso.', 'firma-1'), ('text', 'Leo el archivo.'),
                      ('tool_use', 'toolu_1', 'read_file', {'path': 'a'}),
                      stop='tool_use', entrada=300, leida=1000, escrita=200, salida=80)
    datos = asyncio.run(anthropic.juntar(lineas(sse(*eventos))))
    r = anthropic.traducir_respuesta(datos, 'claude-haiku-5-5')
    assert r['done'] is True and r['done_reason'] == 'stop'
    # El prompt entero es lo no cacheado más lo escrito y lo leído en la caché.
    assert r['prompt_eval_count'] == 1500 and r['eval_count'] == 80
    assert r['alfalfa_cache'] == 1000 and r['alfalfa_cache_escritura'] == 200
    assert r['message']['content'] == 'Leo el archivo.' and r['message']['thinking'] == 'Pienso.'
    assert r['message']['tool_calls'] == [{'id': 'toolu_1', 'function': {'name': 'read_file',
                                                                         'arguments': {'path': 'a'}}}]
    assert r['message']['alfalfa_bloques'][0] == {'type': 'thinking', 'thinking': 'Pienso.', 'signature': 'firma-1'}
    cortada = anthropic.traducir_respuesta({'content': [], 'stop_reason': 'max_tokens'}, 'm')
    assert cortada['done'] is True and cortada['done_reason'] == 'length'
    with pytest.raises(anthropic.StreamCortado) as error:
        asyncio.run(anthropic.juntar(lineas(sse(*eventos[:-1]))))
    assert error.value.estado == 502
    saturado = [eventos[0], {'type': 'error', 'error': {'type': 'overloaded_error', 'message': 'Overloaded'}}]
    with pytest.raises(anthropic.StreamCortado) as error:
        asyncio.run(anthropic.juntar(lineas(sse(*saturado))))
    assert error.value.estado == 529 and 'Overloaded' in str(error.value)


def test_un_rechazo_de_seguridad_no_ejecuta_nada_y_lo_explica():
    r = anthropic.traducir_respuesta({'content': [{'type': 'tool_use', 'id': 't', 'name': 'shell', 'input': {}}],
                                      'stop_reason': 'refusal', 'stop_details': {'category': 'cyber'}}, 'm')
    assert 'tool_calls' not in r['message'] and 'alfalfa_bloques' not in r['message']
    assert 'filtros de seguridad' in r['message']['content'] and 'cyber' in r['message']['content']


def test_el_coste_cuenta_la_cache_escrita_y_el_tramo_largo():
    precio = anthropic.CATALOGO['claude-haiku-5-5']['precio']
    # 1000 sin caché, 2000 leídos y 1000 escritos, 100 de salida.
    esperado = (1000 * 0.10 + 2000 * 0.01 + 1000 * 0.125 + 100 * 0.50) / 1_000_000
    assert catalogo.coste(precio, 4000, 100, 2000, 1000) == pytest.approx(esperado)
    # Pasados 100K de prompt, toda la petición paga el tramo caro.
    assert catalogo.coste(precio, 150000, 100) == pytest.approx((150000 * 0.50 + 100 * 2.50) / 1_000_000)
    # Sin precio de escritura en el catálogo, 1,25 veces la entrada.
    assert catalogo.coste({'entrada': 2.0, 'salida': 10.0}, 1000, 0, 0, 1000) == pytest.approx(2.5 / 1000)


def test_el_tramo_del_catalogo_sobrevive_a_guardar_y_releer():
    crudo = {'anthropic': {'id': 'anthropic', 'name': 'Anthropic', 'env': ['ANTHROPIC_API_KEY'],
                           'npm': '@ai-sdk/anthropic', 'models': {'claude-haiku-5-5': {
                               'name': 'Claude Haiku 5.5', 'tool_call': True, 'limit': {'context': 1000000,
                                                                                        'output': 128000},
                               'cost': {'input': 0.1, 'output': 0.5, 'cache_read': 0.01, 'cache_write': 0.125,
                                        'tiers': [{'input': 0.5, 'output': 2.5, 'cache_read': 0.05,
                                                   'cache_write': 0.625,
                                                   'tier': {'type': 'context', 'size': 100000}}]}}}}}
    validado = catalogo.validar(crudo)
    precio = validado['anthropic']['modelos']['claude-haiku-5-5']['precio']
    assert precio['tramo'] == {'desde': 100000, 'entrada': 0.5, 'salida': 2.5, 'cache_lectura': 0.05,
                               'cache_escritura': 0.625}
    assert catalogo._revalidar(validado) == validado
    # Anthropic no es un compatible con OpenAI: no se confunde con uno del catálogo.
    assert not catalogo.compatible(validado['anthropic'])


def test_la_clave_sale_del_entorno_o_del_llavero(monkeypatch):
    proveedor = proveedores.obtener('anthropic')
    assert not proveedor.configurado()
    assert 'ANTHROPIC_API_KEY' in proveedor.falta() and '--guardar-clave anthropic' in proveedor.falta()
    llavero.guardar('anthropic', CLAVE)
    assert proveedor.configurado() and CLAVE in proveedores.secretos()
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'sk-ant-del-entorno')
    assert proveedor.clave() == 'sk-ant-del-entorno'


def test_la_clave_se_guarda_con_la_orden_y_no_se_imprime(capsys):
    args = SimpleNamespace(proveedores=False, guardar_clave='anthropic', borrar_clave=None,
                           proveedor_por_defecto=None, fijar_url=None, activar_codigo=False, cuota=False, json=True,
                           catalogo=False)
    assert orden.orden(args, entrada=io.StringIO(CLAVE + '\n')) == 0
    salida = capsys.readouterr().out
    assert CLAVE not in salida
    assert llavero.leer('anthropic') == CLAVE
    fila = next(f for f in json.loads(salida)['proveedores'] if f['id'] == 'anthropic')
    assert fila == {'id': 'anthropic', 'nombre': 'Anthropic', 'configurado': True, 'clave': True,
                    'variable': 'ANTHROPIC_API_KEY', 'donde': 'https://platform.claude.com/settings/keys'}
    args.guardar_clave, args.borrar_clave = None, 'anthropic'
    assert orden.orden(args) == 0
    assert llavero.leer('anthropic') is None


def test_los_modelos_de_anthropic_salen_en_su_seccion():
    assert proveedores.separar('anthropic/claude-haiku-5-5') == ('anthropic', 'claude-haiku-5-5')
    assert 'anthropic' in [p.id for p in proveedores.disponibles()]
    llavero.guardar('anthropic', CLAVE)
    filas = proveedores.obtener('anthropic').modelos()
    assert [f['name'] for f in filas] == ['anthropic/claude-haiku-5-5', 'anthropic/claude-sonnet-5-5',
                                          'anthropic/claude-opus-5-5']
    assert {f['proveedor'] for f in filas} == {'anthropic'}
    haiku = filas[0]
    assert haiku['nombre'] == 'Claude Haiku 5.5'
    # La ventana propia manda; el precio es del catálogo y el modo Ahorro compacta antes del tramo caro.
    assert haiku['ventana'] == 1000000 and haiku['precio'] == {'entrada': 0.1, 'salida': 0.5}
    assert haiku['barato_hasta'] == 100000 and haiku['tarifa_larga'] == {'entrada': 5.0, 'salida': 5.0}
    assert proveedores.obtener('anthropic').tarifa('claude-haiku-5-5') == {'desde': 100000, 'entrada': 5.0,
                                                                          'salida': 5.0}
    assert proveedores.obtener('anthropic').precio('claude-haiku-5-5')['tramo']['desde'] == 100000
    configuracion.cambiar('anthropic', 'modelos', ['claude-fable-5-1'])
    assert [f['name'] for f in proveedores.obtener('anthropic').modelos()][-1] == 'anthropic/claude-fable-5-1'
    modelos, fallos = proveedores.listar_modelos()
    assert 'anthropic/claude-sonnet-5-5' in [m['name'] for m in modelos] and 'anthropic' not in fallos


def test_show_da_ventana_y_capacidades_de_nube():
    info = anthropic.mostrar('claude-sonnet-5-5')
    assert {'tools', 'vision', 'thinking'} <= set(info['capabilities'])
    assert info['model_info']['anthropic.context_length'] == 1000000
    assert info['catalogo']['salida'] == 128000
    from alfalfa.proveedores.nativo.runtime import is_cloud
    assert is_cloud('claude-sonnet-5-5', info)


def test_la_url_de_anthropic_es_configurable_pero_con_https():
    assert proveedores.obtener('anthropic').url() == 'https://api.anthropic.com/v1'
    configuracion.cambiar('anthropic', 'url', 'http://proxy.example.com/v1')
    with pytest.raises(ValueError, match='HTTPS'):
        proveedores.obtener('anthropic').url()


class AnthropicFalso:
    """La API de mensajes, en memoria: devuelve streams SSE."""

    def __init__(self, respuestas):
        self.respuestas, self.cuerpos, self.cabeceras = list(respuestas), [], []

    def __call__(self, request):
        assert request.url.path == '/v1/messages'
        self.cuerpos.append(json.loads(request.content))
        self.cabeceras.append(dict(request.headers))
        estado, datos = self.respuestas.pop(0)
        if estado != 200:
            return httpx.Response(estado, json=datos, headers={'retry-after': '0'} if estado == 429 else {})
        return httpx.Response(200, content=sse(*datos), headers={'content-type': 'text/event-stream'})


@pytest.fixture
def falso(monkeypatch):
    def con(respuestas):
        api = AnthropicFalso(respuestas)
        real = anthropic.Transporte.__init__

        def init(self, proveedor, url, clave, tarea=None, timeout=None, transporte=None):
            real(self, proveedor, url, clave, tarea, timeout, httpx.MockTransport(api))
        monkeypatch.setattr(anthropic.Transporte, '__init__', init)
        return api
    return con


def test_un_turno_completo_con_herramientas_contra_claude(tmp_path, monkeypatch, falso):
    from alfalfa.terminal import TerminalManager
    (tmp_path / 'a.txt').write_text('hola desde a\n')
    monkeypatch.chdir(tmp_path)
    llavero.guardar('anthropic', CLAVE)
    api = falso([
        (200, mensaje(('thinking', 'Hay que leerlo.', 'firma-1'),
                      ('tool_use', 'toolu_X', 'read_file', {'path': 'a.txt'}),
                      stop='tool_use', entrada=500, escrita=4000, salida=20)),
        (200, mensaje(('text', 'Dice hola desde a.'), entrada=60, leida=4000, escrita=500, salida=10)),
    ])
    m = TerminalManager('Lee a.txt', home=tmp_path / 'home', model='anthropic/claude-haiku-5-5', effort='high',
                        auto=True)
    try:
        codigo = asyncio.run(asyncio.wait_for(m.run('Lee a.txt'), 60))
        assert codigo == 0, m.task.get('summary')
        assert m.task['provider'] == 'anthropic'
        cabeceras = api.cabeceras[0]
        assert cabeceras['x-api-key'] == CLAVE and cabeceras['anthropic-version'] == anthropic.VERSION_API
        assert cabeceras['anthropic-beta'] == anthropic.BETA_BLOQUES
        primera, segunda = api.cuerpos
        assert primera['model'] == 'claude-haiku-5-5' and primera['output_config']['effort'] == 'high'
        assert primera['system'][-1]['cache_control'] == {'type': 'ephemeral'}
        assert primera['tools'][-1]['cache_control'] == {'type': 'ephemeral'}
        assert primera['max_tokens'] <= 128000
        # El razonamiento firmado vuelve con la llamada, y el resultado va a su id.
        asistente = next(x for x in segunda['messages'] if x['role'] == 'assistant')
        assert asistente['content'][0] == {'type': 'thinking', 'thinking': 'Hay que leerlo.', 'signature': 'firma-1'}
        assert asistente['content'][1]['id'] == 'toolu_X'
        resultado = next(b for x in segunda['messages'] if x['role'] == 'user' for b in x['content']
                         if b.get('type') == 'tool_result')
        assert resultado['tool_use_id'] == 'toolu_X' and 'hola desde a' in json.dumps(resultado)
        # El coste: cada parte a su precio (lo escrito en caché a 0,125 $/M, lo leído a 0,01 $/M).
        esperado = ((500 * 0.10 + 4000 * 0.125 + 20 * 0.50)
                    + (60 * 0.10 + 4000 * 0.01 + 500 * 0.125 + 10 * 0.50)) / 1_000_000
        assert m.task['coste_usd'] == pytest.approx(esperado)
        assert m.task['tokens_entrada'] == 4500 + 4560 and m.task['tokens_salida'] == 30
        # Los bloques firmados nunca se escriben a disco.
        guardado = ''.join(p.read_text() for patron in ('*.json', '*.jsonl')
                           for p in (tmp_path / 'home').rglob(patron))
        assert 'messages.jsonl' in [p.name for p in (tmp_path / 'home').rglob('*.jsonl')]
        assert 'firma-1' not in guardado and 'Hay que leerlo.' not in guardado and CLAVE not in guardado
    finally:
        m.close()


def test_los_errores_de_anthropic_llegan_como_los_de_ollama(tmp_path, monkeypatch, falso):
    from alfalfa.terminal import TerminalManager
    monkeypatch.chdir(tmp_path)
    llavero.guardar('anthropic', CLAVE)
    falso([(401, {'type': 'error', 'error': {'type': 'authentication_error',
                                             'message': 'invalid x-api-key ' + CLAVE}})])
    m = TerminalManager('Hola', home=tmp_path / 'home', model='anthropic/claude-haiku-5-5', auto=True)
    try:
        codigo = asyncio.run(asyncio.wait_for(m.run('Hola'), 60))
        assert codigo != 0
        error = m.task['runtime_provider_error']
        assert error['provider'] == 'anthropic' and error['status_code'] == 401
        assert CLAVE not in json.dumps(m.task)
    finally:
        m.close()


def test_el_proveedor_por_defecto_elige_su_modelo(tmp_path, monkeypatch):
    from alfalfa.terminal import modelo_por_defecto
    monkeypatch.chdir(tmp_path)
    configuracion.cambiar(None, 'por_defecto', 'anthropic')
    assert modelo_por_defecto() == 'deepseek-v4.1-flash:cloud'
    llavero.guardar('anthropic', CLAVE)
    assert modelo_por_defecto() == 'anthropic/claude-sonnet-5-5'


def test_sin_herramientas_declaradas_las_llamadas_van_como_texto():
    # La compactación «codex» manda la historia con llamadas y resultados, pero sin `tools`.
    historia = [{'role': 'system', 'content': 'Reglas.'},
                {'role': 'user', 'content': 'Lee a.txt'},
                {'role': 'assistant', 'content': 'Voy.', 'tool_calls': [
                    {'id': 'toolu_1', 'function': {'name': 'read_file', 'arguments': {'path': 'a.txt'}}}],
                 'alfalfa_bloques': [{'type': 'thinking', 'thinking': 'x', 'signature': 'firma'},
                                     {'type': 'text', 'largo': 4}, {'type': 'tool_use', 'id': 'toolu_1'}]},
                {'role': 'tool', 'tool_name': 'read_file', 'content': 'hola', 'alfalfa_fallo': True},
                {'role': 'user', 'content': 'Resume la conversación.'}]
    cuerpo, _ = anthropic.traducir_peticion({'model': 'claude-haiku-5-5', 'think': False, 'messages': historia})
    assert 'tools' not in cuerpo
    tipos = [b['type'] for m in cuerpo['messages'] for b in (m['content'] if isinstance(m['content'], list) else [])]
    assert 'tool_use' not in tipos and 'tool_result' not in tipos and 'thinking' not in tipos
    usuario, asistente, resto = cuerpo['messages']
    assert asistente['content'] == [{'type': 'text', 'text': 'Voy.\n[Llamada a read_file: {"path": "a.txt"}]'}]
    assert resto['content'][0]['text'] == 'Resultado de read_file (falló):\nhola'
    assert resto['content'][1]['text'] == 'Resume la conversación.'


def test_un_resultado_fallido_va_con_is_error():
    cuerpo, _ = anthropic.traducir_peticion({'model': 'claude-haiku-5-5', 'tools': [
        {'type': 'function', 'function': {'name': 'shell'}}], 'messages': [
        {'role': 'user', 'content': 'Hola'},
        {'role': 'assistant', 'content': '', 'tool_calls': [{'id': 'toolu_1', 'function': {'name': 'shell',
                                                                                          'arguments': {}}}]},
        {'role': 'tool', 'tool_name': 'shell', 'content': '{"error": "no"}', 'alfalfa_fallo': True}]})
    resultado = cuerpo['messages'][-1]['content'][0]
    assert resultado['type'] == 'tool_result' and resultado['is_error'] is True


def test_el_precio_propio_manda_sobre_models_dev():
    # models.dev dice 0,1 $/M para la caché leída de Sonnet 5.5; la documentación de Anthropic, 0,20.
    assert catalogo.ficha('anthropic', 'claude-sonnet-5-5')['precio']['cache_lectura'] == 0.1
    precio = proveedores.obtener('anthropic').precio('claude-sonnet-5-5')
    assert precio['cache_lectura'] == 0.20 and precio['cache_escritura'] == 2.50
    # Un modelo que no está en la tabla propia usa el de models.dev.
    assert proveedores.obtener('anthropic').precio('claude-opus-5')['entrada'] == 5


def test_los_bloques_guardados_no_duplican_el_contexto_y_vuelven_iguales():
    grande = 'x' * 30000
    originales = [{'type': 'thinking', 'thinking': 'Escribo el archivo.', 'signature': 'firma-1'},
                  {'type': 'text', 'text': 'Voy '}, {'type': 'text', 'text': 'a escribir.'},
                  {'type': 'tool_use', 'id': 'toolu_1', 'name': 'write_file',
                   'input': {'path': 'a.txt', 'content': grande}}]
    r = anthropic.traducir_respuesta({'content': originales, 'stop_reason': 'tool_use'}, 'claude-haiku-5-5')
    mensaje = r['message']
    sin = {k: v for k, v in mensaje.items() if k != 'alfalfa_bloques'}
    # Lo que el motor mide (`len(json.dumps(messages))`) apenas crece: el texto y la llamada no van dos veces.
    assert len(json.dumps(mensaje)) - len(json.dumps(sin)) < 300
    historia = [{'role': 'user', 'content': 'Escribe a.txt'}, mensaje,
                {'role': 'tool', 'tool_name': 'write_file', 'content': 'ok'}]
    cuerpo, _ = anthropic.traducir_peticion({'model': 'claude-haiku-5-5', 'messages': historia, 'tools': [
        {'type': 'function', 'function': {'name': 'write_file'}}]})
    assert cuerpo['messages'][1]['content'] == originales


def test_un_razonamiento_sin_texto_ni_llamadas_se_guarda_y_vuelve():
    r = anthropic.traducir_respuesta({'content': [{'type': 'thinking', 'thinking': '', 'signature': 'firma-1'}],
                                      'stop_reason': 'end_turn'}, 'claude-haiku-5-5')
    assert r['done'] is True and r['done_reason'] == 'stop'
    assert r['message']['content'] == '' and 'tool_calls' not in r['message'] and 'thinking' not in r['message']
    assert r['message']['alfalfa_bloques'] == [{'type': 'thinking', 'thinking': '', 'signature': 'firma-1'}]
    cuerpo, _ = anthropic.traducir_peticion({'model': 'claude-haiku-5-5', 'messages': [
        {'role': 'user', 'content': 'Hola'}, r['message'], {'role': 'user', 'content': 'Sigue'}]})
    assert cuerpo['messages'][1]['content'] == [{'type': 'thinking', 'thinking': '', 'signature': 'firma-1'}]


def test_los_bloques_descartados_quedan_en_el_registro():
    datos = {'content': [{'type': 'text', 'text': 'Hola'}], 'stop_reason': 'end_turn',
             'input_transformations': [{'type': 'thinking_dropped', 'reason': 'prefix_binding_mismatch',
                                        'path': 'messages.3.content.0'},
                                       {'type': 'otra_cosa'}]}
    r = anthropic.traducir_respuesta(datos, 'claude-haiku-5-5')
    assert r['alfalfa_aviso'] == 'Anthropic descartó 1 bloque(s) de razonamiento anterior (prefix_binding_mismatch).'
    assert 'alfalfa_aviso' not in anthropic.traducir_respuesta({'content': [], 'input_transformations': []}, 'm')


@pytest.mark.parametrize('tipo, estado', [('invalid_request_error', 400), ('api_error', 500),
                                          ('rate_limit_error', 429), ('tipo_nuevo', 500)])
def test_un_error_a_mitad_del_stream_lleva_su_estado(tipo, estado):
    async def lineas(cuerpo):
        for linea in cuerpo.decode().split('\n'):
            yield linea
    eventos = [mensaje()[0], {'type': 'error', 'error': {'type': tipo, 'message': 'algo de ' + tipo}}]
    with pytest.raises(anthropic.StreamCortado) as error:
        asyncio.run(anthropic.juntar(lineas(sse(*eventos))))
    assert error.value.estado == estado and tipo in str(error.value)


def test_una_herramienta_que_falla_llega_con_is_error_y_el_descarte_queda_en_el_registro(tmp_path, monkeypatch,
                                                                                         falso):
    from alfalfa.terminal import TerminalManager
    monkeypatch.chdir(tmp_path)
    llavero.guardar('anthropic', CLAVE)
    segunda = mensaje(('text', 'No existe.'), entrada=60, salida=5)
    segunda[0]['message']['input_transformations'] = [{'type': 'thinking_dropped',
                                                       'reason': 'prefix_binding_mismatch'}]
    api = falso([(200, mensaje(('tool_use', 'toolu_X', 'read_file', {'path': 'no-existe.txt'}), stop='tool_use')),
                 (200, segunda)])
    m = TerminalManager('Lee no-existe.txt', home=tmp_path / 'home', model='anthropic/claude-haiku-5-5', auto=True)
    try:
        asyncio.run(asyncio.wait_for(m.run('Lee no-existe.txt'), 60))
        resultado = next(b for x in api.cuerpos[1]['messages'] if x['role'] == 'user' for b in x['content']
                         if b.get('type') == 'tool_result')
        assert resultado['tool_use_id'] == 'toolu_X' and resultado['is_error'] is True
        eventos = ''.join(p.read_text() for p in (tmp_path / 'home').rglob('*.jsonl'))
        assert 'Anthropic descartó 1 bloque(s) de razonamiento' in eventos
    finally:
        m.close()
