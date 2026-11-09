"""Las imágenes según el modelo: quién las ve, qué dice la lista y qué pasa si no las ve.

- `--modelos --json` dice de cada modelo si ve imágenes (`vision`): true, false o null
  si no se sabe. Sale de `/api/show` (Ollama), de lo propio de cada proveedor, del
  servicio Alfalfa o del catálogo de models.dev.
- Un mensaje con imágenes para un modelo que no las ve se rechaza antes de empezar el
  turno, con un error que nombra modelos con visión que el usuario tiene de verdad.
  Con visión delegada, las imágenes se describen con ella en vez de fallar.
- Las imágenes del historial (o de una herramienta) no rompen el turno de un modelo
  sin visión: se describen o se dice que no se envían.
"""
import asyncio
import base64
import json

import httpx
import pytest

from alfalfa import ajustes, imagenes, proveedores
from alfalfa.proveedores import alfalfa as proveedor_alfalfa
from alfalfa.proveedores import compatible, ollama, openai
from alfalfa.proveedores.nativo.runtime import NativeRuntime
from alfalfa.terminal import TerminalManager
from tests.test_compactar_sesion import Cliente

PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5E'
                       'rkJggg==')


@pytest.fixture
def foto(tmp_path):
    path = tmp_path / 'captura.png'
    path.write_bytes(PNG)
    return path


@pytest.fixture
def m(tmp_path, monkeypatch):
    (tmp_path / 'project').mkdir()
    monkeypatch.chdir(tmp_path / 'project')
    manager = TerminalManager('Encargo.', home=tmp_path / 'alfalfa', model='glm-5.3:cloud')
    yield manager
    manager.close()


# -- la lista de modelos ---------------------------------------------------------


def _ollama(capacidades):
    """Un Ollama falso: `/api/tags` y `/api/show` con las capacidades de cada modelo (None: falla)."""
    def manejar(request):
        if request.url.path == '/api/tags':
            return httpx.Response(200, json={'models': [{'name': n} for n in capacidades]})
        nombre = json.loads(request.content)['model']
        if capacidades[nombre] is None:
            return httpx.Response(500, json={'error': 'roto'})
        return httpx.Response(200, json={'capabilities': capacidades[nombre]})
    return lambda: httpx.Client(transport=httpx.MockTransport(manejar))


def test_ollama_dice_si_cada_modelo_ve_imagenes_por_su_api_show(monkeypatch):
    monkeypatch.setattr(ollama, 'cliente_de_lista', _ollama({
        'qwen3-vl:8b': ['completion', 'tools', 'vision'], 'qwen3:8b': ['completion', 'tools'], 'raro:1b': None}))
    filas = {f['name']: f['vision'] for f in ollama.Ollama().modelos()}
    assert filas == {'qwen3-vl:8b': True, 'qwen3:8b': False, 'raro:1b': None}


def test_si_api_tags_trae_las_capacidades_no_hace_falta_api_show(monkeypatch):
    def manejar(request):
        assert request.url.path == '/api/tags', 'no debe preguntar a /api/show'
        return httpx.Response(200, json={'models': [{'name': 'kimi-k3.1-pro:cloud', 'capabilities': ['vision']},
                                                    {'name': 'qwen3:8b', 'capabilities': ['tools']}]})
    monkeypatch.setattr(ollama, 'cliente_de_lista', lambda: httpx.Client(transport=httpx.MockTransport(manejar)))
    assert [f['vision'] for f in ollama.Ollama().modelos()] == [True, False]


def test_si_api_show_no_responde_vale_el_catalogo_de_ollama_cloud(monkeypatch):
    monkeypatch.setattr(ollama, 'cliente_de_lista', _ollama({'glm-5.3-flash:cloud': None}))
    assert ollama.Ollama().modelos()[0]['vision'] is True  # el catálogo dice que GLM 5.3 Flash ve


def test_openai_lo_dice_por_su_catalogo_y_no_sabe_de_un_modelo_anadido(monkeypatch):
    from alfalfa.proveedores import configuracion
    monkeypatch.setattr(configuracion, 'de', lambda ident: {'modelos': ['gpt-inventado-9']})
    filas = {f['name']: f['vision'] for f in openai.OpenAI().modelos()}
    assert filas == {'openai/gpt-6-luna': True, 'openai/gpt-inventado-9': None}
    assert openai.mostrar('gpt-inventado-9')['vision_conocida'] is False
    assert proveedores.vision_de(openai.mostrar('gpt-inventado-9')) is None
    assert proveedores.vision_de(openai.mostrar('gpt-6-luna')) is True


def test_un_compatible_sin_ficha_no_se_sabe():
    proveedor = compatible.Compatible('openrouter')
    assert proveedor.fila({'id': 'alguien/sin-ficha', 'nombre': 'Sin ficha'})['vision'] is None
    assert proveedor.fila({'id': 'x/y', 'nombre': 'Con ficha', 'vision': False})['vision'] is False
    info = compatible.mostrar({'id': 'openrouter'}, None, 'alguien/sin-ficha', 'https://x')
    assert proveedores.vision_de(info) is None
    info = compatible.mostrar({'id': 'openrouter'}, {'id': 'x', 'vision': False}, 'x', 'https://x')
    assert proveedores.vision_de(info) is False


def test_alfalfa_lo_toma_de_las_capacidades_del_servicio(monkeypatch):
    filas = [{'name': 'gpt-6-luna', 'capabilities': ['completion', 'tools', 'vision']},
             {'name': 'glm-5.3', 'capabilities': ['completion', 'tools']},
             {'name': 'viejo'}]  # un servicio anterior no manda capacidades
    monkeypatch.setattr(proveedor_alfalfa.Alfalfa, '_pedir', lambda self, metodo, ruta: {'models': filas})
    vistas = {f['name']: f['vision'] for f in proveedor_alfalfa.Alfalfa(firmante=object()).modelos()}
    assert vistas == {'alfalfa/gpt-6-luna': True, 'alfalfa/glm-5.3': False, 'alfalfa/viejo': None}


def test_toda_fila_de_la_lista_lleva_vision(monkeypatch):
    class Falso(proveedores.Proveedor):
        id, nombre = 'ollama', 'Ollama'

        def modelos(self):
            return [{'name': 'a', 'vision': True}, {'name': 'b'}, {'name': 'c', 'vision': 'quizá'}]

    monkeypatch.setattr(proveedores, 'disponibles', lambda: [Falso()])
    from alfalfa import catalogo
    monkeypatch.setattr(catalogo, 'gratis_ahora', lambda: [])
    modelos, _ = proveedores.listar_modelos()
    assert [(f['name'], f['vision']) for f in modelos] == [('a', True), ('b', None), ('c', None)]


def test_los_sugeridos_son_los_que_el_usuario_puede_usar(monkeypatch):
    filas = [{'name': 'qwen3:8b', 'nombre': 'qwen3:8b', 'proveedor': 'ollama', 'vision': False},
             {'name': 'openrouter/x:free', 'nombre': 'X gratis', 'proveedor': 'openrouter', 'vision': True,
              'falta_clave': {'texto': 'falta'}},
             {'name': 'alfalfa/gpt-6-luna', 'nombre': 'GPT-6 Luna - Alfalfa', 'proveedor': 'alfalfa', 'vision': True},
             {'name': 'qwen3-vl:8b', 'nombre': 'qwen3-vl:8b', 'proveedor': 'ollama', 'vision': True}]
    monkeypatch.setattr(proveedores, 'listar_modelos', lambda: (filas, {}))
    assert proveedores.con_vision('qwen3:8b') == [('qwen3-vl:8b', 'qwen3-vl:8b'),
                                                 ('alfalfa/gpt-6-luna', 'GPT-6 Luna - Alfalfa')]
    texto = proveedores.texto_sin_vision('qwen3:8b')
    assert texto.startswith('El modelo qwen3:8b no ve imágenes')
    assert 'qwen3-vl:8b' in texto and 'GPT-6 Luna - Alfalfa (alfalfa/gpt-6-luna)' in texto
    assert 'deepseek' not in texto
    assert 'Ninguno de tus modelos' in proveedores.texto_sin_vision('qwen3:8b', [])


# -- antes del turno ----------------------------------------------------------------


def _sin_vision(monkeypatch, vision=False):
    async def vision_del_modelo(modelo, timeout=15):
        return vision
    monkeypatch.setattr(proveedores, 'vision_del_modelo', vision_del_modelo)
    monkeypatch.setattr(proveedores, 'con_vision', lambda modelo=None, limite=3: [('qwen3-vl:8b', 'Qwen3 VL')])


def test_un_mensaje_con_imagenes_se_rechaza_antes_del_turno(m, foto, monkeypatch):
    _sin_vision(monkeypatch)
    texto, sugeridos = asyncio.run(m.sin_vision(imagenes.con_etiquetas('Mira', [str(foto)])))
    assert 'glm-5.3:cloud no ve imágenes' in texto and 'Qwen3 VL (qwen3-vl:8b)' in texto
    assert sugeridos == [('qwen3-vl:8b', 'Qwen3 VL')]
    # Sin imágenes, o si no se sabe, o con visión delegada, se sigue.
    assert asyncio.run(m.sin_vision('Sin imágenes')) is None
    _sin_vision(monkeypatch, vision=None)
    assert asyncio.run(m.sin_vision(imagenes.con_etiquetas('Mira', [str(foto)]))) is None
    _sin_vision(monkeypatch)
    monkeypatch.setattr(ajustes, 'vision_delegada', lambda: {'modelo': 'gpt-6-luna', 'nombre': 'Luna'})
    assert asyncio.run(m.sin_vision(imagenes.con_etiquetas('Mira', [str(foto)]))) is None


VISION = '''
import json, sys
from unittest.mock import patch
from alfalfa import proveedores
from alfalfa.terminal import main
from alfalfa.proveedores.nativo.runtime import NativeRuntime
async def inference(self, client, endpoint, payload, **kwargs):
    if endpoint == '/api/show':
        return {'capabilities': ['tools'], 'model_info': {'x.context_length': 131072}}
    with open('requests.jsonl', 'a') as stream:
        stream.write(json.dumps(payload['messages']) + '\\n')
    return dict(message=dict(role='assistant', content='Respuesta a: ' + payload['messages'][-1]['content'][-30:]),
                done=True, done_reason='stop', prompt_eval_count=900, eval_count=10)
async def sin_vision(modelo, timeout=15):
    return False
with patch.object(NativeRuntime, 'request', inference), \\
        patch.object(proveedores, 'vision_del_modelo', sin_vision), \\
        patch.object(proveedores, 'con_vision', lambda modelo=None, limite=3: [('qwen3-vl:8b', 'Qwen3 VL')]):
    sys.exit(main(['--stdio'] + sys.argv[1:]))
'''


def test_stdio_rechaza_el_mensaje_sin_turno_y_la_sesion_sigue(tmp_path, foto):
    import tests.test_compactar_sesion as base
    original, base.SESION = base.SESION, VISION
    try:
        cliente = Cliente(tmp_path, 'Primero')
    finally:
        base.SESION = original
    try:
        assert cliente.tipo('turno_terminado')['estado'] == 'completed'
        antes = len(cliente.peticiones())
        cliente.enviar(tipo='mensaje', texto='¿Qué falla aquí?', imagenes=[str(foto)])
        error = cliente.tipo('error')
        assert error['motivo'] == 'sin_vision' and 'no ve imágenes' in error['texto']
        assert error['rechazado'] == {'texto': '¿Qué falla aquí?', 'imagenes': [str(foto.resolve())]}
        assert error['con_vision'] == [{'modelo': 'qwen3-vl:8b', 'nombre': 'Qwen3 VL'}]
        # No se llamó al modelo ni empezó un turno; el siguiente mensaje sí lo abre.
        cliente.enviar(tipo='mensaje', texto='Entonces sin imagen')
        assert cliente.tipo('turno_terminado')['estado'] == 'completed'
        assert len(cliente.peticiones()) == antes + 1
        assert not any(f['tipo'] == 'evento' and f['rol'] == 'user' and 'Qué falla' in f['texto']
                       for f in cliente.filas)
    finally:
        cliente.cerrar()


# -- dentro del turno -----------------------------------------------------------------


def _con_foto(m, foto, actual=True):
    rt = NativeRuntime(m, m.task)
    rt.user_message(imagenes.con_etiquetas('Mira', [str(foto)]))
    if not actual:
        rt.user_message('Otro mensaje, sin imágenes')
    return rt


def test_con_vision_delegada_las_describe_en_vez_de_fallar(m, foto, monkeypatch):
    monkeypatch.setattr(ajustes, 'vision_delegada', lambda: {'modelo': 'gpt-6-luna', 'nombre': 'Luna'})
    preguntas = []

    async def mirar(self, path, question):
        preguntas.append(question)
        return {'analysis': 'Un punto negro.'}

    monkeypatch.setattr(NativeRuntime, '_vision', mirar)
    rt = _con_foto(m, foto)
    enviado = asyncio.run(rt._con_imagenes(rt.state['messages'], {'capabilities': ['tools']}))[-1]
    assert 'images' not in enviado and 'imagenes' not in enviado
    assert 'Descripción de la imagen 1 hecha por Luna' in enviado['content'] and 'Un punto negro.' in enviado['content']
    assert preguntas == [NativeRuntime.PREGUNTA_SIN_VISION]


def test_las_del_historial_no_rompen_el_turno_de_un_modelo_sin_vision(m, foto):
    rt = _con_foto(m, foto, actual=False)
    vieja = asyncio.run(rt._con_imagenes(rt.state['messages'], {'capabilities': ['tools']}))[-2]
    assert 'images' not in vieja and 'no ve imágenes' in vieja['content']


def test_si_no_se_sabe_se_mandan_y_el_proveedor_dira(m, foto):
    rt = _con_foto(m, foto)
    enviado = asyncio.run(rt._con_imagenes(rt.state['messages'], {'capabilities': ['tools'],
                                                                   'vision_conocida': False}))[-1]
    assert enviado['images'] == [base64.b64encode(PNG).decode()]


def test_las_imagenes_de_una_herramienta_siguen_la_misma_regla(m, foto):
    rt = NativeRuntime(m, m.task)
    copia = imagenes.preparar(foto, rt.directory / 'imagenes')
    contexto = [{'role': 'tool', 'tool_name': 'captura', 'content': '{"ok": true}', 'imagenes': [str(copia)]}]
    con = asyncio.run(rt._con_imagenes(contexto, {'capabilities': ['tools', 'vision']}))[0]
    assert con['images'] == [base64.b64encode(PNG).decode()]
    sin = asyncio.run(rt._con_imagenes(contexto, {'capabilities': ['tools']}))[0]
    assert 'images' not in sin and 'no ve imágenes' in sin['content'] and sin['content'].startswith('{"ok": true}')


# -- las imágenes de las herramientas hacia cada API ---------------------------------------


def _con_herramienta():
    dato = base64.b64encode(PNG).decode()
    return {'model': 'm', 'messages': [
        {'role': 'user', 'content': 'Haz una captura'},
        {'role': 'assistant', 'content': '', 'tool_calls': [{'id': 'c1', 'function': {'name': 'captura',
                                                                                        'arguments': {}}}]},
        {'role': 'tool', 'tool_name': 'captura', 'content': 'hecha', 'images': [dato]},
        {'role': 'assistant', 'content': 'Ya está.'}]}, dato


def test_openai_lleva_la_imagen_de_la_herramienta_en_su_output():
    cuerpo = openai.traducir_peticion(_con_herramienta()[0])
    salida = next(i for i in cuerpo['input'] if i.get('type') == 'function_call_output')
    assert salida['call_id'] == 'c1'
    assert salida['output'][0] == {'type': 'input_text', 'text': 'hecha'}
    assert salida['output'][1]['type'] == 'input_image'
    assert salida['output'][1]['image_url'].startswith('data:image/png;base64,')


def test_chat_completions_la_manda_detras_en_un_mensaje_del_usuario():
    cuerpo = compatible.traducir_chat(_con_herramienta()[0])
    roles = [mensaje['role'] for mensaje in cuerpo['messages']]
    assert roles == ['user', 'assistant', 'tool', 'user', 'assistant']
    herramienta, imagen = cuerpo['messages'][2], cuerpo['messages'][3]
    assert herramienta == {'role': 'tool', 'tool_call_id': 'c1', 'content': 'hecha'}
    assert imagen['content'][0]['text'] == 'Imagen que devolvió la herramienta captura:'
    assert imagen['content'][1]['image_url']['url'].startswith('data:image/png;base64,')
