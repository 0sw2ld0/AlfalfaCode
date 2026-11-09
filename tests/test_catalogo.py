"""El catálogo de models.dev: validación, caché con fecha, copia del paquete y «Gratis ahora».

Nunca sale a internet: `_descargar` devuelve un catálogo falso.
"""
import json
import os
import time

import pytest

from alfalfa import catalogo


def _modelo(ident, entrada=1.0, salida=2.0, **extra):
    fila = {'id': ident, 'name': ident.upper(), 'attachment': False, 'reasoning': True, 'tool_call': True,
            'modalities': {'input': ['text'], 'output': ['text']}, 'open_weights': True,
            'limit': {'context': 200000, 'output': 32000},
            'reasoning_options': [{'type': 'effort', 'values': ['low', 'medium', 'high']}],
            'cost': {'input': entrada, 'output': salida, 'cache_read': 0.1}}
    fila.update(extra)
    return fila


def falso():
    """Un api.json con la forma de models.dev y un caso de cada filtro."""
    return {
        'openrouter': {'id': 'openrouter', 'name': 'OpenRouter', 'env': ['OPENROUTER_API_KEY'],
                       'npm': '@openrouter/ai-sdk-provider', 'api': 'https://openrouter.ai/api/v1',
                       'doc': 'https://openrouter.ai/models', 'models': {
                           'google/gemma:free': _modelo('google/gemma:free', 0, 0,
                                                        attachment=True,
                                                        modalities={'input': ['text', 'image']}),
                           'openrouter/auto': _modelo('openrouter/auto', 0, 0),
                           'vieja:free': _modelo('vieja:free', 0, 0, status='deprecated'),
                           'sin-herramientas:free': _modelo('sin-herramientas:free', 0, 0, tool_call=False),
                           'de-pago': _modelo('de-pago', 3, 15)}},
        'opencode': {'id': 'opencode', 'name': 'OpenCode', 'env': ['OPENCODE_API_KEY'],
                     'npm': '@ai-sdk/openai-compatible', 'api': 'https://opencode.ai/inference/openai/v1',
                     'doc': 'https://opencode.ai/docs', 'models': {
                         'big-pickle': _modelo('big-pickle', 0, 0),
                         'gpt-6-x': _modelo('gpt-6-x', 5, 30, provider={'npm': '@ai-sdk/openai'}),
                         'claude-x': _modelo('claude-x', 3, 15, provider={'npm': '@ai-sdk/anthropic'})}},
        'lmstudio': {'id': 'lmstudio', 'name': 'LMStudio', 'env': ['LMSTUDIO_API_KEY'],
                     'npm': '@ai-sdk/openai-compatible', 'api': 'http://127.0.0.1:1234/v1', 'doc': 'https://lmstudio.ai',
                     'models': {'local': _modelo('local', 0, 0)}},
        'zai-coding-plan': {'id': 'zai-coding-plan', 'name': 'Z.AI Coding Plan', 'env': ['ZHIPU_API_KEY'],
                            'npm': '@ai-sdk/openai-compatible', 'api': 'https://api.z.ai/api/coding/paas/v4',
                            'doc': 'https://z.ai', 'models': {'glm': _modelo('glm', 0, 0)}},
        'nube': {'id': 'nube', 'name': 'Nube Gratis', 'env': ['NUBE_API_KEY'], 'npm': '@ai-sdk/openai-compatible',
                 'api': 'https://api.nube.example/v1', 'doc': 'https://nube.example/claves',
                 'models': {'libre': _modelo('libre', 0, 0)}},
        'anthropic': {'id': 'anthropic', 'name': 'Anthropic', 'env': ['ANTHROPIC_API_KEY'],
                      'npm': '@ai-sdk/anthropic', 'doc': 'https://docs.anthropic.com',
                      'models': {'claude': _modelo('claude', 0, 0)}},
        'openai': {'id': 'openai', 'name': 'OpenAI', 'env': ['OPENAI_API_KEY'], 'npm': '@ai-sdk/openai',
                   'doc': 'https://platform.openai.com/docs/models',
                   'models': {'gpt-6-luna': _modelo('gpt-6-luna', 0.25, 2.0,
                                                    limit={'context': 1050000, 'output': 128000})}},
        'ollama-cloud': {'id': 'ollama-cloud', 'name': 'Ollama Cloud', 'env': ['OLLAMA_API_KEY'],
                         'npm': '@ai-sdk/openai-compatible', 'api': 'https://ollama.com/v1',
                         'doc': 'https://docs.ollama.com/cloud',
                         'models': {'glm-5.3-flash': _modelo('glm-5.3-flash', 0.15, 0.5,
                                                             limit={'context': 1000000, 'output': 131072})}},
    }


@pytest.fixture
def con_catalogo(monkeypatch):
    """Usa el catálogo falso como si llegara de models.dev; cuenta las descargas."""
    bajadas = []

    def bajar():
        bajadas.append(time.time())
        return json.dumps(falso()).encode()

    monkeypatch.setattr(catalogo, '_descargar', bajar)
    catalogo.olvidar()
    catalogo.cargar(red=True)
    return bajadas


def test_sin_red_ni_copia_vale_la_del_paquete():
    datos = catalogo.cargar(red=True)
    assert datos['origen'] == 'paquete'
    # La copia mínima trae lo necesario para el primer arranque.
    assert {'openai', 'openrouter', 'opencode', 'ollama-cloud'} <= set(datos['proveedores'])
    assert catalogo.gratis_ahora(), 'la copia del paquete trae modelos gratis de nube'


def test_baja_valida_y_guarda_con_fecha(con_catalogo):
    assert len(con_catalogo) == 1
    guardado = json.loads(catalogo.ruta().read_text())
    assert guardado['fuente'] == catalogo.URL and guardado['fecha'] > 0
    assert 'openrouter' in guardado['proveedores']
    assert oct(catalogo.ruta().stat().st_mode & 0o777) == '0o600'
    # En otro proceso (memoria vacía) se lee la copia guardada sin bajarla otra vez.
    catalogo.olvidar()
    assert catalogo.cargar(red=True)['origen'] == 'cache'
    assert len(con_catalogo) == 1


def test_se_refresca_cada_24_horas(con_catalogo):
    viejo = time.time() - catalogo.CADA - 10
    datos = json.loads(catalogo.ruta().read_text())
    datos['fecha'] = viejo
    catalogo.ruta().write_text(json.dumps(datos))
    os.utime(catalogo._intento(), (viejo, viejo))
    catalogo.olvidar()
    assert catalogo.cargar(red=True)['origen'] == 'red'
    assert len(con_catalogo) == 2
    # Sin `red` (el motor en cada turno) nunca se baja.
    catalogo.olvidar()
    catalogo.cargar(red=False)
    assert len(con_catalogo) == 2


def test_sin_red_vale_la_ultima_copia_y_no_reintenta_cada_vez(con_catalogo, monkeypatch):
    viejo = time.time() - catalogo.CADA - 10
    datos = json.loads(catalogo.ruta().read_text())
    datos['fecha'] = viejo
    catalogo.ruta().write_text(json.dumps(datos))
    os.utime(catalogo._intento(), (viejo, viejo))
    intentos = []

    def sin_red():
        intentos.append(1)
        raise OSError('sin red')

    monkeypatch.setattr(catalogo, '_descargar', sin_red)
    catalogo.olvidar()
    assert catalogo.cargar(red=True)['origen'] == 'cache'
    assert catalogo.proveedor('nube')
    catalogo.olvidar()
    catalogo.cargar(red=True)
    assert len(intentos) == 1, 'tras un fallo se espera una hora antes de reintentar'


def test_una_copia_guardada_rota_no_rompe_nada(con_catalogo):
    catalogo.ruta().write_text('{"proveedores": "no"}')
    catalogo.olvidar()
    assert catalogo.cargar()['origen'] == 'paquete'


def test_lo_que_no_es_catalogo_se_rechaza():
    for malo in ([], {}, 'x', {'p': 'no'}, {'p': {'models': {}}}):
        with pytest.raises(catalogo.CatalogoInvalido):
            catalogo.validar(malo)


def test_valida_campos_y_descarta_lo_raro():
    crudo = falso()
    crudo['Mal Id'] = crudo['nube']
    crudo['espia'] = dict(crudo['nube'], api='https://usuario:clave@evil.example/v1')
    crudo['plano'] = dict(crudo['nube'], api='http://evil.example/v1')
    crudo['nube']['models']['negativo'] = _modelo('negativo', -1, 0)
    crudo['nube']['models']['infinito'] = _modelo('infinito', float('inf'), 0)
    crudo['nube']['models']['../raro'] = _modelo('../raro')
    crudo['nube']['env'] = ['NUBE_API_KEY', 'no válida', 'OTRA;rm']
    datos = catalogo.validar(crudo)
    assert 'Mal Id' not in datos
    assert 'api' not in datos['espia'] and 'api' not in datos['plano']
    nube = datos['nube']
    assert nube['env'] == ['NUBE_API_KEY']
    assert 'precio' not in nube['modelos']['negativo'] and 'precio' not in nube['modelos']['infinito']
    assert '../raro' not in nube['modelos']
    ficha = nube['modelos']['libre']
    assert ficha['ventana'] == 200000 and ficha['salida'] == 32000
    assert ficha['esfuerzos'] == ['low', 'medium', 'high'] and ficha['herramientas'] and ficha['razona']
    assert ficha['precio'] == {'entrada': 0, 'salida': 0, 'cache_lectura': 0.1}


def test_un_catalogo_demasiado_grande_no_se_acepta(monkeypatch):
    monkeypatch.setattr(catalogo, 'MAX_BYTES', 100)
    monkeypatch.setattr(catalogo, '_descargar', None)

    class Respuesta:
        headers = {'content-length': '1000'}

        def raise_for_status(self):
            pass

        def iter_bytes(self):
            yield b'x' * 1000

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class Cliente:
        def __init__(self, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def stream(self, metodo, url, headers=None):
            assert url == catalogo.URL
            return Respuesta()

    import httpx
    monkeypatch.setattr(httpx, 'Client', Cliente)
    with pytest.raises(catalogo.CatalogoInvalido, match='pesa'):
        catalogo.refrescar()


def test_gratis_ahora_solo_de_nube_con_herramientas_y_sin_planes(con_catalogo):
    gratis = {(p['id'], f['id']) for p, f in catalogo.gratis_ahora()}
    assert gratis == {('openrouter', 'google/gemma:free'), ('nube', 'libre')}
    # Fuera: lmstudio (local), el plan de Z.AI, los gratis de Zen (sólo desde OpenCode),
    # «auto», los retirados, los que no llaman herramientas y los que no hablan el formato de OpenAI.


def test_la_ficha_tolera_los_nombres_de_ollama(con_catalogo):
    assert catalogo.ficha('ollama-cloud', 'glm-5.3-flash:cloud')['ventana'] == 1000000
    assert catalogo.ficha('ollama-cloud', 'glm-5.3-flash')['salida'] == 131072
    assert catalogo.ficha('ollama-cloud', 'no-existe') is None
    assert catalogo.ficha('no-existe', 'x') is None


def test_coste_con_los_precios_por_millon():
    precio = {'entrada': 2.0, 'salida': 10.0, 'cache_lectura': 0.5}
    assert catalogo.coste(precio, 1_000_000, 100_000) == pytest.approx(3.0)
    assert catalogo.coste(precio, 1_000_000, 0, cache=500_000) == pytest.approx(1.25)
    assert catalogo.coste(None, 10, 10) is None
    assert catalogo.coste({'entrada': 0, 'salida': 0}, 10, 10) == 0


def test_la_copia_del_paquete_esta_validada():
    datos = json.loads(catalogo.MINIMO.read_text())
    assert datos['fuente'] == catalogo.URL
    assert catalogo._revalidar(datos['proveedores']).keys() == datos['proveedores'].keys()
    assert (catalogo.MINIMO.parent / 'PROCEDENCIA.md').read_text().count('models.dev') >= 3
