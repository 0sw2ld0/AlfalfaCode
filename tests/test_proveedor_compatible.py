"""Proveedores compatibles con OpenAI del catálogo (OpenRouter, OpenCode Zen…), sin salir a internet."""
import asyncio
import io
import json
from types import SimpleNamespace

import httpx
import pytest

from alfalfa import catalogo, proveedores
from alfalfa.proveedores import compatible, configuracion, llavero, orden
from tests.test_catalogo import con_catalogo  # noqa: F401  (fixture)


def _llamar(proveedor, ruta, cuerpo, manejar, tarea=None):
    """Una petición en formato Ollama al cliente del proveedor, con la API de verdad falsa."""
    async def ir():
        async with proveedor.cliente(30, tarea=tarea, transporte=httpx.MockTransport(manejar)) as cliente:
            respuesta = await cliente.post(ruta, json=cuerpo)
            return respuesta.status_code, respuesta.json()
    return asyncio.run(ir())


def test_el_prefijo_de_un_compatible_se_reconoce(con_catalogo):  # noqa: F811
    assert proveedores.separar('openrouter/google/gemma:free') == ('openrouter', 'google/gemma:free')
    assert proveedores.separar('nube/libre') == ('nube', 'libre')
    # Uno local o que no habla el formato de OpenAI no es un prefijo: sigue siendo de Ollama.
    assert proveedores.separar('lmstudio/local') == ('ollama', 'lmstudio/local')
    assert proveedores.separar('anthropic/claude') == ('ollama', 'anthropic/claude')
    assert proveedores.separar('usuario/modelo') == ('ollama', 'usuario/modelo')
    # Un modelo de Ollama con el mismo espacio de nombres se guarda con `ollama/` y vuelve igual.
    nombre = proveedores.con_prefijo('ollama', 'nube/mio')
    assert nombre == 'ollama/nube/mio'
    assert proveedores.separar(nombre) == ('ollama', 'nube/mio')
    assert proveedores.con_prefijo('ollama', 'usuario/modelo') == 'usuario/modelo'


def test_sin_clave_explica_cual_falta_y_donde_conseguirla(con_catalogo):  # noqa: F811
    nube = proveedores.obtener('nube')
    assert not nube.configurado()
    with pytest.raises(ValueError) as error:
        nube.cliente(30)
    texto = str(error.value)
    assert 'NUBE_API_KEY' in texto and 'https://nube.example/claves' in texto and '--guardar-clave nube' in texto
    abierto = proveedores.obtener('openrouter')
    assert 'OPENROUTER_API_KEY' in abierto.falta() and 'openrouter.ai/settings/keys' in abierto.falta()


def test_la_clave_se_guarda_atada_a_la_url_de_ese_momento(con_catalogo, monkeypatch, capsys):  # noqa: F811
    args = SimpleNamespace(proveedores=False, guardar_clave='nube', borrar_clave=None, proveedor_por_defecto=None,
                           fijar_url=None, activar_codigo=False, cuota=False, json=True, catalogo=False)
    assert orden.orden(args, entrada=io.StringIO('sk-nube-123456\n')) == 0
    salida = capsys.readouterr().out
    assert 'sk-nube-123456' not in salida
    assert llavero.leer('nube') == 'sk-nube-123456'
    assert configuracion.de('nube') == {'url': 'https://api.nube.example/v1', 'guardada': True}
    assert proveedores.obtener('nube').configurado()
    assert 'nube' in proveedores.compatibles_guardados()
    assert 'sk-nube-123456' in proveedores.secretos()
    # Si el catálogo cambia la URL, la clave no la sigue.
    catalogo._memoria['proveedores']['nube']['api'] = 'https://evil.example/v1'
    assert proveedores.obtener('nube').url() == 'https://api.nube.example/v1'
    args.guardar_clave, args.borrar_clave = None, 'nube'
    assert orden.orden(args) == 0
    assert llavero.leer('nube') is None and 'guardada' not in configuracion.de('nube')


def test_solo_los_conocidos_leen_su_variable_de_entorno(con_catalogo, monkeypatch):  # noqa: F811
    monkeypatch.setenv('OPENROUTER_API_KEY', 'sk-or-123456')
    monkeypatch.setenv('NUBE_API_KEY', 'sk-nube-entorno')
    assert proveedores.obtener('openrouter').clave() == 'sk-or-123456'
    # El nombre de la variable de los demás viene del catálogo: no se lee, sólo se explica.
    assert proveedores.obtener('nube').clave() is None
    # Una clave en el Llavero sin `guardada` (no la guardó AlfalfaCode para ese proveedor) tampoco.
    llavero.guardar('nube', 'sk-nube-llavero')
    assert proveedores.obtener('nube').clave() is None


def test_una_orden_rechaza_proveedores_que_no_son_compatibles(con_catalogo):  # noqa: F811
    args = SimpleNamespace(proveedores=False, guardar_clave='lmstudio', borrar_clave=None, proveedor_por_defecto=None,
                           fijar_url=None, activar_codigo=False, cuota=False, json=True, catalogo=False)
    assert orden.orden(args, entrada=io.StringIO('sk-local-123456\n')) == 1
    assert llavero.leer('lmstudio') is None


def test_api_show_y_tags_con_lo_del_catalogo(con_catalogo):  # noqa: F811
    llavero.guardar('openrouter', 'sk-or-123456')

    def nunca(request):
        raise AssertionError('no debe salir a la red')

    estado, info = _llamar(proveedores.obtener('openrouter'), '/api/show', {'model': 'google/gemma:free'}, nunca)
    assert estado == 200
    assert info['model_info'] == {'catalogo.context_length': 200000}
    assert set(info['capabilities']) == {'completion', 'tools', 'thinking', 'vision'}
    assert info['remote_host'] == 'https://openrouter.ai/api/v1'
    assert info['catalogo']['salida'] == 32000
    # Uno añadido a mano que el catálogo no conoce: con herramientas y sin ventana (el motor pone la suya).
    _, info = _llamar(proveedores.obtener('openrouter'), '/api/show', {'model': 'otro/raro'}, nunca)
    assert info['capabilities'] == ['completion', 'tools'] and info['model_info'] == {}


def test_chat_completions_ida_y_vuelta_con_herramientas(con_catalogo):  # noqa: F811
    llavero.guardar('nube', 'sk-nube-123456')
    configuracion.cambiar('nube', 'guardada', True)
    vistas = []

    def manejar(request):
        vistas.append((request.url, dict(request.headers), json.loads(request.content)))
        return httpx.Response(200, json={
            'choices': [{'finish_reason': 'tool_calls', 'message': {
                'role': 'assistant', 'content': None, 'reasoning_content': 'pienso',
                'tool_calls': [{'id': 'call_2', 'type': 'function',
                                'function': {'name': 'read_file', 'arguments': '{"path": "b.py"}'}}]}}],
            'usage': {'prompt_tokens': 1200, 'completion_tokens': 30,
                      'prompt_tokens_details': {'cached_tokens': 1000}}})

    pedido = {'model': 'libre', 'stream': False, 'think': True, 'options': {'num_predict': 99999},
              'messages': [{'role': 'system', 'content': 'Eres útil.'},
                           {'role': 'user', 'content': 'Mira', 'images': ['iVBORw0KGgo=']},
                           {'role': 'assistant', 'content': '', 'tool_calls': [
                               {'function': {'name': 'read_file', 'arguments': {'path': 'a.py'}}}]},
                           {'role': 'tool', 'tool_name': 'read_file', 'content': 'print(1)'}],
              'tools': [{'type': 'function', 'function': {'name': 'read_file', 'description': 'Lee',
                                                          'parameters': {'type': 'object'}}}]}
    estado, respuesta = _llamar(proveedores.obtener('nube'), '/api/chat', pedido, manejar, tarea={'effort': 'xhigh'})
    assert estado == 200
    url, cabeceras, cuerpo = vistas[0]
    assert str(url) == 'https://api.nube.example/v1/chat/completions'
    assert cabeceras['authorization'] == 'Bearer sk-nube-123456'
    assert cuerpo['max_tokens'] == 32000, 'la salida se acota a la del catálogo'
    assert cuerpo['reasoning_effort'] == 'high', 'xhigh no lo admite: el más cercano por debajo'
    mensajes = cuerpo['messages']
    assert mensajes[0] == {'role': 'system', 'content': 'Eres útil.'}
    assert mensajes[1]['content'][1]['image_url']['url'].startswith('data:image/png;base64,')
    llamada = mensajes[2]['tool_calls'][0]
    assert llamada['function'] == {'name': 'read_file', 'arguments': '{"path": "a.py"}'}
    assert mensajes[3] == {'role': 'tool', 'tool_call_id': llamada['id'], 'content': 'print(1)'}
    assert cuerpo['tools'][0]['function']['name'] == 'read_file'
    assert respuesta['message']['tool_calls'] == [{'id': 'call_2', 'function': {'name': 'read_file',
                                                                             'arguments': {'path': 'b.py'}}}]
    assert respuesta['message']['thinking'] == 'pienso'
    assert (respuesta['prompt_eval_count'], respuesta['eval_count'], respuesta['alfalfa_cache']) == (1200, 30, 1000)
    assert respuesta['done'] and respuesta['done_reason'] == 'stop'


def test_openrouter_razona_con_su_campo_y_se_identifica(con_catalogo):  # noqa: F811
    llavero.guardar('openrouter', 'sk-or-123456')
    vistas = []

    def manejar(request):
        vistas.append((dict(request.headers), json.loads(request.content)))
        return httpx.Response(200, json={'choices': [{'finish_reason': 'length',
                                                      'message': {'content': 'hola', 'reasoning': 'r'}}]})

    pedido = {'model': 'google/gemma:free', 'think': False, 'messages': [{'role': 'user', 'content': 'hola'}]}
    _, respuesta = _llamar(proveedores.obtener('openrouter'), '/api/chat', pedido, manejar)
    cabeceras, cuerpo = vistas[0]
    assert cabeceras['x-title'] == 'AlfalfaCode'
    assert 'reasoning' not in cuerpo, 'sin «none» entre sus esfuerzos no se pide apagarlo'
    assert respuesta['done_reason'] == 'length' and respuesta['message']['content'] == 'hola'


def test_los_modelos_con_paquete_de_openai_van_por_responses(con_catalogo):  # noqa: F811
    llavero.guardar('opencode', 'sk-zen-123456')
    vistas = []

    def manejar(request):
        vistas.append(str(request.url))
        return httpx.Response(200, json={'status': 'completed', 'output': [
            {'type': 'message', 'content': [{'type': 'output_text', 'text': 'listo'}]}],
            'usage': {'input_tokens': 10, 'output_tokens': 2}})

    _, respuesta = _llamar(proveedores.obtener('opencode'), '/api/chat',
                           {'model': 'gpt-6-x', 'messages': [{'role': 'user', 'content': 'hola'}]}, manejar)
    assert vistas == ['https://opencode.ai/inference/openai/v1/responses']
    assert respuesta['message']['content'] == 'listo'


def test_los_errores_del_proveedor_llegan_sin_la_clave(con_catalogo):  # noqa: F811
    llavero.guardar('openrouter', 'sk-or-123456')

    def manejar(request):
        return httpx.Response(429, headers={'retry-after': '7'},
                              json={'error': {'message': 'Rate limit exceeded', 'code': 429}})

    async def ir():
        async with proveedores.obtener('openrouter').cliente(30, transporte=httpx.MockTransport(manejar)) as c:
            return await c.post('/api/chat', json={'model': 'google/gemma:free', 'messages': []})
    respuesta = asyncio.run(ir())
    assert respuesta.status_code == 429 and respuesta.headers['retry-after'] == '7'
    assert respuesta.json() == {'error': 'Rate limit exceeded'}


def test_la_lista_trae_los_gratis_con_aviso_y_la_clave_que_falta(con_catalogo, monkeypatch):  # noqa: F811
    from alfalfa.proveedores import ollama
    monkeypatch.setattr(ollama.Ollama, 'modelos', lambda self: [])
    llavero.guardar('openrouter', 'sk-or-123456')
    configuracion.cambiar('openrouter', 'modelos', ['de-pago'])
    modelos, fallos = proveedores.listar_modelos()
    por_nombre = {m['name']: m for m in modelos}
    gemma = por_nombre['openrouter/google/gemma:free']
    assert gemma['gratis'] and gemma['aviso'] == 'Gratis: puede usar tus datos para entrenar'
    assert gemma['ventana'] == 200000 and gemma['precio'] == {'entrada': 0, 'salida': 0}
    assert 'falta_clave' not in gemma
    pago = por_nombre['openrouter/de-pago']
    assert not pago.get('gratis') and pago['precio'] == {'entrada': 3, 'salida': 15}
    libre = por_nombre['nube/libre']
    assert libre['falta_clave']['variable'] == 'NUBE_API_KEY'
    assert libre['falta_clave']['donde'] == 'https://nube.example/claves'
    assert not any(n.startswith(('lmstudio/', 'zai-coding-plan/', 'opencode/big-pickle')) for n in por_nombre)


def test_estado_de_los_compatibles_sin_secretos(con_catalogo):  # noqa: F811
    llavero.guardar('openrouter', 'sk-or-123456')
    filas = {f['id']: f for f in orden.estado()['proveedores']}
    assert filas['openrouter']['configurado'] and filas['openrouter']['variable'] == 'OPENROUTER_API_KEY'
    assert filas['opencode']['nombre'] == 'OpenCode Zen' and not filas['opencode']['configurado']
    assert 'sólo se pueden usar desde OpenCode' in filas['opencode']['aviso']
    assert 'sk-or-123456' not in json.dumps(filas)


def test_esfuerzo_mas_cercano():
    ficha = {'esfuerzos': ['none', 'low', 'high', 'max']}
    assert compatible.esfuerzo_para(ficha, 'medium', True) == 'low'
    assert compatible.esfuerzo_para(ficha, 'xhigh', True) == 'high'
    assert compatible.esfuerzo_para(ficha, 'max', True) == 'max'
    assert compatible.esfuerzo_para(ficha, 'high', False) == 'none'
    assert compatible.esfuerzo_para({'esfuerzos': ['high']}, 'low', True) == 'high'
    assert compatible.esfuerzo_para({}, 'high', True) is None


def test_el_catalogo_por_la_terminal(con_catalogo, capsys):  # noqa: F811
    args = SimpleNamespace(proveedores=False, guardar_clave=None, borrar_clave=None, proveedor_por_defecto=None,
                           fijar_url=None, activar_codigo=False, cuota=False, json=True, catalogo=True)
    assert orden.orden(args) == 0
    datos = json.loads(capsys.readouterr().out)
    assert datos['tipo'] == 'catalogo' and datos['gratis'] == 2
    assert set(datos['gratis_por_proveedor']) == {'openrouter', 'nube'}


def _runtime(tmp_path, monkeypatch, modelo):
    from alfalfa.proveedores.nativo.runtime import NativeRuntime
    from alfalfa.terminal import TerminalManager
    monkeypatch.chdir(tmp_path)
    manager = TerminalManager('x', home=tmp_path / 'home', model=modelo)
    return manager, NativeRuntime(manager, manager.task)


def test_el_motor_toma_ventana_salida_y_precio_del_catalogo(con_catalogo, tmp_path, monkeypatch):  # noqa: F811
    llavero.guardar('openrouter', 'sk-or-123456')
    manager, rt = _runtime(tmp_path, monkeypatch, 'openrouter/de-pago')
    try:
        proveedor, modelo, cliente = rt._cliente(httpx.Timeout(5))
        asyncio.run(cliente.aclose())
        assert (proveedor.id, modelo) == ('openrouter', 'de-pago')
        info = compatible.mostrar(proveedor.datos(), proveedor.ficha(modelo), modelo, proveedor.url())
        assert rt._ventana(modelo, info) == 200000
        assert rt._salida_del_catalogo(info) == 32000
        # Sin ventana del proveedor ni en /api/show, la de la ficha.
        assert rt._ventana(modelo, {'remote_host': 'x', 'model_info': {}}) == 200000
        # Coste: 1M de entrada (800K de caché) y 100K de salida a 3/15 $ por millón y 0,1 la caché.
        rt._record_response_usage({'prompt_eval_count': 1_000_000, 'eval_count': 100_000,
                                   'alfalfa_cache': 800_000, 'message': {}}, {})
        assert manager.task['coste_usd'] == pytest.approx(0.6 + 0.08 + 1.5)
        assert (manager.task['tokens_entrada'], manager.task['tokens_salida']) == (1_000_000, 100_000)
    finally:
        manager.close()


def test_lo_que_dice_el_proveedor_manda_sobre_el_catalogo(con_catalogo, tmp_path, monkeypatch):  # noqa: F811
    manager, rt = _runtime(tmp_path, monkeypatch, 'glm-5.3-flash:cloud')
    try:
        proveedor, modelo, cliente = rt._cliente(httpx.Timeout(5))
        asyncio.run(cliente.aclose())
        # Ollama Cloud: la ficha existe (1M) pero /api/show dice 202752 y manda.
        info = {'remote_host': 'https://ollama.com', 'model_info': {'glm.context_length': 202752}}
        assert rt._ventana(modelo, info) == 202752
        assert rt._ventana(modelo, {'remote_host': 'https://ollama.com', 'model_info': {}}) == 1000000
        # Ollama no cobra por token: sin coste aunque el catálogo tenga precio.
        rt._record_response_usage({'prompt_eval_count': 1000, 'eval_count': 10, 'message': {}}, {})
        assert 'coste_usd' not in manager.task
    finally:
        manager.close()


def test_el_turno_guarda_su_gasto_y_el_protocolo_lo_cuenta():
    from alfalfa import sesiones
    from alfalfa.stdio import gasto
    task = {'total_tokens': 100, 'coste_usd': 0.5}
    turno = sesiones.abrir_turno(task, 'hola', 0)
    task.update(total_tokens=350, coste_usd=0.75)
    sesiones.cerrar_turno(task, turno['id'], 'completed', 2, gasto=sesiones.gasto_del_turno(task, 100, 0.5))
    assert task['turnos'][0]['tokens'] == 250 and task['turnos'][0]['coste'] == pytest.approx(0.25)
    assert gasto(task, task['turnos'][0]) == {'coste': 0.75, 'tokens_turno': 250, 'coste_turno': 0.25}
    assert sesiones.turnos(task)[0]['coste'] == pytest.approx(0.25)
    # Sin precio, ningún coste: 0 $ no es lo mismo que «no se sabe».
    assert gasto({'total_tokens': 5}) == {}


def test_un_modelo_de_ollama_con_el_nombre_de_un_proveedor_sigue_en_ollama(con_catalogo, monkeypatch):  # noqa: F811
    """`deepseek/deepseek-r1:7b` es un modelo de Ollama de un autor que se llama como un proveedor del catálogo."""
    from alfalfa.proveedores import ollama
    catalogo._memoria['proveedores']['deepseek'] = dict(catalogo._memoria['proveedores']['nube'], id='deepseek',
                                                        nombre='DeepSeek', api='https://api.deepseek.com/v1')
    assert proveedores.es_prefijo('deepseek')

    def manejar(request):
        return httpx.Response(200, json={'models': [{'name': 'deepseek/deepseek-r1:7b'}, {'name': 'huihui/gemma:1b'}]})

    monkeypatch.setattr(ollama, 'cliente_de_lista', lambda: httpx.Client(transport=httpx.MockTransport(manejar)))
    filas = {f['nombre']: f for f in ollama.Ollama().modelos()}
    # Se guarda con `ollama/` y vuelve a Ollama; el nombre visible sigue siendo el corto.
    assert filas['deepseek/deepseek-r1:7b']['name'] == 'ollama/deepseek/deepseek-r1:7b'
    assert proveedores.separar(filas['deepseek/deepseek-r1:7b']['name']) == ('ollama', 'deepseek/deepseek-r1:7b')
    assert filas['huihui/gemma:1b']['name'] == 'huihui/gemma:1b'
    assert proveedores.para_modelo('ollama/deepseek/deepseek-r1:7b')[0].id == 'ollama'
