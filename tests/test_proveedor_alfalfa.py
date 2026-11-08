"""El proveedor Alfalfa: código de cortesía atado a este Mac, firma y cuota."""
import asyncio
import base64
import io
import json
import shutil
import sys
import time

import httpx
import pytest

from alfalfa import proveedores
from alfalfa.proveedores import alfalfa, configuracion, llavero

ec = pytest.importorskip('cryptography.hazmat.primitives.asymmetric.ec')
from cryptography.exceptions import InvalidSignature  # noqa: E402
from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402

CODIGO = 'CUY-ABCD-EFGH-JKMN'
URL = 'https://alfalfa.example.com:8444'


class FirmanteDePrueba:
    """Lo que hace el Secure Enclave, con una clave en memoria."""

    def __init__(self):
        self.privada = ec.generate_private_key(ec.SECP256R1())
        self.creada = False

    def tiene_clave(self):
        return self.creada

    def publica(self):
        self.creada = True
        crudo = self.privada.public_key().public_bytes(serialization.Encoding.X962,
                                                       serialization.PublicFormat.UncompressedPoint)
        return base64.b64encode(crudo).decode()

    def firmar(self, datos):
        return base64.b64encode(self.privada.sign(datos, ec.ECDSA(hashes.SHA256()))).decode()

    async def firmar_async(self, datos):
        return self.firmar(datos)


def verificar(publica_b64, request, cuerpo):
    publica = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), base64.b64decode(publica_b64))
    h = request.headers
    datos = alfalfa.canonico(request.method, request.url.path, h['x-alfalfa-marca'], h['x-alfalfa-nonce'], cuerpo)
    try:
        publica.verify(base64.b64decode(h['x-alfalfa-firma']), datos, ec.ECDSA(hashes.SHA256()))
        return True
    except InvalidSignature:
        return False


class Servicio:
    """El servicio Alfalfa en memoria: verifica la firma como el de verdad."""

    def __init__(self, respuestas=None):
        self.publica = None
        self.vistas = []
        self.respuestas = list(respuestas or [])

    def __call__(self, request):
        cuerpo = request.content
        self.vistas.append((request.method, request.url.path, json.loads(cuerpo) if cuerpo else None))
        assert request.headers['x-alfalfa-codigo'] == CODIGO
        assert abs(int(request.headers['x-alfalfa-marca']) - time.time()) < 60
        if request.url.path == '/activar':
            self.publica = json.loads(cuerpo)['clave_publica']
        if not verificar(self.publica, request, cuerpo):
            return httpx.Response(403, json={'error': 'firma'})
        cuotas = {'cuotas': [{'proveedor': 'openai', 'usado_pct': 12.5, 'renueva': '2026-12-01'},
                             {'proveedor': 'ollama', 'usado_pct': 3.0, 'renueva': '2026-12-01'}]}
        if request.url.path == '/activar':
            return httpx.Response(200, json={'ok': True, 'nombre': 'Ana', 'caduca': None, **cuotas,
                                             'modelos': [{'name': 'gpt-6-luna'}, {'name': 'glm-5.3-flash'}]})
        if request.url.path == '/api/tags':
            return httpx.Response(200, json={'models': [{'name': 'gpt-6-luna', 'nombre': 'GPT-6 Luna'},
                                                        {'name': 'glm-5.3-flash', 'nombre': 'GLM 5.3 Flash'},
                                                        {'name': 'deepseek-v4.1-flash', 'nombre': 'DeepSeek v4.1 Flash'}]})
        if request.url.path == '/cuota':
            return httpx.Response(200, json=cuotas)
        if request.url.path == '/api/show':
            return httpx.Response(200, json={'capabilities': ['completion', 'tools', 'thinking', 'vision'],
                                             'remote_host': 'alfalfa', 'model_info': {'alfalfa.context_length': 272000}})
        estado, valor = self.respuestas.pop(0) if self.respuestas else (200, {
            'model': 'gpt-6-luna', 'done': True, 'done_reason': 'stop', 'prompt_eval_count': 50, 'eval_count': 5,
            'message': {'role': 'assistant', 'content': 'Hola desde Alfalfa.'}, 'alfalfa': cuotas})
        return httpx.Response(estado, json=valor)


@pytest.fixture
def servicio(monkeypatch):
    falso = Servicio()
    firmante = FirmanteDePrueba()
    monkeypatch.setattr(alfalfa, 'Firmante', lambda *a, **k: firmante)
    monkeypatch.setattr(alfalfa, 'cliente_de_lista',
                        lambda: httpx.Client(transport=httpx.MockTransport(falso)))
    real = alfalfa.Transporte.__init__

    def init(self, codigo, firmante, tarea=None, al_cambiar_cuota=None, interno=None):
        real(self, codigo, firmante, tarea, al_cambiar_cuota, httpx.MockTransport(falso))
    monkeypatch.setattr(alfalfa.Transporte, '__init__', init)
    configuracion.cambiar('alfalfa', 'url', URL)
    falso.firmante = firmante
    return falso


def test_sin_url_ni_codigo_no_esta_configurado():
    p = proveedores.obtener('alfalfa')
    assert not p.configurado()
    with pytest.raises(alfalfa.ErrorDeAlfalfa, match='dirección del servicio'):
        p.url()


def test_activar_ata_el_codigo_y_lo_guarda_en_el_llavero(servicio):
    hecho = proveedores.obtener('alfalfa').activar(' cuy-abcd-efgh-jkmn ')
    assert hecho['nombre'] == 'Ana' and hecho['modelos'] == ['gpt-6-luna', 'glm-5.3-flash']
    assert hecho['cuotas'][0] == {'proveedor': 'openai', 'nombre': 'GPT-6 Luna', 'usado_pct': 12.5,
                                  'renueva': '2026-12-01'}
    metodo, ruta, cuerpo = servicio.vistas[0]
    assert (metodo, ruta) == ('POST', '/activar') and cuerpo['codigo'] == CODIGO and cuerpo['equipo']
    assert llavero.leer('alfalfa') == CODIGO
    assert proveedores.obtener('alfalfa').configurado()


def test_un_codigo_que_no_es_cuy_no_se_envia(servicio):
    with pytest.raises(alfalfa.ErrorDeAlfalfa, match='CUY-'):
        proveedores.obtener('alfalfa').activar('hola')
    assert servicio.vistas == []


def test_la_lista_lleva_el_sufijo_alfalfa(servicio):
    proveedores.obtener('alfalfa').activar(CODIGO)
    nombres = [(m['name'], m['nombre']) for m in proveedores.obtener('alfalfa').modelos()]
    assert nombres == [('alfalfa/gpt-6-luna', 'GPT-6 Luna - Alfalfa'),
                       ('alfalfa/glm-5.3-flash', 'GLM 5.3 Flash - Alfalfa'),
                       ('alfalfa/deepseek-v4.1-flash', 'DeepSeek v4.1 Flash - Alfalfa')]
    modelos, fallos = proveedores.listar_modelos()
    assert 'alfalfa/gpt-6-luna' in [m['name'] for m in modelos]


def test_el_codigo_nunca_sale_en_el_protocolo(servicio):
    from alfalfa.stdio import Transport
    proveedores.obtener('alfalfa').activar(CODIGO)
    salida = io.StringIO()
    Transport(salida).emit('evento', texto='mi código es ' + CODIGO)
    assert CODIGO not in salida.getvalue()


def test_la_orden_activa_por_la_entrada_y_da_la_cuota(servicio, capsys):
    from alfalfa.terminal import main
    monkey_stdin = io.StringIO(CODIGO + '\n')
    sys_stdin, sys.stdin = sys.stdin, monkey_stdin
    try:
        assert main(['--activar-codigo', '--json']) == 0
    finally:
        sys.stdin = sys_stdin
    activado = json.loads(capsys.readouterr().out)
    assert activado['tipo'] == 'activado' and CODIGO not in json.dumps(activado)
    assert main(['--cuota', '--json']) == 0
    cuota = json.loads(capsys.readouterr().out)
    assert cuota['tipo'] == 'cuota' and cuota['cuotas'][1]['usado_pct'] == 3.0


def test_un_turno_firma_cada_peticion_y_emite_la_cuota(servicio, tmp_path, monkeypatch):
    from alfalfa.stdio import Transport
    from alfalfa.terminal import TerminalManager
    proveedores.obtener('alfalfa').activar(CODIGO)
    monkeypatch.chdir(tmp_path)
    salida = io.StringIO()
    m = TerminalManager('Hola', home=tmp_path / 'home', model='alfalfa/gpt-6-luna', effort='xhigh', auto=True,
                        transport=Transport(salida))
    try:
        assert asyncio.run(asyncio.wait_for(m.run('Hola'), 60)) == 0, m.task.get('summary')
        chats = [v for v in servicio.vistas if v[1] == '/api/chat']
        assert chats and chats[0][2]['model'] == 'gpt-6-luna' and chats[0][2]['esfuerzo'] == 'xhigh'
        lineas = [json.loads(linea) for linea in salida.getvalue().splitlines()]
        cuota = [fila for fila in lineas if fila['tipo'] == 'cuota']
        assert cuota and cuota[0]['servicio'] == 'alfalfa' and cuota[0]['cuotas'][0]['usado_pct'] == 12.5
        assert m.task['provider'] == 'alfalfa'
        assert CODIGO not in salida.getvalue()
    finally:
        m.close()


def test_cuota_agotada_se_explica_y_no_se_reintenta(servicio, tmp_path, monkeypatch):
    from alfalfa.terminal import TerminalManager
    proveedores.obtener('alfalfa').activar(CODIGO)
    servicio.respuestas = [(402, {'error': 'Cuota del mes agotada para GPT-6 Luna; se renueva el 2026-12-01.',
                                  'alfalfa': {'causa': 'cuota_agotada'}})]
    monkeypatch.chdir(tmp_path)
    m = TerminalManager('Hola', home=tmp_path / 'home', model='alfalfa/gpt-6-luna', auto=True)
    try:
        assert asyncio.run(asyncio.wait_for(m.run('Hola'), 60)) != 0
        error = m.task['runtime_provider_error']
        assert error['status_code'] == 402 and error['retry'] is False and error['provider'] == 'alfalfa'
        assert 'cuota del mes agotada' in error['action']
        assert len([v for v in servicio.vistas if v[1] == '/api/chat']) == 1
    finally:
        m.close()


@pytest.mark.skipif(sys.platform != 'darwin' or not shutil.which('swiftc'), reason='necesita macOS y swiftc')
def test_el_ayudante_firma_con_el_secure_enclave(tmp_path):
    firmante = alfalfa.Firmante(tmp_path)
    try:
        publica = firmante.publica()
    except alfalfa.ErrorDeAlfalfa as exc:
        if 'Secure Enclave' in str(exc):
            pytest.skip(str(exc))
        raise
    assert firmante.tiene_clave() and (tmp_path / 'alfalfa-firma.clave').stat().st_mode & 0o777 == 0o600
    assert firmante.publica() == publica  # la misma clave, no una nueva
    datos = alfalfa.canonico('POST', '/api/chat', 1, 'nonce-de-prueba', b'{}')
    firma = base64.b64decode(firmante.firmar(datos))
    clave = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), base64.b64decode(publica))
    clave.verify(firma, datos, ec.ECDSA(hashes.SHA256()))
    assert asyncio.run(firmante.firmar_async(datos))
