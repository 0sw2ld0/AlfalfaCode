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
from alfalfa.proveedores import anthropic, compatible, ollama, openai
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


def test_anthropic_lo_dice_por_su_catalogo_y_no_sabe_de_un_modelo_anadido_sin_ficha(monkeypatch):
    from alfalfa import catalogo
    from alfalfa.proveedores import configuracion
    externas = {'claude-con-ficha': {'id': 'claude-con-ficha', 'nombre': 'Con ficha', 'vision': False}}
    monkeypatch.setattr(configuracion, 'de', lambda ident: {'modelos': ['claude-inventado-9', 'claude-con-ficha']})
    monkeypatch.setattr(catalogo, 'ficha', lambda proveedor, modelo: externas.get(modelo))
    filas = {f['name']: f['vision'] for f in anthropic.Anthropic().modelos()}
    assert filas == {'anthropic/claude-haiku-5-5': True, 'anthropic/claude-sonnet-5-5': True,
                     'anthropic/claude-opus-5-5': True, 'anthropic/claude-inventado-9': None,
                     'anthropic/claude-con-ficha': False}
    # Sin ficha no se sabe: no se rechaza, se le mandan y Anthropic dirá.
    assert anthropic.mostrar('claude-inventado-9')['vision_conocida'] is False
    assert proveedores.vision_de(anthropic.mostrar('claude-inventado-9')) is None
    assert proveedores.vision_de(anthropic.mostrar('claude-con-ficha')) is False
    assert proveedores.vision_de(anthropic.mostrar('claude-haiku-5-5')) is True


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


def test_un_alfalfa_cloud_anterior_sin_capacidades_usa_la_tabla_de_respaldo(monkeypatch):
    # Alfalfa Cloud anterior a 0.8.2: /api/tags sin `capabilities`. Sin la tabla todas las filas
    # quedaban en null y «Ninguno de tus modelos las ve» aunque Luna y Haiku sí las ven.
    filas = [{'name': n} for n in ('gpt-6-luna', 'claude-haiku-5-5', 'glm-5.3-flash', 'deepseek-v4.1-flash',
                                   'glm-5.3', 'nuevo-9')]
    monkeypatch.setattr(proveedor_alfalfa.Alfalfa, '_pedir', lambda self, metodo, ruta: {'models': filas})
    vistas = {f['name']: f['vision'] for f in proveedor_alfalfa.Alfalfa(firmante=object()).modelos()}
    assert vistas == {'alfalfa/gpt-6-luna': True, 'alfalfa/claude-haiku-5-5': True, 'alfalfa/glm-5.3-flash': True,
                      'alfalfa/deepseek-v4.1-flash': True, 'alfalfa/glm-5.3': False, 'alfalfa/nuevo-9': None}
    monkeypatch.setattr(proveedores, 'listar_modelos', lambda: ([dict(f, proveedor='alfalfa') for f in
                                                                 proveedor_alfalfa.Alfalfa(firmante=object()).modelos()],
                                                                {}))
    assert [ident for ident, _ in proveedores.con_vision('glm-5.3:cloud')] == [
        'alfalfa/gpt-6-luna', 'alfalfa/claude-haiku-5-5', 'alfalfa/glm-5.3-flash']


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


def test_si_la_lista_tarda_el_rechazo_no_la_espera(monkeypatch):
    import threading
    import time
    soltar = threading.Event()

    def lenta():
        soltar.wait(10)
        return [{'name': 'qwen3-vl:8b', 'vision': True}], {}

    monkeypatch.setattr(proveedores, 'listar_modelos', lenta)
    inicio = time.monotonic()
    try:
        assert proveedores.con_vision('qwen3:8b', tope=0.2) is None
        texto = proveedores.texto_sin_vision('qwen3:8b', None)
    finally:
        soltar.set()
    assert time.monotonic() - inicio < 2
    # Sin la lista no se dice que no haya ninguno: no se sabe.
    assert 'Ninguno' not in texto and texto.endswith('Cambia a un modelo con visión o quita las imágenes.')


# -- antes del turno ----------------------------------------------------------------


def test_vision_del_modelo_pregunta_al_proveedor_de_verdad(monkeypatch):
    # Sin sustituir vision_del_modelo: Ollama por un transporte falso de httpx y Anthropic
    # con su propio /api/show (no sale a la red).
    vistos = []

    def manejar(request):
        nombre = json.loads(request.content)['model']
        vistos.append((request.url.path, nombre))
        if nombre == 'roto:1b':
            return httpx.Response(500, json={'error': 'roto'})
        return httpx.Response(200, json={'capabilities': ['completion', 'tools']
                                         + (['vision'] if 'vl' in nombre else [])})

    monkeypatch.setattr(ollama.Ollama, 'cliente', lambda self, timeout, tarea=None, al_cambiar_cuota=None:
                        httpx.AsyncClient(base_url='http://ollama.test', transport=httpx.MockTransport(manejar)))
    assert asyncio.run(proveedores.vision_del_modelo('qwen3-vl:8b')) is True
    assert asyncio.run(proveedores.vision_del_modelo('glm-5.3:cloud')) is False
    assert asyncio.run(proveedores.vision_del_modelo('roto:1b')) is None
    assert vistos == [('/api/show', 'qwen3-vl:8b'), ('/api/show', 'glm-5.3:cloud'), ('/api/show', 'roto:1b')]

    from alfalfa import catalogo
    monkeypatch.setattr(catalogo, 'ficha', lambda proveedor, modelo: None)
    monkeypatch.setattr(ajustes, 'anthropic_clave', lambda: '')
    assert asyncio.run(proveedores.vision_del_modelo('anthropic/claude-haiku-5-5')) is None  # sin clave
    monkeypatch.setattr(ajustes, 'anthropic_clave', lambda: 'sk-ant-prueba')
    assert asyncio.run(proveedores.vision_del_modelo('anthropic/claude-haiku-5-5')) is True
    assert asyncio.run(proveedores.vision_del_modelo('anthropic/claude-inventado-9')) is None


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
        # Sólo con imágenes: vuelve sin el relleno, como la app pintó su burbuja.
        cliente.enviar(tipo='mensaje', texto='', imagenes=[str(foto)])
        assert cliente.tipo('error')['rechazado'] == {'texto': '', 'imagenes': [str(foto.resolve())]}
        # No se llamó al modelo ni empezó un turno; el siguiente mensaje sí lo abre.
        cliente.enviar(tipo='mensaje', texto='Entonces sin imagen')
        assert cliente.tipo('turno_terminado')['estado'] == 'completed'
        assert len(cliente.peticiones()) == antes + 1
        assert not any(f['tipo'] == 'evento' and f['rol'] == 'user' and 'Qué falla' in f['texto']
                       for f in cliente.filas)
    finally:
        cliente.cerrar()


# -- dentro del turno -----------------------------------------------------------------


# Un turno lento con un modelo que no ve (glm) y otro que sí (qwen3-vl): el mensaje con
# imágenes llega con el turno en marcha.
DURANTE = '''
import asyncio, json, sys
from pathlib import Path
from unittest.mock import patch
from alfalfa import proveedores
from alfalfa.terminal import main
from alfalfa.proveedores.nativo.runtime import NativeRuntime
def ve(modelo):
    return 'vl' in modelo
async def inference(self, client, endpoint, payload, **kwargs):
    if endpoint == '/api/show':
        return {'capabilities': ['tools'] + (['vision'] if ve(payload['model']) else []),
                'model_info': {'x.context_length': 131072}}
    with open('requests.jsonl', 'a') as stream:
        stream.write(json.dumps({'model': payload['model'], 'messages': payload['messages']}) + '\\n')
    last = payload['messages'][-1]['content']
    if 'LENTO' in last and not Path('soltar').exists():
        await asyncio.sleep(60)
    return dict(message=dict(role='assistant', content='Respuesta a: ' + last[-30:]),
                done=True, done_reason='stop', prompt_eval_count=900, eval_count=10)
async def vision_del_modelo(modelo, timeout=15):
    return ve(modelo)
with patch.object(NativeRuntime, 'request', inference), \\
        patch.object(proveedores, 'vision_del_modelo', vision_del_modelo), \\
        patch.object(proveedores, 'con_vision', lambda modelo=None, limite=3: [('qwen3-vl:8b', 'Qwen3 VL')]):
    sys.exit(main(['--stdio'] + sys.argv[1:]))
'''


def _durante(tmp_path):
    import tests.test_compactar_sesion as base
    original, base.SESION = base.SESION, DURANTE
    try:
        return Cliente(tmp_path, '--modelo', 'glm-5.3:cloud', 'Tarea LENTO')
    finally:
        base.SESION = original


def _pensando(cliente, cuantas=1):
    import time
    fin = time.time() + 15
    while len(cliente.peticiones()) < cuantas:
        assert time.time() < fin, 'el modelo no recibió la petición'
        time.sleep(0.05)


def test_cambiar_de_modelo_y_enviar_con_el_turno_en_marcha_espera_al_turno_siguiente(tmp_path, foto):
    # «Cambiar a X y enviar» con el turno en marcha y «Redirigir»: el cambio de modelo vale
    # para el turno siguiente, así que el mensaje no entra en éste (que no lo vería y caería).
    cliente = _durante(tmp_path)
    try:
        _pensando(cliente)
        cliente.enviar(tipo='ajustes', modelo='qwen3-vl:8b')
        cliente.enviar(tipo='mensaje', texto='¿Qué falla aquí?', imagenes=[str(foto)])
        cliente.enviar(tipo='redirigir')
        aviso = cliente.hasta(lambda f: f['tipo'] == 'evento' and f['rol'] == 'system'
                              and 'espera a que acabe este turno' in f['texto'])
        assert 'glm-5.3:cloud no ve imágenes' in aviso['texto']
        (tmp_path / 'soltar').write_text('')
        primero = cliente.tipo('turno_terminado')
        assert primero['estado'] == 'completed'
        assert not any(f['tipo'] == 'error' for f in cliente.filas)
        # El mensaje abre el turno siguiente, con el modelo nuevo y sus imágenes.
        entregado = cliente.hasta(lambda f: f['tipo'] == 'evento' and f['rol'] == 'user')
        assert '¿Qué falla aquí?' in entregado['texto']
        assert cliente.tipo('turno_terminado')['estado'] == 'completed'
        peticiones = cliente.peticiones()
        assert all('images' not in m for p in peticiones if p['model'] == 'glm-5.3:cloud' for m in p['messages'])
        ultima = peticiones[-1]
        assert ultima['model'] == 'qwen3-vl:8b'
        assert ultima['messages'][-1]['images'] == [base64.b64encode(PNG).decode()]
    finally:
        cliente.cerrar()


def test_sin_cambiar_de_modelo_el_mensaje_con_imagenes_se_rechaza_al_acabar_el_turno(tmp_path, foto):
    cliente = _durante(tmp_path)
    try:
        _pensando(cliente)
        cliente.enviar(tipo='mensaje', texto='¿Qué falla aquí?', imagenes=[str(foto)])
        cliente.enviar(tipo='redirigir')
        cliente.hasta(lambda f: f['tipo'] == 'evento' and f['rol'] == 'system'
                      and 'espera a que acabe este turno' in f['texto'])
        (tmp_path / 'soltar').write_text('')
        assert cliente.tipo('turno_terminado')['estado'] == 'completed'
        error = cliente.tipo('error')
        assert error['motivo'] == 'sin_vision'
        assert error['rechazado'] == {'texto': '¿Qué falla aquí?', 'imagenes': [str(foto.resolve())]}
        # Nunca entró en la conversación.
        assert not any(f['tipo'] == 'evento' and f['rol'] == 'user' for f in cliente.filas)
        assert not any('Qué falla' in json.dumps(p) for p in cliente.peticiones())
    finally:
        cliente.cerrar()




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


def test_la_vision_delegada_no_repite_en_cada_paso_ni_lo_hecho_ni_lo_fallido(m, foto, monkeypatch):
    monkeypatch.setattr(ajustes, 'vision_delegada', lambda: {'modelo': 'gpt-6-luna', 'nombre': 'Luna'})
    llamadas = []

    async def falla(self, path, question):
        llamadas.append(path)
        raise ValueError('Luna no completó el análisis.')

    monkeypatch.setattr(NativeRuntime, '_vision', falla)
    rt = _con_foto(m, foto, actual=False)
    for _ in range(3):  # tres pasos del mismo turno
        vieja = asyncio.run(rt._con_imagenes(rt.state['messages'], {'capabilities': ['tools']}))[-2]
        assert '(Imagen 1 sin describir: Luna no completó el análisis.)' in vieja['content']
    assert len(llamadas) == 1
    # Un turno nuevo (otro runtime) lo vuelve a intentar.
    otro = NativeRuntime(m, m.task)
    asyncio.run(otro._con_imagenes(rt.state['messages'], {'capabilities': ['tools']}))
    assert len(llamadas) == 2


def test_con_la_descripcion_en_cache_no_se_anuncia_analizando(m, foto, monkeypatch):
    monkeypatch.setattr(ajustes, 'vision_delegada', lambda: {'modelo': 'gpt-6-luna', 'nombre': 'Luna'})
    rt = _con_foto(m, foto)
    ruta = rt.state['messages'][-1]['imagenes'][0]
    from pathlib import Path
    cache = rt._vision_cache(Path(ruta), NativeRuntime.PREGUNTA_SIN_VISION)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({'analysis': 'Ya descrita.'}))
    guardados = []
    monkeypatch.setattr(m, 'save', lambda: guardados.append(dict(m.task)))
    m.task.pop('activity', None)
    enviado = asyncio.run(rt._con_imagenes(rt.state['messages'], {'capabilities': ['tools']}))[-1]
    assert 'Ya descrita.' in enviado['content']
    assert 'activity' not in m.task and guardados == []


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
# Hoy ninguna herramienta propia deja imágenes en un mensaje `tool` (las capturas del
# navegador van en un mensaje `user` aparte, con `captura_navegador`): estas pruebas cubren
# sólo la traducción de cada API, por si un mensaje así llega.


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
