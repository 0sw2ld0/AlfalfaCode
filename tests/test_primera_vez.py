"""La primera vez sin proveedor: los gratis ahora y OpenRouter conectado en el navegador (OAuth PKCE)."""
import base64
import hashlib
import io
import threading
import urllib.request
from urllib.parse import parse_qs, urlsplit

import pytest

from alfalfa import primera_vez, terminal
from alfalfa.proveedores import conectar, configuracion, llavero


class _Respuesta:
    def __init__(self, codigo=200, datos=None):
        self.status_code = codigo
        self._datos = datos or {}

    def json(self):
        return self._datos


def _ficha(ident, nombre=None, ventana=100_000, herramientas=True, fecha='2026-01-01'):
    return {'id': ident, 'nombre': nombre or ident, 'ventana': ventana, 'herramientas': herramientas,
            'fecha': fecha}


GRATIS = [
    ({'id': 'nvidia', 'nombre': 'NVIDIA', 'doc': 'https://build.nvidia.com'}, _ficha('meta/llama-x')),
    ({'id': 'openrouter', 'nombre': 'OpenRouter'}, _ficha('openrouter/free', 'Free Models Router', 2_000_000)),
    ({'id': 'openrouter', 'nombre': 'OpenRouter'}, _ficha('google/gemma-4-31b-it:free', 'Gemma 4 31B (free)')),
    ({'id': 'openrouter', 'nombre': 'OpenRouter'},
     _ficha('nvidia/nemotron-3.5-super-vl:free', 'Nemotron 3.5 Super VL (free)', 1_000_000)),
]


@pytest.fixture
def sin_nada(monkeypatch):
    """Un Mac recién instalado: sin claves, sin Ollama y con estos gratis en el catálogo."""
    from alfalfa import catalogo
    monkeypatch.setattr(primera_vez, 'ollama_instalado', lambda: False)
    monkeypatch.setattr(primera_vez, 'ollama_responde', lambda get=None: False)
    monkeypatch.setattr(catalogo, 'gratis_ahora', lambda red=False: list(GRATIS))


def test_sin_claves_ni_ollama_es_la_primera_vez(sin_nada):
    assert primera_vez.sin_proveedor()


def test_una_clave_un_proveedor_elegido_o_ollama_ya_no(sin_nada, monkeypatch):
    llavero._almacen['openai'] = 'sk-prueba-123456'
    assert not primera_vez.sin_proveedor()
    llavero._almacen.clear()
    configuracion.cambiar(None, 'por_defecto', 'ollama')
    assert not primera_vez.sin_proveedor()
    configuracion.escribir({})
    monkeypatch.setattr(primera_vez, 'ollama_instalado', lambda: True)
    assert not primera_vez.sin_proveedor()


def test_ollama_responde_mira_la_url_local_y_acepta_una_remota(monkeypatch):
    llamadas = []

    def get(url, **kw):
        llamadas.append(url)
        return _Respuesta(200)

    assert primera_vez.ollama_responde(get)
    assert llamadas == ['http://127.0.0.1:11434/api/version']

    def caido(url, **kw):
        raise OSError('conexión rechazada')

    assert not primera_vez.ollama_responde(caido)
    configuracion.cambiar('ollama', 'url', 'https://ollama.example.com')
    assert primera_vez.ollama_responde(caido)


def test_el_recomendado_es_el_probado_y_nunca_un_enrutador(sin_nada):
    assert primera_vez.recomendado('openrouter') == 'nvidia/nemotron-3.5-super-vl:free'
    sin_preferido = [f for d, f in GRATIS if d['id'] == 'openrouter' and 'nemotron' not in f['id']]
    assert primera_vez.recomendado('openrouter', sin_preferido) == 'google/gemma-4-31b-it:free'
    assert primera_vez.recomendado('nadie', []) is None


def test_ofertas_pone_primero_lo_que_se_conecta_sin_clave(sin_nada):
    datos = primera_vez.ofertas()
    assert datos['sin_proveedor'] and datos['aviso'].startswith('Gratis')
    primero, segundo = datos['proveedores']
    assert primero['id'] == 'openrouter' and primero['conectar']
    assert primero['recomendado'] == 'openrouter/nvidia/nemotron-3.5-super-vl:free'
    assert len(primero['modelos']) == 3
    assert segundo['id'] == 'nvidia' and not segundo['conectar']
    texto = primera_vez.texto(datos)
    assert 'alfalfa --conectar openrouter' in texto
    assert 'alfalfa --guardar-clave nvidia' in texto
    assert 'puede usar tus datos para entrenar' in texto


def _navegador_que_autoriza(codigo='codigo-1', estado=None, rutas=None):
    """Hace de navegador: sigue la callback_url con el código como haría OpenRouter."""
    vistos = {}

    def abrir(url):
        q = parse_qs(urlsplit(url).query)
        vistos.update(q)
        destino = q['callback_url'][0]
        sufijo = {'code': codigo, 'state': estado or q['state'][0]} if codigo else {'error': 'denied'}

        def ir():
            from urllib.parse import urlencode
            for extra in rutas or []:
                try:
                    urllib.request.urlopen(destino.replace('localhost', '127.0.0.1') + extra, timeout=5)
                except Exception:
                    pass
            urllib.request.urlopen(destino.replace('localhost', '127.0.0.1') + '?' + urlencode(sufijo), timeout=5)
        threading.Thread(target=ir, daemon=True).start()
    return abrir, vistos


def test_obtener_clave_hace_pkce_y_canjea_el_codigo():
    abrir, vistos = _navegador_que_autoriza()
    pedidos = []

    def post(url, json, timeout):
        pedidos.append((url, json))
        return _Respuesta(200, {'key': 'sk-or-v1-prueba'})

    clave = conectar.obtener_clave(abrir=abrir, post=post, espera=10)
    assert clave == 'sk-or-v1-prueba'
    assert vistos['code_challenge_method'] == ['S256'] and vistos['key_label'] == ['AlfalfaCode']
    assert vistos['callback_url'][0].startswith('http://localhost:')
    url, cuerpo = pedidos[0]
    assert url == 'https://openrouter.ai/api/v1/auth/keys' and cuerpo['code'] == 'codigo-1'
    reto = base64.urlsafe_b64encode(hashlib.sha256(cuerpo['code_verifier'].encode()).digest()).rstrip(b'=')
    assert vistos['code_challenge'] == [reto.decode()]


def test_una_vuelta_con_otro_state_no_vale():
    # Primero llega una petición ajena con otro state; la buena después.
    abrir, _ = _navegador_que_autoriza(rutas=['?code=robado&state=otro'])
    clave = conectar.obtener_clave(abrir=abrir, post=lambda url, json, timeout: _Respuesta(
        200, {'key': 'sk-or-buena'} if json['code'] == 'codigo-1' else {}), espera=10)
    assert clave == 'sk-or-buena'


def test_si_el_usuario_no_autoriza_se_dice():
    abrir, _ = _navegador_que_autoriza(codigo=None)
    with pytest.raises(conectar.ErrorAlConectar, match='no dio permiso'):
        conectar.obtener_clave(abrir=abrir, post=lambda *a, **k: None, espera=10)


def test_sin_respuesta_a_tiempo_se_dice():
    with pytest.raises(conectar.ErrorAlConectar, match='a tiempo'):
        conectar.obtener_clave(abrir=lambda url: None, espera=0.2)


def test_un_canje_fallido_se_dice_sin_detalles():
    abrir, _ = _navegador_que_autoriza()
    with pytest.raises(conectar.ErrorAlConectar, match='HTTP 403'):
        conectar.obtener_clave(abrir=abrir, post=lambda *a, **k: _Respuesta(403, {'error': 'x'}), espera=10)


def test_conectar_guarda_la_clave_en_el_llavero_y_elige_un_gratis(sin_nada, monkeypatch):
    monkeypatch.setattr(conectar, 'obtener_clave', lambda **kw: 'sk-or-v1-guardada')
    hecho = conectar.conectar('openrouter')
    assert hecho == {'proveedor': 'openrouter', 'modelo': 'openrouter/nvidia/nemotron-3.5-super-vl:free'}
    assert llavero._almacen['openrouter'] == 'sk-or-v1-guardada'
    datos = configuracion.leer()
    assert datos['por_defecto'] == 'openrouter'
    assert datos['openrouter'] == {'url': 'https://openrouter.ai/api/v1', 'guardada': True,
                                   'modelo': 'nvidia/nemotron-3.5-super-vl:free'}
    assert 'sk-or' not in configuracion.ruta().read_text()
    assert terminal.modelo_por_defecto() == 'openrouter/nvidia/nemotron-3.5-super-vl:free'
    assert not primera_vez.sin_proveedor()


def test_solo_se_conecta_openrouter():
    with pytest.raises(conectar.ErrorAlConectar, match='guardar-clave nvidia'):
        conectar.conectar('nvidia')


def test_alfalfa_sin_nada_ensena_los_gratis_en_vez_de_un_error(sin_nada, capsys):
    assert terminal.main([]) == 0
    err = capsys.readouterr().err
    assert 'No hay ningún proveedor configurado' in err and 'alfalfa --conectar openrouter' in err


def test_un_encargo_sin_proveedor_no_va_a_ollama(sin_nada, capsys):
    assert terminal.main(['arregla el test']) == 1
    assert 'alfalfa --conectar openrouter' in capsys.readouterr().err


def test_en_la_terminal_se_ofrece_conectar_y_se_sigue_con_el_gratis(sin_nada, monkeypatch):
    class Tty(io.StringIO):
        def isatty(self):
            return True

    monkeypatch.setattr('builtins.input', lambda texto: '')
    monkeypatch.setattr(conectar, 'obtener_clave', lambda **kw: 'sk-or-v1-guardada')
    salida = io.StringIO()
    modelo = terminal.bienvenida(None, entrada=Tty(), salida=salida)
    assert modelo == 'openrouter/nvidia/nemotron-3.5-super-vl:free'
    assert 'OpenRouter conectado' in salida.getvalue() and 'sk-or' not in salida.getvalue()


def test_con_modelo_explicito_no_hay_bienvenida(sin_nada, monkeypatch):
    llamadas = []
    monkeypatch.setattr(terminal, 'bienvenida', lambda *a, **k: llamadas.append(1))

    async def ejecutar(args):
        return 0
    monkeypatch.setattr(terminal, 'execute', ejecutar)
    assert terminal.main(['--modelo', 'gpt-oss:20b', 'hola']) == 0
    assert not llamadas


def test_primera_vez_en_json_para_la_app(sin_nada, capsys):
    import json
    assert terminal.main(['--primera-vez', '--json']) == 0
    datos = json.loads(capsys.readouterr().out)
    assert datos['tipo'] == 'primera_vez' and datos['sin_proveedor']
    assert datos['proveedores'][0]['id'] == 'openrouter'
