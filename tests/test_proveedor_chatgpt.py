import asyncio
import hashlib
import json
import time
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from alfalfa import proveedores
from alfalfa.proveedores import chatgpt, llavero
from alfalfa.proveedores.conectar import ErrorAlConectar

CLIENTE = 'oaiapp_prueba'
MODELO = 'gpt-6.2-sol'
SCOPES = ' '.join(chatgpt.SCOPES)


@pytest.fixture
def almacen(monkeypatch, tmp_path):
    monkeypatch.setattr(llavero, '_almacen', {})
    monkeypatch.setenv('ALFALFA_HOME', str(tmp_path / 'alfalfa'))


def tokens(id_token=None, *, scope=SCOPES):
    return dict(access_token='acceso-de-prueba', refresh_token='renovacion-de-prueba',
                token_type='Bearer', expires_in=600, id_token=id_token, scope=scope)


@pytest.mark.parametrize('nonce_incorrecto', [False, True])
def test_inicio_oauth_oficial_con_pkce_y_oidc(almacen, monkeypatch, nonce_incorrecto):
    clave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = jwt.algorithms.RSAAlgorithm.to_jwk(clave.public_key(), as_dict=True)
    jwk['kid'] = 'prueba'
    pedido = {}

    def abrir(url):
        assert url.startswith(chatgpt.AUTORIZAR + '?')
        pedido.update({k: v[0] for k, v in parse_qs(urlsplit(url).query).items()})
        assert pedido['client_id'] == 'dynamic_agent_client'
        assert pedido['agent_name_hint'] == 'AlfalfaCode'
        assert pedido['redirect_uri'].startswith('http://127.0.0.1:')
        assert pedido['redirect_uri'].endswith('/auth/callback')
        assert pedido['ext_agent_host_id'].startswith('urn:uuid:')
        assert pedido['scope'] == SCOPES and pedido['resource'] == chatgpt.RECURSO
        with httpx.Client(trust_env=False) as cliente:
            assert cliente.get(pedido['redirect_uri'], params={'state': 'ajeno', 'code': 'ajeno'}).status_code == 400
            cliente.get(pedido['redirect_uri'], params={'state': pedido['state'], 'code': 'codigo-de-prueba',
                                                       'client_id': CLIENTE}).raise_for_status()

    def http(request):
        if str(request.url) == chatgpt.JWKS:
            return httpx.Response(200, json={'keys': [jwk]})
        assert str(request.url) == chatgpt.TOKEN
        campos = parse_qs(request.content.decode())
        assert campos['client_id'] == [CLIENTE] and campos['redirect_uri'] == [pedido['redirect_uri']]
        assert campos['grant_type'] == ['authorization_code'] and campos['resource'] == [chatgpt.RECURSO]
        digest = hashlib.sha256(campos['code_verifier'][0].encode()).digest()
        from base64 import urlsafe_b64encode
        assert pedido['code_challenge'] == urlsafe_b64encode(digest).decode().rstrip('=')
        id_token = jwt.encode({'iss': chatgpt.AUTORIDAD, 'aud': CLIENTE, 'sub': 'prueba@example.invalid',
                               'exp': int(time.time()) + 600,
                               'nonce': 'otro' if nonce_incorrecto else pedido['nonce']},
                              clave, algorithm='RS256', headers={'kid': 'prueba'})
        return httpx.Response(200, json=tokens(id_token))

    monkeypatch.setattr(chatgpt, '_http', lambda: httpx.Client(transport=httpx.MockTransport(http)))
    if nonce_incorrecto:
        with pytest.raises(ErrorAlConectar, match='verificar la identidad'):
            chatgpt.conectar(abrir=abrir)
        assert not chatgpt.ChatGPT().configurado()
    else:
        assert chatgpt.conectar(abrir=abrir) == {'proveedor': 'chatgpt', 'modelo': None}
        assert chatgpt.acceso() == 'acceso-de-prueba'
        estado = chatgpt.ChatGPT().estado()
        assert estado['configurado'] and 'acceso-de-prueba' not in json.dumps(estado)


def test_renueva_y_revoca_sin_volver_a_usar_la_credencial_anterior(almacen, monkeypatch):
    chatgpt._guardar(chatgpt._tokens(tokens()) | {'client_id': CLIENTE, 'host': 'host', 'vence': 0})
    peticiones = []

    def http(request):
        datos = parse_qs(request.content.decode())
        peticiones.append((str(request.url), datos))
        assert datos['client_id'] == [CLIENTE]
        if str(request.url) == chatgpt.TOKEN:
            assert datos['refresh_token'] == ['renovacion-de-prueba'] and 'scope' not in datos
            nuevos = dict(tokens(), access_token='acceso-nuevo', refresh_token='renovacion-nueva')
            return httpx.Response(200, json=nuevos)
        assert str(request.url) == chatgpt.REVOCAR and datos['token'] == ['renovacion-nueva']
        return httpx.Response(200)

    monkeypatch.setattr(chatgpt, '_http', lambda: httpx.Client(transport=httpx.MockTransport(http)))
    assert chatgpt.acceso() == chatgpt.acceso() == 'acceso-nuevo'
    assert len(peticiones) == 1
    assert chatgpt.desconectar()
    assert len(peticiones) == 2 and not chatgpt.ChatGPT().configurado()
    assert chatgpt._leer()['host'] == 'host'
    assert 'renovacion-nueva' not in chatgpt._archivo().read_text()
    assert chatgpt._archivo().stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize('evento, estado', [
    ({'type': 'response.completed', 'response': {'status': 'completed', 'output': [
        {'type': 'function_call', 'name': 'read_file', 'arguments': '{"path":"README.md"}', 'call_id': 'call_1'}]}},
     200),
    ({'type': 'response.failed',
      'response': {'error': {'code': 'subscription_sharing_usage_limit_exceeded', 'message': 'dato-privado'}}}, 403),
    ({'type': 'response.output_text.delta', 'delta': 'parcial'}, 502),
])
def test_responses_de_plan_respeta_el_contrato_y_devuelve_herramientas_al_motor(monkeypatch, evento, estado):
    monkeypatch.setattr(chatgpt, 'acceso', lambda: 'acceso-de-prueba')

    def http(request):
        assert str(request.url) == chatgpt.RECURSO + '/responses'
        assert request.headers['Authorization'] == 'Bearer acceso-de-prueba'
        datos = json.loads(request.content)
        assert datos['model'] == MODELO and datos['stream'] is True and datos['store'] is False
        assert 'max_output_tokens' not in datos
        assert datos['tools'][0]['name'] == 'read_file' and datos['tools'][0]['type'] == 'function'
        return httpx.Response(200, text='data: ' + json.dumps(evento) + '\n\ndata: [DONE]\n')

    async def turno():
        async with httpx.AsyncClient(base_url='https://local.invalid',
                                     transport=chatgpt.Transporte(5, transporte=httpx.MockTransport(http))) as cliente:
            return await cliente.post('/api/chat', json={
                'model': MODELO, 'messages': [{'role': 'user', 'content': 'Lee el README'}],
                'options': {'num_predict': 100}, 'tools': [{'function': {'name': 'read_file'}}]})

    respuesta = asyncio.run(turno())
    assert respuesta.status_code == estado
    if estado == 200:
        llamada, = respuesta.json()['message']['tool_calls']
        assert llamada['function'] == {'name': 'read_file', 'arguments': {'path': 'README.md'}}
    assert 'dato-privado' not in respuesta.text


def test_catalogo_usa_los_modelos_visibles_de_la_cuenta(almacen, monkeypatch):
    chatgpt._guardar(chatgpt._tokens(tokens()))
    def http(request):
        assert str(request.url) == chatgpt.RECURSO + '/models'
        return httpx.Response(200, json={'models': [
            {'slug': MODELO, 'display_name': 'Sol del plan', 'visibility': 'list'},
            {'slug': 'otro', 'visibility': 'hidden'}]})
    monkeypatch.setattr(chatgpt, 'cliente_de_lista', lambda: httpx.Client(transport=httpx.MockTransport(http)))
    proveedor, modelo = proveedores.para_modelo('chatgpt/' + MODELO)
    fila, = proveedor.modelos()
    assert modelo == MODELO and fila['name'] == 'chatgpt/' + MODELO and proveedor.precio(modelo) is None
