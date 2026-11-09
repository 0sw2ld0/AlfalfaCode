"""Con la clave propia, OpenAI y Anthropic ofrecen los modelos de chat de la cuenta (`GET /v1/models`).

Nunca se sale a la red: un transporte falso hace de API y un catálogo de models.dev falso
describe algunos modelos.
"""
import asyncio
import json
import time

import httpx
import pytest

from alfalfa import catalogo, proveedores
from alfalfa.proveedores import anthropic, lista_api, llavero, openai


def _modelo_dev(nombre, fecha, herramientas=True, razona=True, esfuerzos=None, vision=True, estado=None,
                ventana=400000, precio=(1, 5)):
    crudo = {'name': nombre, 'tool_call': herramientas, 'reasoning': razona, 'release_date': fecha,
             'attachment': vision, 'modalities': {'input': ['text', 'image'] if vision else ['text']},
             'limit': {'context': ventana, 'output': 128000},
             'cost': {'input': precio[0], 'output': precio[1]}}
    if esfuerzos:
        crudo['reasoning_options'] = [{'type': 'effort', 'values': esfuerzos}]
    if estado:
        crudo['status'] = estado
    return crudo


@pytest.fixture
def models_dev():
    """Un catálogo de models.dev pequeño y conocido, ya cargado (sin red)."""
    crudo = {
        'openai': {'name': 'OpenAI', 'npm': '@ai-sdk/openai', 'api': 'https://api.openai.com/v1', 'models': {
            'gpt-6-luna': _modelo_dev('GPT-6 Luna', '2026-10-01', esfuerzos=['none', 'low', 'medium', 'high',
                                                                              'xhigh', 'max'], precio=(0.1, 0.5)),
            'gpt-6-sol': _modelo_dev('GPT-6 Sol', '2026-10-01', esfuerzos=['none', 'low', 'medium', 'high',
                                                                            'xhigh', 'max']),
            'gpt-5': _modelo_dev('GPT-5', '2025-08-07', esfuerzos=['minimal', 'low', 'medium', 'high']),
            'gpt-5-pro': _modelo_dev('GPT-5 Pro', '2025-10-06', esfuerzos=['high']),
            'gpt-4o': _modelo_dev('GPT-4o', '2024-05-13', razona=False, ventana=128000),
            'gpt-4.1-nano': _modelo_dev('GPT-4.1 nano', '2025-04-14', razona=False, estado='deprecated'),
            'gpt-3.5-turbo': _modelo_dev('GPT-3.5 Turbo', '2023-03-01', herramientas=False, razona=False),
            'text-embedding-3-large': _modelo_dev('Embedding', '2024-01-25', herramientas=False, razona=False),
        }},
        'anthropic': {'name': 'Anthropic', 'npm': '@ai-sdk/anthropic', 'api': 'https://api.anthropic.com/v1',
                      'models': {
            'claude-haiku-5-5': _modelo_dev('Claude Haiku 5.5', '2026-09-21', ventana=1000000,
                                            esfuerzos=['low', 'medium', 'high', 'xhigh', 'max'], precio=(0.1, 0.5)),
            'claude-opus-4-6': _modelo_dev('Claude Opus 4.6', '2026-02-05', ventana=1000000,
                                           esfuerzos=['low', 'medium', 'high', 'max'], precio=(5, 25)),
            'claude-opus-4-5': _modelo_dev('Claude Opus 4.5', '2025-11-24', ventana=200000,
                                           esfuerzos=['low', 'medium', 'high'], precio=(5, 25)),
        }},
    }
    catalogo.olvidar()
    catalogo._memoria.update(fecha=time.time(), proveedores=catalogo.validar(crudo), origen='red')
    yield
    catalogo.olvidar()


class API:
    """Hace de `GET /v1/models` y cuenta las peticiones."""

    def __init__(self, paginas):
        self.paginas = paginas
        self.peticiones = []

    def __call__(self, peticion):
        self.peticiones.append(peticion)
        indice = 0
        if peticion.url.params.get('after_id'):
            indice = next(i for i, p in enumerate(self.paginas) if p.get('last_id') == peticion.url.params['after_id']
                          ) + 1
        return httpx.Response(200, json=self.paginas[indice])


def _usar(monkeypatch, api):
    monkeypatch.setattr(lista_api, '_transporte', httpx.MockTransport(api))
    lista_api.olvidar()


OPENAI = {'object': 'list', 'data': [
    {'id': i, 'object': 'model', 'created': c, 'owned_by': 'openai'} for i, c in (
        ('gpt-6-luna', 1790000000), ('gpt-6-sol', 1790000000), ('gpt-5', 1754000000), ('gpt-5-pro', 1759000000),
        ('gpt-4o', 1715000000), ('gpt-4o-2024-08-06', 1722000000), ('gpt-4.1-nano', 1744000000),
        ('gpt-3.5-turbo', 1677000000), ('text-embedding-3-large', 1705000000), ('tts-1', 1681000000),
        ('whisper-1', 1677000000), ('gpt-transcribe', 1790000000), ('gpt-realtime-2.1', 1790000000),
        ('gpt-audio-1.5', 1790000000), ('gpt-image-2', 1790000000), ('dall-e-3', 1698000000),
        ('omni-moderation-latest', 1731000000), ('gpt-7-nuevo', 1800000000))] + [
    {'id': 'gpt-5.1', 'object': 'model', 'created': 1762000000, 'owned_by': 'system',
     'shutdown_date': '2026-01-01'}]}


def test_con_clave_openai_ofrece_los_modelos_de_chat_de_la_cuenta(monkeypatch, models_dev):
    api = API([OPENAI])
    _usar(monkeypatch, api)
    llavero.guardar('openai', 'sk-prueba-123')
    filas = proveedores.obtener('openai').modelos()
    nombres = [f['name'].split('/', 1)[1] for f in filas]
    # Luna, recomendado, arriba; debajo los de chat de la cuenta, los más nuevos primero.
    assert nombres == ['gpt-6-luna', 'gpt-7-nuevo', 'gpt-6-sol', 'gpt-5-pro', 'gpt-5', 'gpt-4o']
    assert [f['recomendado'] for f in filas] == [True] + [False] * 5
    # Fuera: embeddings, voz, realtime, imagen, moderación, sin herramientas, retirados, apagados
    # y la instantánea con fecha de un modelo que también está.
    for fuera in ('text-embedding-3-large', 'tts-1', 'whisper-1', 'gpt-transcribe', 'gpt-realtime-2.1',
                  'gpt-audio-1.5', 'gpt-image-2', 'dall-e-3', 'omni-moderation-latest', 'gpt-3.5-turbo',
                  'gpt-4.1-nano', 'gpt-4o-2024-08-06', 'gpt-5.1'):
        assert fuera not in nombres
    luna, nuevo = filas[0], filas[1]
    # Luna conserva lo propio (ventana, tramo largo); el precio es del catálogo.
    assert luna['nombre'] == 'GPT-6 Luna' and luna['ventana'] == 1_050_000 and luna['barato_hasta'] == 272_000
    assert luna['precio'] == {'entrada': 0.1, 'salida': 0.5}
    # Un modelo que models.dev no describe sale igual, con «no se sabe».
    assert nuevo['nombre'] == 'gpt-7-nuevo' and nuevo['vision'] is None and 'precio' not in nuevo
    assert next(f for f in filas if f['name'] == 'openai/gpt-5')['esfuerzos'] == ['minimal', 'low', 'medium', 'high']
    # Se pidió una vez, con la clave en la cabecera y sin imprimirla en ningún sitio guardado.
    assert len(api.peticiones) == 1
    assert api.peticiones[0].url.path == '/v1/models'
    assert api.peticiones[0].headers['authorization'] == 'Bearer sk-prueba-123'
    assert 'sk-prueba-123' not in lista_api.ruta('openai').read_text()
    # La segunda vez sale de la caché, sin red; con otra clave se vuelve a pedir.
    proveedores.obtener('openai').modelos()
    lista_api.olvidar()
    proveedores.obtener('openai').modelos()
    assert len(api.peticiones) == 1
    llavero.guardar('openai', 'sk-otra-456')
    lista_api.ruta('openai').with_suffix('.intento').unlink()
    proveedores.obtener('openai').modelos()
    assert len(api.peticiones) == 2


def test_sin_clave_o_sin_red_openai_usa_models_dev(monkeypatch, models_dev):
    api = API([OPENAI])
    _usar(monkeypatch, api)
    filas = proveedores.obtener('openai').modelos()
    assert [f['name'].split('/', 1)[1] for f in filas] == ['gpt-6-luna', 'gpt-6-sol', 'gpt-5-pro', 'gpt-5', 'gpt-4o']
    assert api.peticiones == []
    # Con clave pero sin red: lo mismo, y no se reintenta en cada consulta.
    llavero.guardar('openai', 'sk-prueba-123')
    monkeypatch.setattr(lista_api, '_transporte', httpx.MockTransport(
        lambda p: (_ for _ in ()).throw(httpx.ConnectError('sin red', request=p))))
    assert [f['name'] for f in proveedores.obtener('openai').modelos()] == [f['name'] for f in filas]
    assert not lista_api.ruta('openai').exists()
    assert lista_api.ruta('openai').with_suffix('.intento').exists()


def test_el_esfuerzo_de_openai_es_el_que_admite_cada_modelo(models_dev):
    def cuerpo(modelo, esfuerzo, think=True):
        razona, admitidos = openai.razonamiento(modelo)
        return openai.traducir_peticion({'model': modelo, 'think': think, 'messages': []}, esfuerzo,
                                        razona=razona, admitidos=admitidos)
    assert cuerpo('gpt-6-luna', 'max')['reasoning'] == {'effort': 'max', 'summary': 'auto'}
    assert cuerpo('gpt-6-luna', 'high', think=False)['reasoning'] == {'effort': 'none'}
    # GPT-5 no tiene `xhigh` ni `none`: el más cercano por debajo, y `minimal` para apagarlo.
    assert cuerpo('gpt-5', 'xhigh')['reasoning']['effort'] == 'high'
    assert cuerpo('gpt-5', 'high', think=False)['reasoning']['effort'] == 'minimal'
    # GPT-5 Pro sólo admite `high`.
    assert cuerpo('gpt-5-pro', 'low')['reasoning']['effort'] == 'high'
    # GPT-4o no razona: no se manda `reasoning`, que la API rechazaría.
    assert 'reasoning' not in cuerpo('gpt-4o', 'high')
    assert 'thinking' not in openai.mostrar('gpt-4o')['capabilities']
    # Uno que nadie describe: el esfuerzo de la sesión tal cual.
    assert cuerpo('gpt-7-nuevo', 'xhigh')['reasoning']['effort'] == 'xhigh'


def test_el_transporte_de_openai_pide_el_esfuerzo_del_modelo(models_dev):
    vistos = []

    def responses(peticion):
        vistos.append(json.loads(peticion.content))
        return httpx.Response(200, json={'status': 'completed', 'output': [
            {'type': 'message', 'content': [{'type': 'output_text', 'text': 'hola'}]}]})

    async def turno(modelo):
        transporte = openai.Transporte('https://api.openai.com/v1', 'sk-x', {'effort': 'xhigh'},
                                       transporte=httpx.MockTransport(responses))
        async with httpx.AsyncClient(base_url='https://openai.alfalfa.invalid', transport=transporte) as cliente:
            await cliente.post('/api/chat', json={'model': modelo, 'messages': [{'role': 'user', 'content': 'hola'}]})

    asyncio.run(turno('gpt-5'))
    asyncio.run(turno('gpt-4o'))
    assert vistos[0]['reasoning']['effort'] == 'high' and 'reasoning' not in vistos[1]


def _claude(ident, nombre, esfuerzos=None, imagen=True, ventana=1000000, salida=128000, ciclo='active',
            creado='2026-01-01T00:00:00Z', adaptativo=True, capacidades=True):
    dato = {'type': 'model', 'id': ident, 'display_name': nombre, 'created_at': creado, 'lifecycle': ciclo,
            'max_input_tokens': ventana, 'max_tokens': salida}
    if capacidades:
        niveles = ('low', 'medium', 'high', 'xhigh', 'max')
        dato['capabilities'] = {
            'image_input': {'supported': imagen},
            'effort': {'supported': esfuerzos is not None,
                       **{n: {'supported': n in (esfuerzos or [])} for n in niveles}},
            'thinking': {'supported': True, 'types': {'adaptive': {'supported': adaptativo},
                                                      'enabled': {'supported': True}}}}
    else:
        dato['capabilities'] = None
    return dato


ANTHROPIC = [
    {'data': [_claude('claude-nuevo-6', 'Claude Nuevo 6', ['low', 'high'], ventana=500000, salida=64000,
                      creado='2026-11-01T00:00:00Z'),
              _claude('claude-haiku-5-5', 'Claude Haiku 5.5', ['low', 'medium', 'high', 'xhigh', 'max'],
                      ventana=2000000, creado='2026-09-21T00:00:00Z'),
              _claude('claude-opus-4-6', 'Claude Opus 4.6', ['low', 'medium', 'high', 'max'],
                      creado='2026-02-05T00:00:00Z')],
     'has_more': True, 'first_id': 'claude-nuevo-6', 'last_id': 'claude-opus-4-6'},
    {'data': [_claude('claude-opus-4-5-20251101', 'Claude Opus 4.5', ['low', 'medium', 'high'], ventana=200000,
                      creado='2025-11-24T00:00:00Z', adaptativo=False),
              _claude('claude-sonnet-3-7', 'Claude Sonnet 3.7', None, ciclo='deprecated',
                      creado='2025-02-24T00:00:00Z'),
              _claude('claude-misterio', 'Claude Misterio', creado='2025-01-01T00:00:00Z', capacidades=False)],
     'has_more': False, 'first_id': 'claude-opus-4-5-20251101', 'last_id': 'claude-misterio'},
]


def test_con_clave_anthropic_ofrece_los_modelos_de_la_cuenta_con_sus_capacidades(monkeypatch, models_dev):
    api = API(ANTHROPIC)
    _usar(monkeypatch, api)
    llavero.guardar('anthropic', 'sk-ant-prueba-123456')
    filas = proveedores.obtener('anthropic').modelos()
    nombres = [f['name'].split('/', 1)[1] for f in filas]
    assert nombres == ['claude-haiku-5-5', 'claude-sonnet-5-5', 'claude-opus-5-5',
                       'claude-nuevo-6', 'claude-opus-4-6', 'claude-opus-4-5-20251101', 'claude-misterio']
    assert [f['recomendado'] for f in filas] == [True] * 3 + [False] * 4
    # Dos páginas, con la clave y la versión de la API en las cabeceras.
    assert len(api.peticiones) == 2
    assert api.peticiones[0].headers['x-api-key'] == 'sk-ant-prueba-123456'
    assert api.peticiones[0].headers['anthropic-version'] == anthropic.VERSION_API
    assert api.peticiones[0].url.params['limit'] == '1000'
    assert api.peticiones[1].url.params['after_id'] == 'claude-opus-4-6'
    porid = {f['name'].split('/', 1)[1]: f for f in filas}
    # Haiku conserva lo propio: su ventana y su tramo largo, aunque la API diga otra cosa.
    assert porid['claude-haiku-5-5']['ventana'] == 1000000 and porid['claude-haiku-5-5']['barato_hasta'] == 100000
    # Uno que models.dev no describe: lo que dice la API, sin precio.
    nuevo = porid['claude-nuevo-6']
    assert nuevo['nombre'] == 'Claude Nuevo 6' and nuevo['ventana'] == 500000 and nuevo['vision'] is True
    assert nuevo['esfuerzos'] == ['low', 'high'] and 'precio' not in nuevo and nuevo['salida_max'] == 64000
    # Sin capacidades en la API ni ficha en models.dev: «no se sabe».
    assert porid['claude-misterio']['vision'] is None and 'precio' not in porid['claude-misterio']
    assert porid['claude-opus-4-6']['esfuerzos'] == ['low', 'medium', 'high', 'max']
    # Lo que dice la API también vale al pedir: esfuerzos, razonamiento adaptativo, ventana y visión.
    assert anthropic.esfuerzo_para('claude-nuevo-6', 'xhigh', True) == 'high'
    assert anthropic.esfuerzo_para('claude-nuevo-6', 'medium', True) == 'low'
    assert anthropic.esfuerzo_para('claude-opus-4-6', 'xhigh', True) == 'high'
    assert anthropic.esfuerzo_para('claude-opus-4-5-20251101', 'high', True) is None
    info = anthropic.mostrar('claude-nuevo-6')
    assert info['model_info']['anthropic.context_length'] == 500000 and 'vision' in info['capabilities']
    assert 'vision_conocida' not in info and info['catalogo']['salida'] == 64000
    assert anthropic.mostrar('claude-misterio')['vision_conocida'] is False


def test_sin_clave_anthropic_usa_models_dev_y_alfalfa_cloud_no_cambia(monkeypatch, models_dev):
    api = API(ANTHROPIC)
    _usar(monkeypatch, api)
    filas = proveedores.obtener('anthropic').modelos()
    assert [f['name'].split('/', 1)[1] for f in filas] == ['claude-haiku-5-5', 'claude-sonnet-5-5', 'claude-opus-5-5',
                                                           'claude-opus-4-6', 'claude-opus-4-5']
    assert api.peticiones == []
    # Alfalfa Cloud sigue con lo que el servicio da a cada código: no se mira la lista de las API.
    from alfalfa.proveedores import alfalfa
    assert 'lista_api' not in vars(alfalfa)


def test_listar_modelos_trae_los_de_la_cuenta_marcados(monkeypatch, models_dev):
    _usar(monkeypatch, API([OPENAI]))
    llavero.guardar('openai', 'sk-prueba-123')
    modelos, fallos = proveedores.listar_modelos()
    de_openai = [m for m in modelos if m['proveedor'] == 'openai']
    assert de_openai[0]['name'] == 'openai/gpt-6-luna' and de_openai[0]['recomendado'] is True
    assert 'openai/gpt-7-nuevo' in [m['name'] for m in de_openai] and 'openai' not in fallos
    assert next(m for m in de_openai if m['name'] == 'openai/gpt-7-nuevo')['vision'] is None


def test_lo_que_llega_de_la_api_se_valida():
    assert lista_api.de_openai({'id': '../../etc'}) is None
    assert lista_api.de_openai({'id': 'gpt-x', 'created': 'mañana', 'shutdown_date': 7}) == {'id': 'gpt-x'}
    fila = lista_api.de_anthropic({'type': 'model', 'id': 'claude-x', 'max_input_tokens': 0, 'lifecycle': 'raro',
                                   'display_name': 'C\x00X', 'capabilities': {'effort': {'supported': False}}})
    assert fila == {'id': 'claude-x', 'nombre': 'CX', 'esfuerzos': []}
    assert lista_api.de_anthropic({'type': 'otra', 'id': 'claude-x'}) is None


def test_elegir_esfuerzo_y_quitar_instantaneas():
    assert proveedores.elegir_esfuerzo(['low', 'high'], 'medium') == 'low'
    assert proveedores.elegir_esfuerzo(['high'], 'low') == 'high'
    assert proveedores.elegir_esfuerzo(['none'], 'high') is None
    assert proveedores.elegir_esfuerzo([], 'high') is None
    assert proveedores.sin_instantaneas(['gpt-4o', 'gpt-4o-2024-08-06', 'gpt-5-2025-08-07',
                                         'claude-haiku-4-5', 'claude-haiku-4-5-20251001']) == [
        'gpt-4o', 'gpt-5-2025-08-07', 'claude-haiku-4-5']
