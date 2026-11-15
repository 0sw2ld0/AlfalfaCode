"""El bucle de reintentos del transporte: qué se reintenta, cuánto se espera y qué se avisa.

Hasta ahora ninguna prueba pasaba por `NativeRuntime._request`: todas sustituían `request`
entero. Aquí la petición sale de verdad por httpx, contra un transporte falso o un servidor en
127.0.0.1, y el reloj y las esperas son simulados.

Además fija tres arreglos:
- un corte limpio de la conexión a mitad del cuerpo se reintenta, igual que el mismo corte con RST;
- `Retry-After` mayor que el plazo falla al momento en vez de esperar diez minutos, `1.5` vale,
  y un 429 o 503 sin la cabecera espera más de uno o dos segundos;
- «transporte: intento» solo sale cuando de verdad se reintenta, con la causa, y la app lo
  aparta de la conversación sea cual sea el proveedor.
"""
import asyncio
import json

import httpx
import pytest

from alfalfa import stdio
from alfalfa.proveedores.ollama import Ollama
from alfalfa.reintentos.fallos import es_aviso_de_transporte
from alfalfa.terminal import TerminalManager

OK = {'message': {'role': 'assistant', 'content': 'hola'}, 'done': True}
CORTE_A_MITAD = 'peer closed connection without sending complete message body (incomplete chunked read)'


def r(estado, cuerpo, **cabeceras):
    if isinstance(cuerpo, bytes):
        return httpx.Response(estado, content=cuerpo, headers=cabeceras)
    return httpx.Response(estado, json=cuerpo, headers=cabeceras)


class Escena:
    def __init__(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self.m = TerminalManager('x', home=tmp_path / 'alfalfa', model='demo:latest')
        self.rt = self.m.runtime()
        self.rt._proveedor, self.rt._precio = Ollama(), None
        self.reloj, self.esperas, self.avisos, self.chat = 1000.0, [], [], 0
        self.rt._retry_clock = lambda: self.reloj

        async def dormir(segundos):
            self.esperas.append(round(segundos, 2))
            self.reloj += segundos

        self.rt._retry_sleep = dormir
        original = self.m.event

        def event(task, rol, texto, **kwargs):
            if rol == 'system':
                self.avisos.append(str(texto))
            return original(task, rol, texto, **kwargs)

        self.m.event = event

    async def pedir(self, *respuestas, url='https://simulado.test', **extra):
        cola = list(respuestas)

        def api(peticion):
            if peticion.url.path == '/api/show':
                return httpx.Response(200, json={'capabilities': ['tools']})
            self.chat += 1
            paso = cola.pop(0)
            if isinstance(paso, Exception):
                raise paso
            return paso

        transporte = None if url != 'https://simulado.test' else httpx.MockTransport(api)
        async with httpx.AsyncClient(base_url=url, transport=transporte) as cliente:
            if transporte is None:
                self.rt._dispatch_model_info = {(str(cliente.base_url), 'demo:latest'): {'capabilities': ['tools']}}
            return await self.rt.request(cliente, '/api/chat', {'model': 'demo:latest', 'messages': []}, **extra)

    def reintentos(self):
        return [t for t in self.avisos if es_aviso_de_transporte(t)]

    def error(self):
        return self.rt.task.get('runtime_provider_error') or {}


@pytest.fixture
def escena(tmp_path, monkeypatch):
    e = Escena(tmp_path, monkeypatch)
    yield e
    e.m.close()


# ---------------------------------------------------------------- qué se reintenta
@pytest.mark.parametrize('fallos', [
    [r(500, {'error': 'x'})],
    [r(502, {'error': 'x'}), r(504, {'error': 'x'})],
    [r(400, {'error': 'server overloaded'})],
    [httpx.ReadTimeout('lento')],
    [httpx.ConnectError('sin ruta')],
    [httpx.ReadError('connection reset by peer')],
    [httpx.RemoteProtocolError('Server disconnected without sending a response.')],
])
async def test_lo_transitorio_se_reintenta_y_acaba_bien(escena, fallos):
    assert await escena.pedir(*fallos, r(200, OK)) == OK
    assert escena.chat == len(fallos) + 1
    assert 'runtime_provider_error' not in escena.rt.task


@pytest.mark.parametrize('texto', [
    CORTE_A_MITAD,
    'peer closed connection without sending complete message body (received 300 bytes, expected 2046)',
])
async def test_un_corte_a_mitad_del_cuerpo_se_reintenta_como_el_rst(escena, texto):
    assert await escena.pedir(httpx.RemoteProtocolError(texto), r(200, OK)) == OK
    assert escena.chat == 2


async def test_cada_reintento_avisa_una_vez(escena):
    await escena.pedir(r(502, {'error': 'x'}), httpx.ReadTimeout('lento'), r(200, OK))
    primero, segundo = escena.reintentos()
    assert 'intento 1/3' in primero and 'HTTP 502' in primero
    assert 'intento 2/3' in segundo and 'sin respuesta a tiempo' in segundo


@pytest.mark.parametrize('respuesta, causa', [
    (r(401, {'error': 'unauthorized'}), 'access_denied'),
    (r(403, {'error': 'forbidden'}), 'access_denied'),
    (r(404, {'error': 'model "demo" not found'}), 'model_not_found'),
    (r(400, {'error': 'invalid request: unknown parameter foo'}), 'invalid_request'),
    (r(200, {'error': "model 'demo' not found, try pulling it first"}), 'model_not_found'),
    (r(200, b'<html>proxy</html>'), 'invalid_response'),
    (httpx.RemoteProtocolError('malformed chunk header'), 'unknown'),
])
async def test_lo_definitivo_o_desconocido_no_se_reintenta(escena, respuesta, causa):
    with pytest.raises(ValueError):
        await escena.pedir(respuesta, r(200, OK))
    assert escena.chat == 1 and escena.esperas == []
    assert escena.error()['cause'] == causa and escena.error()['retry'] is False


async def test_una_credencial_en_el_error_no_llega_al_mensaje(escena):
    with pytest.raises(ValueError) as fallo:
        await escena.pedir(r(401, {'error': 'Authorization: Bearer abc.def.ghi rechazado'}))
    assert 'abc.def.ghi' not in str(fallo.value)
    assert 'abc.def.ghi' not in json.dumps(escena.rt.task)


async def test_tres_fallos_seguidos_agotan_los_intentos(escena):
    with pytest.raises(ValueError, match='3 intentos'):
        await escena.pedir(*[httpx.ConnectError('sin ruta')] * 3)
    assert escena.chat == 3 and escena.esperas == [1.0, 2.0]
    assert escena.rt.task['runtime_transport_retry_exhaustion'] == 'attempt_limit'
    assert escena.rt.task['runtime_transport_attempts'] == 3


async def test_una_respuesta_de_mas_de_4_mb_no_se_reintenta(escena):
    with pytest.raises(ValueError, match='4 MB'):
        await escena.pedir(r(200, b'{"message": "' + b'a' * (4 * 1024 * 1024 + 10) + b'"}'))
    assert escena.chat == 1


async def test_con_el_plazo_vencido_no_sale_ninguna_peticion(escena):
    with pytest.raises(ValueError):
        await escena.pedir(r(200, OK), session_deadline=escena.reloj - 1)
    assert escena.chat == 0


async def test_el_plazo_corta_la_espera_y_no_se_renueva_al_reintentar(escena):
    with pytest.raises(ValueError):
        await escena.pedir(r(500, {'error': 'x'}), r(200, OK), session_deadline=escena.reloj + 0.5)
    assert escena.chat == 1 and escena.esperas == [0.5]
    assert escena.rt.task['runtime_transport_retry_exhaustion'] == 'session_deadline'


# ---------------------------------------------------------------- un corte real a mitad del cuerpo
class ServidorQueCorta:
    """Responde con Content-Length y cierra (FIN) tras mandar un trozo; la segunda vez, entero."""

    def __init__(self):
        self.peticiones = 0

    async def atender(self, lector, escritor):
        try:
            cabecera = await lector.readuntil(b'\r\n\r\n')
            largo = next((int(linea.split(b':', 1)[1]) for linea in cabecera.split(b'\r\n')
                          if linea.lower().startswith(b'content-length:')), 0)
            if largo:
                await lector.readexactly(largo)
            self.peticiones += 1
            cuerpo = json.dumps(dict(OK, relleno='x' * 2000)).encode()
            escritor.write(b'HTTP/1.1 200 OK\r\ncontent-type: application/json\r\ncontent-length: '
                           + str(len(cuerpo)).encode() + b'\r\n\r\n'
                           + (cuerpo[:300] if self.peticiones == 1 else cuerpo))
            await escritor.drain()
            escritor.close()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass


async def test_un_corte_limpio_a_mitad_de_la_respuesta_se_reintenta(escena):
    servidor = ServidorQueCorta()
    escucha = await asyncio.start_server(servidor.atender, '127.0.0.1', 0)
    try:
        puerto = escucha.sockets[0].getsockname()[1]
        valor = await escena.pedir(url='http://127.0.0.1:' + str(puerto))
    finally:
        escucha.close()
        await escucha.wait_closed()
    assert valor['message'] == OK['message']
    assert servidor.peticiones == 2
    aviso, = escena.reintentos()
    assert 'conexión cortada' in aviso


# ---------------------------------------------------------------- Retry-After
async def test_retry_after_en_segundos_se_respeta(escena):
    await escena.pedir(r(429, {'error': 'rate limit'}, **{'Retry-After': '7'}), r(200, OK))
    assert escena.esperas == [7.0]


async def test_retry_after_con_decimales_se_respeta(escena):
    await escena.pedir(r(429, {'error': 'rate limit'}, **{'Retry-After': '1.5'}), r(200, OK))
    assert escena.esperas == [1.5]


@pytest.mark.parametrize('valor', ['-3', 'nan', 'inf', 'pronto'])
async def test_un_retry_after_absurdo_se_ignora(escena, valor):
    await escena.pedir(r(500, {'error': 'x'}, **{'Retry-After': valor}), r(200, OK))
    assert escena.esperas == [1.0]


async def test_retry_after_con_fecha_pasada_usa_la_espera_normal(escena):
    await escena.pedir(r(500, {'error': 'x'}, **{'Retry-After': 'Wed, 21 Oct 2015 07:28:00 GMT'}), r(200, OK))
    assert escena.esperas == [1.0]


async def test_retry_after_mayor_que_el_plazo_falla_sin_esperar(escena):
    with pytest.raises(ValueError, match='pide esperar 3600 s'):
        await escena.pedir(r(429, {'error': 'rate limit'}, **{'Retry-After': '3600'}), r(200, OK))
    assert escena.esperas == [] and escena.chat == 1
    assert escena.rt.task['runtime_transport_retry_exhaustion'] == 'retry_after'


@pytest.mark.parametrize('estado', [429, 503])
async def test_saturado_sin_retry_after_espera_mas(escena, estado):
    with pytest.raises(ValueError):
        await escena.pedir(*[r(estado, {'error': 'x'})] * 3)
    assert escena.chat == 3 and escena.esperas == [5.0, 10.0]


async def test_un_500_sin_retry_after_espera_poco(escena):
    await escena.pedir(r(500, {'error': 'x'}), r(500, {'error': 'x'}), r(200, OK))
    assert escena.esperas == [1.0, 2.0]


# ---------------------------------------------------------------- avisos
async def test_una_peticion_que_va_bien_no_avisa_de_ningun_intento(escena):
    assert await escena.pedir(r(200, OK)) == OK
    assert not [t for t in escena.avisos if 'transporte' in t]
    assert escena.rt.task['runtime_transport_attempts'] == 1


async def test_el_aviso_de_reintento_dice_que_fallo_y_cuanto_espera(escena):
    await escena.pedir(r(503, {'error': 'x'}), r(200, OK))
    aviso, = escena.reintentos()
    assert aviso == 'Ollama transporte: intento 1/3 falló (fallo del servidor, HTTP 503); reintento en 5 s.'


@pytest.mark.parametrize('texto, es', [
    ('Ollama transporte: intento 1/3.', True),
    ('OpenRouter transporte: intento 2/3 falló (conexión cortada); reintento en 2 s.', True),
    ('DeepSeek transporte: intento 1/3.', True),
    ('El modelo dijo: «Ollama transporte: intento 1/3.»', False),
    ('Ollama transporte: agotados 3 intentos; la petición no se completó.', False),
])
def test_el_filtro_reconoce_el_aviso_de_cualquier_proveedor(texto, es):
    assert es_aviso_de_transporte(texto) is es


def test_al_reabrir_la_sesion_no_salen_los_avisos_de_otro_proveedor(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('ALFALFA_HOME', str(tmp_path / 'alfalfa'))
    m = TerminalManager('x', home=tmp_path / 'alfalfa', model='demo:latest')
    try:
        m.event(m.task, 'system', 'OpenRouter transporte: intento 1/3.')
        m.event(m.task, 'system', 'OpenRouter transporte: intento 1/3 falló (fallo de red); reintento en 1 s.')
        m.event(m.task, 'system', 'Aviso que sí importa.')
        textos = json.dumps(stdio.history(m.task['id']), ensure_ascii=False)
    finally:
        m.close()
    assert 'transporte: intento' not in textos
    assert 'Aviso que sí importa.' in textos
