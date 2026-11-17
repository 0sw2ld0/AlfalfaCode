"""El texto de la respuesta mientras se escribe (proveedores/escritura.py): Ollama por trozos (`stream: true`) y
rehecho entero, el transporte PACAI que lo manda como `message_delta` y lo cierra con el texto final en
`message_end`, lo descartado, los secretos partidos entre trozos y quien lo mira por el socket."""
import asyncio
import json

import httpx
import pytest

from alfalfa.pacai import transporte as transporte_pacai
from alfalfa.pacai.transporte import reserva_de_secretos
from alfalfa.proveedores import escritura, ollama
from alfalfa.proveedores.escritura import LectorNDJSON
from alfalfa.proveedores.nativo.runtime import NativeRuntime
from alfalfa.terminal import TerminalManager
from tests.pacai_comun import Cliente, abrir, cerrar, herramienta, problemas
from tests.test_pacai_observadores import Mirando, carpeta, gestor  # noqa: F401  (carpeta es un fixture)


def lineas(*partes, done_reason='stop', **final):
    """Lo que manda Ollama con `stream: true`: una línea por trozo y la última con `done: true`."""
    salida = [json.dumps({'model': 'm', 'message': {'role': 'assistant', 'content': p}, 'done': False})
              for p in partes]
    salida.append(json.dumps({'model': 'm', 'message': {'role': 'assistant', 'content': ''}, 'done': True,
                              'done_reason': done_reason, 'prompt_eval_count': 7, 'eval_count': 3, **final}))
    return ('\n'.join(salida) + '\n').encode()


# -- leer el stream de Ollama ----------------------------------------------
def test_el_lector_avisa_de_cada_trozo_y_rehace_la_respuesta_entera():
    oidos = []
    lector = LectorNDJSON()
    datos = lineas('Ho', 'la ', 'mundo')
    with escritura.escuchando(lambda que, texto: oidos.append((que, texto))):
        for n in range(0, len(datos), 7):              # llega partido por cualquier sitio
            lector.trozo(datos[n:n + 7])
        respuesta = lector.respuesta()
    assert oidos == [('texto', 'Ho'), ('texto', 'la '), ('texto', 'mundo')]
    assert respuesta['message'] == {'role': 'assistant', 'content': 'Hola mundo'}
    assert respuesta['done'] is True and respuesta['done_reason'] == 'stop' and respuesta['eval_count'] == 3
    assert lector.tamano == len('Hola mundo')


def test_el_lector_junta_razonamiento_y_llamadas_y_respeta_los_errores():
    llamada = {'function': {'name': 'read_file', 'arguments': {'path': 'a'}}}
    datos = b'\n'.join([
        json.dumps({'message': {'role': 'assistant', 'content': '', 'thinking': 'Pienso '}, 'done': False}).encode(),
        json.dumps({'message': {'role': 'assistant', 'content': '', 'thinking': 'algo'}, 'done': False}).encode(),
        json.dumps({'message': {'role': 'assistant', 'content': '', 'tool_calls': [llamada]}, 'done': False}).encode(),
        json.dumps({'message': {'role': 'assistant', 'content': ''}, 'done': True, 'done_reason': 'stop'}).encode()])
    lector = LectorNDJSON()
    lector.trozo(datos)                                # sin salto final: la última línea llega al pedir la respuesta
    r = lector.respuesta()
    assert r['message'] == {'role': 'assistant', 'content': '', 'thinking': 'Pienso algo', 'tool_calls': [llamada]}
    malo = LectorNDJSON()
    malo.trozo(lineas('a')[:60] + b'\n' + json.dumps({'error': 'el modelo no existe'}).encode() + b'\n')
    assert malo.respuesta() == {'error': 'el modelo no existe'}


def test_una_respuesta_entera_de_otro_proveedor_pasa_tal_cual_y_no_se_repite_en_vivo():
    oidos = []
    entera = {'message': {'role': 'assistant', 'content': 'Hola'}, 'done': True, 'done_reason': 'stop'}
    lector = LectorNDJSON()
    with escritura.escuchando(lambda que, texto: oidos.append(texto)):
        lector.trozo(json.dumps(entera).encode())
        assert lector.respuesta() == entera
    assert oidos == []                                 # su texto sale con el mensaje final


def test_sin_nadie_escuchando_no_pasa_nada():
    assert not escritura.activa()
    escritura.escribir('x')
    escritura.descartar()
    with escritura.escuchando(None):
        assert not escritura.activa()


# -- en un turno de verdad con Ollama --------------------------------------
@pytest.fixture
def ollama_falso(tmp_path, monkeypatch):
    """Un Ollama que contesta por trozos si se le pide `stream: true` y entero si no."""
    monkeypatch.chdir(tmp_path)
    pedidas = []
    respuestas = []

    def api(peticion):
        cuerpo = json.loads(peticion.content or b'{}')
        if peticion.url.path == '/api/show':
            return httpx.Response(200, json={'capabilities': ['completion', 'tools'],
                                             'model_info': {'general.context_length': 32768}})
        if peticion.url.path != '/api/chat':
            return httpx.Response(200, json={})
        pedidas.append(cuerpo)
        estado, partes = respuestas.pop(0) if len(respuestas) > 1 else respuestas[0]
        if estado != 200:
            return httpx.Response(estado, json={'error': 'ocupado'})
        if cuerpo.get('stream'):
            return httpx.Response(200, content=lineas(*partes), headers={'content-type': 'application/x-ndjson'})
        return httpx.Response(200, json={'message': {'role': 'assistant', 'content': ''.join(partes)}, 'done': True,
                                         'done_reason': 'stop', 'prompt_eval_count': 7, 'eval_count': 3})

    def cliente(self, timeout, tarea=None, al_cambiar_cuota=None):
        return httpx.AsyncClient(base_url='http://127.0.0.1:11434', timeout=timeout,
                                 transport=httpx.MockTransport(api))

    async def sin_esperar(self, segundos):
        return None

    monkeypatch.setattr(ollama.Ollama, 'cliente', cliente)
    monkeypatch.setattr(NativeRuntime, '_retry_sleep', sin_esperar)
    return pedidas, respuestas


def turno(tmp_path, monkeypatch, en_vivo):
    oidos = []
    monkeypatch.setattr(TerminalManager, 'escribe_en_vivo', property(lambda self: en_vivo))
    monkeypatch.setattr(TerminalManager, 'texto_parcial', lambda self, que, texto='': oidos.append((que, texto)))
    m = TerminalManager('Saluda', home=tmp_path / 'alfalfa', model='llama3.2', auto=True)
    try:
        asyncio.run(asyncio.wait_for(m.run('Saluda'), 60))
    finally:
        m.close()
    return m, oidos


def test_con_un_cliente_que_lo_ensena_ollama_responde_por_trozos(tmp_path, monkeypatch, ollama_falso):
    pedidas, respuestas = ollama_falso
    respuestas.append((200, ['¡Ho', 'la', '!']))
    m, oidos = turno(tmp_path, monkeypatch, True)
    assert pedidas and pedidas[0]['stream'] is True
    assert [t for q, t in oidos if q == 'texto'] == ['¡Ho', 'la', '!']
    assert m.task['status'] == 'completed' and m.task['summary'].startswith('¡Hola!')


def test_sin_ese_cliente_la_peticion_sigue_entera(tmp_path, monkeypatch, ollama_falso):
    pedidas, respuestas = ollama_falso
    respuestas.append((200, ['Hola']))
    m, oidos = turno(tmp_path, monkeypatch, False)
    assert pedidas[0]['stream'] is False and oidos == []
    assert m.task['status'] == 'completed'


def test_un_reintento_descarta_lo_que_se_escribio_antes(tmp_path, monkeypatch, ollama_falso):
    pedidas, respuestas = ollama_falso
    respuestas += [(503, []), (200, ['Bien'])]
    m, oidos = turno(tmp_path, monkeypatch, True)
    assert len(pedidas) == 2 and m.task['status'] == 'completed'
    assert [q for q, _ in oidos] == ['descartar', 'descartar', 'texto']   # cada intento empieza limpio


# -- el transporte PACAI ---------------------------------------------------
async def escribir(c, *trozos):
    for trozo in trozos:
        c.t.texto_parcial(trozo)
    await asyncio.sleep(transporte_pacai.ESPERA_TEXTO + 0.05)
    return c.drenar()


async def test_el_texto_sale_por_trozos_y_el_mensaje_se_cierra_con_el_texto_entero():
    c = Cliente()
    run = await abrir(c)
    primero = await escribir(c, 'Ho', 'la ')
    assert [m['type'] for m in primero] == ['message_start', 'message_delta']
    inicio, delta = primero
    assert inicio['payload']['run_id'] == run and inicio['payload']['role'] == 'assistant'
    assert delta['payload'] == {'message_id': inicio['id'], 'blocks': [{'type': 'text', 'text': 'Hola '}]}
    segundo = await escribir(c, 'mun', 'do')
    assert [m['type'] for m in segundo] == ['message_delta']
    assert segundo[0]['payload']['blocks'][0]['text'] == 'mundo'
    c.t.emit('evento', rol='agent', texto='Hola mundo.', hora=3.0)
    fin = c.drenar()
    assert [m['type'] for m in fin] == ['message_end']
    assert fin[0]['payload'] == {'message_id': inicio['id'], 'blocks': [{'type': 'text', 'text': 'Hola mundo.'}]}
    assert await escribir(c) == []                     # lo que quedara pendiente ya no sale
    await cerrar(c)
    c.comprobar()


async def test_lo_descartado_se_retira_con_un_message_end_vacio():
    c = Cliente()
    await abrir(c)
    inicio = (await escribir(c, 'Esto no vale'))[0]
    c.t.texto_descartado()
    retirado = c.drenar()
    assert [m['type'] for m in retirado] == ['message_end']
    assert retirado[0]['payload'] == {'message_id': inicio['id'], 'blocks': []}
    otro = await escribir(c, 'Esto sí')
    assert otro[0]['type'] == 'message_start' and otro[0]['id'] != inicio['id']
    c.t.texto_descartado()
    c.t.texto_descartado()                             # sin nada escrito, no manda nada
    assert [m['type'] for m in c.drenar()] == ['message_end']
    await cerrar(c)
    c.comprobar()


async def test_si_el_modelo_pasa_a_una_herramienta_o_el_turno_acaba_lo_escrito_se_retira():
    c = Cliente()
    await abrir(c)
    inicio = (await escribir(c, 'Voy a leer'))[0]
    salida = herramienta(c, 'read_file', {'path': 'a.txt'})
    assert [m['type'] for m in salida] == ['message_end', 'tool_start']
    assert salida[0]['payload'] == {'message_id': inicio['id'], 'blocks': []}
    otro = (await escribir(c, 'Y ahora'))[0]
    fin = await cerrar(c)
    assert fin[0]['type'] == 'message_end' and fin[0]['payload'] == {'message_id': otro['id'], 'blocks': []}
    assert fin[-1]['type'] == 'run_finished'
    c.comprobar()


async def test_un_secreto_partido_entre_trozos_nunca_sale_a_medias():
    c = Cliente()
    c.t.secretos.add('clave-secreta-123456')
    await abrir(c)
    primero = await escribir(c, 'la clave es clave-sec')
    assert primero[1]['payload']['blocks'][0]['text'] == 'la clave es '   # «clave-sec» espera
    segundo = await escribir(c, 'reta-123456 y ya')
    assert segundo[0]['payload']['blocks'][0]['text'] == '[REDACTADO] y ya'
    assert 'clave-secreta' not in json.dumps(c.log)


def test_la_reserva_es_lo_justo_para_no_partir_un_secreto():
    assert reserva_de_secretos('hola mundo', ['sk-abcdef']) == 0
    assert reserva_de_secretos('mi clave sk-ab', ['sk-abcdef']) == 5
    assert reserva_de_secretos('termina en s', ['sk-abcdef', 'otro']) == 1
    assert reserva_de_secretos('', ['sk']) == 0


async def test_fuera_de_una_ejecucion_no_sale_nada():
    c = Cliente()
    c.saludar()
    assert await escribir(c, 'suelto') == []


# -- quien mira por el socket -----------------------------------------------
async def test_quien_llega_a_media_respuesta_ve_lo_escrito_y_lo_que_sigue(carpeta):  # noqa: F811
    c = Cliente()
    c.t.conectar(gestor(carpeta))
    await abrir(c)
    inicio = (await escribir(c, 'Primera parte. '))[0]
    o = await Mirando.conectar(carpeta / 'vivo.sock')
    foto = (await o.leer(3))[2]['payload']
    escrito = [m for m in foto['last_messages'] if m['type'] in ('message_start', 'message_delta')]
    assert [m['type'] for m in escrito] == ['message_start', 'message_delta'] and escrito[0]['id'] == inicio['id']
    assert escrito[1]['payload']['blocks'][0]['text'] == 'Primera parte. '
    await escribir(c, 'Segunda.')
    delta = await o.hasta(lambda m: m['type'] == 'message_delta')
    assert delta['payload']['blocks'][0]['text'] == 'Segunda.'
    c.t.emit('evento', rol='agent', texto='Primera parte. Segunda.', hora=2.0)
    fin = await o.hasta(lambda m: m['type'] == 'message_end')
    assert fin['payload']['blocks'] == [{'type': 'text', 'text': 'Primera parte. Segunda.'}]
    await cerrar(c)
    await c.t.al_salir()
    await o.eof()
    assert problemas(o.log) == [], problemas(o.log)
