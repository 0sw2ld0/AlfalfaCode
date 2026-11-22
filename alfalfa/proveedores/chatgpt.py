"""Inferencia con el plan de ChatGPT. AlfalfaCode dirige las sesiones y las herramientas."""
from __future__ import annotations

import asyncio
import fcntl
import hashlib
import json
import os
import secrets
import stat
import tempfile
import threading
import time
from contextlib import contextmanager
from urllib.parse import parse_qs, urlencode, urlsplit
from uuid import uuid4

import httpx
import jwt
from cryptography.fernet import Fernet, InvalidToken

from . import ErrorDeProveedor, Proveedor, cliente_de_lista, configuracion, llavero, openai
from .conectar import ESPERA, ErrorAlConectar, _manejador, _Servidor, abrir_en_navegador, pkce

ID = 'chatgpt'
NOMBRE = 'Plan de ChatGPT'
AUTORIDAD = 'https://auth.openai.com'
RECURSO = 'https://api.openai.com/v1'
AUTORIZAR = AUTORIDAD + '/api/accounts/authorize'
TOKEN = AUTORIDAD + '/api/accounts/oauth/token'
REVOCAR = AUTORIDAD + '/api/accounts/oauth/revoke'
JWKS = AUTORIDAD + '/.well-known/jwks.json'
CALLBACK = '/auth/callback'
DINAMICO = 'dynamic_agent_client'
PERMISO = 'chatgpt.tokens.use.direct'
SCOPES = ('openid', 'profile', 'email', 'offline_access', 'resource.invoke', PERMISO)
SECRETOS = ('access_token', 'refresh_token', 'id_token')
AVISO = 'Conecta tu plan con `alfalfa --conectar chatgpt` o desde Ajustes ▸ Proveedores.'


def _cuenta():
    # El bloqueo y el Llavero deben compartir el mismo ámbito, también con ALFALFA_HOME.
    ruta = str(configuracion.ruta().parent.resolve())
    return 'chatgpt-' + hashlib.sha256(ruta.encode()).hexdigest()[:32]


@contextmanager
def _bloqueo():
    carpeta = configuracion.ruta().parent
    carpeta.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(carpeta / 'chatgpt.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def _archivo():
    return configuracion.ruta().with_name('chatgpt.enc')


def _leer():
    try:
        fd = os.open(_archivo(), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return {}
    try:
        with os.fdopen(fd, 'rb') as archivo:
            modo = os.fstat(archivo.fileno())
            if not stat.S_ISREG(modo.st_mode) or modo.st_uid != os.getuid() or modo.st_mode & 0o077:
                raise ValueError()
            cifrado = archivo.read(65537)
        if len(cifrado) > 65536:
            raise ValueError()
        clave = llavero.leer(_cuenta())
        if not clave:
            raise ValueError()
        datos = json.loads(Fernet(clave.encode()).decrypt(cifrado))
        if not isinstance(datos, dict):
            raise ValueError()
        return datos
    except (InvalidToken, OSError, ValueError, TypeError):
        raise ErrorAlConectar('La conexión de ChatGPT guardada no se puede leer. Revisa el Llavero.') from None


def _guardar(datos):
    # security -i tiene un límite de 4096 bytes por línea. Guarda sólo la clave
    # de cifrado en el Llavero y el conjunto de tokens cifrados en un solo archivo.
    clave = llavero.leer(_cuenta(), actual=True)
    if not clave:
        clave = Fernet.generate_key().decode()
        llavero.guardar(_cuenta(), clave)
    cifrado = Fernet(clave.encode()).encrypt(json.dumps(datos, ensure_ascii=True).encode())
    destino = _archivo()
    destino.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, nombre = tempfile.mkstemp(prefix='.chatgpt-', dir=destino.parent)
    try:
        with os.fdopen(fd, 'wb') as archivo:
            archivo.write(cifrado)
            archivo.flush()
            os.fsync(archivo.fileno())
        os.replace(nombre, destino)
    finally:
        if os.path.exists(nombre):
            os.unlink(nombre)


def _http():
    return httpx.Client(timeout=15, trust_env=False)


def _post(http, url, datos):
    try:
        respuesta = http.post(url, data=datos)
    except httpx.HTTPError:
        raise ErrorAlConectar('No se pudo conectar con el servicio de autorización de ChatGPT.') from None
    return respuesta


def _renovar(http, datos):
    respuesta = _post(http, TOKEN, dict(datos, resource=RECURSO))
    if respuesta.is_error:
        raise ErrorAlConectar(f'ChatGPT rechazó la autorización (HTTP {respuesta.status_code}). ' + AVISO)
    try:
        valor = respuesta.json()
    except ValueError:
        valor = None
    if not isinstance(valor, dict):
        raise ErrorAlConectar('ChatGPT devolvió una autorización inválida.')
    return valor


def _tokens(valor, anteriores=None):
    anteriores = anteriores or {}
    scope = valor.get('scope', anteriores.get('scope'))
    expiracion = valor.get('expires_in')
    acceso = valor.get('access_token')
    renovacion = valor.get('refresh_token', anteriores.get('refresh_token'))
    if (not isinstance(scope, str) or not {'resource.invoke', PERMISO}.issubset(scope.split())
            or str(valor.get('token_type', '')).lower() != 'bearer'
            or not isinstance(acceso, str) or not acceso
            or not isinstance(renovacion, str) or not renovacion
            or type(expiracion) is not int or expiracion <= 0):
        raise ErrorAlConectar('ChatGPT no concedió credenciales válidas para usar el plan. ' + AVISO)
    return {'access_token': acceso, 'refresh_token': renovacion, 'scope': scope,
            'vence': time.time() + expiracion}


def _identidad(http, token, cliente, nonce):
    try:
        cabecera = jwt.get_unverified_header(token)
        kid = cabecera['kid']
        if cabecera.get('alg') != 'RS256' or not isinstance(kid, str):
            raise ValueError()
        respuesta = http.get(JWKS)
        respuesta.raise_for_status()
        claves = [j for j in respuesta.json()['keys'] if j.get('kid') == kid and j.get('kty') == 'RSA']
        if len(claves) != 1:
            raise ValueError()
        clave = jwt.PyJWK.from_dict(claves[0], algorithm='RS256').key
        datos = jwt.decode(token, clave, algorithms=['RS256'], issuer=AUTORIDAD, audience=cliente,
                           options={'require': ['iss', 'aud', 'exp', 'sub', 'nonce']}, leeway=30)
        aud = datos['aud']
        if (datos.get('azp', cliente) != cliente or (isinstance(aud, list) and len(aud) > 1
                                                    and datos.get('azp') != cliente)):
            raise ValueError()
        if not isinstance(datos['nonce'], str) or not secrets.compare_digest(datos['nonce'].encode(), nonce.encode()):
            raise ValueError()
        if not datos['sub']:
            raise ValueError()
        return datos
    except (jwt.PyJWTError, httpx.HTTPError, ValueError, TypeError, KeyError, AttributeError):
        raise ErrorAlConectar('No se pudo verificar la identidad devuelta por ChatGPT.') from None


class _Vuelta:
    def __init__(self, estado):
        self.estado = estado
        self.listo = threading.Event()
        self.codigo = None
        self.error = None
        self.cliente = None


class _ServidorChatGPT(_Servidor):
    def get_request(self):
        conexion, direccion = super().get_request()
        conexion.settimeout(3)
        return conexion, direccion


def _manejador_chatgpt(vuelta, cliente):
    class Manejador(_manejador(vuelta)):
        def do_GET(self):  # noqa: N802 (http.server)
            partes = urlsplit(self.path)
            if partes.path != CALLBACK:
                self.send_error(404)
                return
            datos = parse_qs(partes.query)
            estado = datos.get('state', [])
            if len(estado) != 1 or not secrets.compare_digest(estado[0].encode(), vuelta.estado.encode()):
                self._responder(400, 'Autorización inválida', 'Vuelve a la ventana que inició la conexión.')
                return
            codigo = datos.get('code', [])
            recibidos = datos.get('client_id', [])
            emitido = recibidos[0] if len(recibidos) == 1 else (cliente if not recibidos else '')
            if ('error' in datos or len(codigo) != 1 or not emitido.startswith('oaiapp_')
                    or (cliente != DINAMICO and emitido != cliente)):
                vuelta.error = 'ChatGPT no autorizó la conexión con AlfalfaCode.'
            else:
                vuelta.codigo, vuelta.cliente = codigo[0], emitido
            vuelta.listo.set()
            self._responder(200, 'Vuelve a AlfalfaCode', 'Puedes cerrar esta pestaña.')

    return Manejador


def conectar(*, abrir=abrir_en_navegador, al_abrir=None, espera=ESPERA):
    if not llavero.disponible():
        raise ErrorAlConectar('La conexión con ChatGPT requiere el Llavero de macOS.')
    with _bloqueo():
        previa = _leer()
        host = previa.get('host')
        if not host:
            host = uuid4().urn
            _guardar({'host': host})
    # Mientras haya una cuenta activa, vuelve a autorizar ese mismo registro.
    cliente = previa.get('client_id') if previa.get('access_token') else DINAMICO
    estado, nonce = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    verifier, challenge = pkce()
    vuelta = _Vuelta(estado)
    with _ServidorChatGPT(('127.0.0.1', 0), _manejador_chatgpt(vuelta, cliente)) as servidor:
        callback = f'http://127.0.0.1:{servidor.server_address[1]}{CALLBACK}'
        hilo = threading.Thread(target=servidor.serve_forever, kwargs={'poll_interval': 0.2}, daemon=True)
        hilo.start()
        try:
            consulta = {'client_id': cliente, 'ext_agent_host_id': host, 'redirect_uri': callback,
                        'response_type': 'code', 'scope': ' '.join(SCOPES), 'resource': RECURSO,
                        'state': estado, 'nonce': nonce, 'code_challenge': challenge, 'code_challenge_method': 'S256'}
            if cliente == DINAMICO:
                consulta['agent_name_hint'] = 'AlfalfaCode'
            url = AUTORIZAR + '?' + urlencode(consulta)
            if al_abrir:
                al_abrir(url)
            abrir(url)
            if not vuelta.listo.wait(espera):
                raise ErrorAlConectar('No llegó la autorización de ChatGPT a tiempo.')
            if vuelta.error:
                raise ErrorAlConectar(vuelta.error)
        finally:
            servidor.shutdown()
    with _http() as http:
        valor = _renovar(http, {'grant_type': 'authorization_code', 'client_id': vuelta.cliente,
                               'code': vuelta.codigo, 'code_verifier': verifier, 'redirect_uri': callback})
        identidad = _identidad(http, valor.get('id_token'), vuelta.cliente, nonce)
    if cliente != DINAMICO and previa.get('sub') != identidad['sub']:
        raise ErrorAlConectar('ChatGPT devolvió otra cuenta. Desconecta primero la cuenta activa para cambiarla.')
    datos = dict(_tokens(valor), host=host, client_id=vuelta.cliente, id_token=valor['id_token'],
                 sub=identidad['sub'], email=identidad.get('email', ''))
    with _bloqueo():
        actual = _leer()
        if actual.get('host') != host or actual.get('id_token') != previa.get('id_token'):
            raise ErrorAlConectar('La cuenta de ChatGPT cambió durante la conexión. Vuelve a intentarlo.')
        _guardar(datos)
    return {'proveedor': ID, 'modelo': None}


def acceso():
    with _bloqueo():
        actual = _leer()
        if not actual.get('refresh_token'):
            raise ErrorAlConectar(AVISO)
        if actual.get('vence', 0) > time.time() + 60:
            return actual['access_token']
        with _http() as http:
            valor = _renovar(http, {'grant_type': 'refresh_token', 'client_id': actual['client_id'],
                                   'refresh_token': actual['refresh_token']})
        nuevo = _tokens(valor, actual)
        _guardar(dict(actual, **nuevo))
        return nuevo['access_token']


def desconectar():
    confirmada = True
    with _bloqueo():
        datos = _leer()
        if datos.get('refresh_token'):
            try:
                with _http() as http:
                    confirmada = _post(http, REVOCAR, {'token': datos['refresh_token'],
                                       'token_type_hint': 'refresh_token', 'client_id': datos['client_id']}).is_success
            except ErrorAlConectar:
                confirmada = False
            _guardar({k: v for k, v in datos.items() if k not in (*SECRETOS, 'vence', 'scope')})
    return confirmada


class Transporte(httpx.AsyncBaseTransport):
    """Responses en streaming con herramientas de función aprobadas por el arnés local."""

    def __init__(self, timeout, tarea=None, transporte=None):
        self.http = httpx.AsyncClient(timeout=timeout, trust_env=False, transport=transporte)
        self.tarea = tarea or {}

    async def aclose(self):
        await self.http.aclose()

    async def handle_async_request(self, request):
        ruta = request.url.path
        try:
            pedido = json.loads(await request.aread() or b'{}')
        except ValueError:
            pedido = {}
        modelo = pedido.get('model') if isinstance(pedido, dict) else None
        if not isinstance(modelo, str) or not modelo:
            return openai._json(400, {'error': 'Falta el modelo del plan de ChatGPT.'})
        if ruta == '/api/show':
            return openai._json(200, openai.mostrar(modelo))
        if ruta != '/api/chat':
            return openai._json(404, {'error': 'Ruta no disponible con el plan de ChatGPT.'})
        try:
            credencial = await asyncio.to_thread(acceso)
        except ErrorAlConectar as exc:
            return openai._json(401, {'error': str(exc)})
        razona, admitidos = openai.razonamiento(modelo)
        cuerpo = openai.traducir_peticion(pedido, self.tarea.get('effort'), razona=razona, admitidos=admitidos)
        cuerpo.pop('max_output_tokens', None)
        cuerpo['stream'] = True
        async with self.http.stream('POST', RECURSO + '/responses', json=cuerpo,
                                    headers={'Authorization': 'Bearer ' + credencial}) as respuesta:
            if respuesta.is_error:
                try:
                    datos = json.loads(await respuesta.aread())
                except ValueError:
                    datos = {}
                return _error(respuesta.status_code, datos.get('error') if isinstance(datos, dict) else None)
            async for linea in respuesta.aiter_lines():
                if not linea.startswith('data:') or linea[5:].strip() == '[DONE]':
                    continue
                try:
                    evento = json.loads(linea[5:])
                except ValueError:
                    return openai._json(502, {'error': 'El plan de ChatGPT envió un evento inválido.'})
                if not isinstance(evento, dict):
                    continue
                tipo = evento.get('type')
                detalle = evento.get('response') or {}
                if tipo == 'response.completed' and isinstance(detalle, dict) and detalle.get('status') == 'completed':
                    return openai._json(200, openai.traducir_respuesta(detalle, modelo))
                if tipo in ('response.failed', 'response.incomplete', 'error'):
                    error = evento if tipo == 'error' else (detalle.get('error') if isinstance(detalle, dict) else None)
                    return _error(502, error)
        return openai._json(502, {'error': 'El plan de ChatGPT terminó sin completar la respuesta.'})


def _error(estado, error):
    codigo = error.get('code') if isinstance(error, dict) else None
    if codigo in ('subscription_sharing_usage_limit_exceeded', 'subscription_sharing_usage_unavailable'):
        return openai._json(403, {'error': 'El plan de ChatGPT no tiene cuota disponible para esta petición. '
                                          'Consulta https://chatgpt.com/settings/usage.'})
    if codigo == 'model_not_found':
        return openai._json(404, {'error': 'model_not_found: el modelo no está disponible para esta cuenta.'})
    if estado in (401, 403):
        return openai._json(estado, {'error': 'El plan de ChatGPT rechazó el acceso. ' + AVISO})
    # La respuesta remota puede repetir el prompt o una credencial. Sólo mostramos el estado.
    return openai._json(estado, {'error': f'El plan de ChatGPT no completó la petición (HTTP {estado}).'})


class ChatGPT(Proveedor):
    id = ID
    nombre = NOMBRE

    def configurado(self):
        return bool(_leer().get('refresh_token'))

    def secretos(self):
        datos = _leer()
        clave = llavero.leer(_cuenta()) if datos else None
        return [v for v in (*[datos.get(k) for k in SECRETOS], clave) if v]

    def falta(self):
        return AVISO

    def accion(self, clas, error_text):
        if clas.get('cause') == 'access_denied':
            if 'cuota disponible' in (error_text or ''):
                return 'revisa el uso del plan en https://chatgpt.com/settings/usage.'
            return AVISO
        return super().accion(clas, error_text)

    def cliente(self, timeout, tarea=None, al_cambiar_cuota=None):
        return httpx.AsyncClient(base_url='https://chatgpt.alfalfa.invalid',
                                 transport=Transporte(timeout, tarea), timeout=timeout)

    def estado(self):
        datos = _leer()
        configurado = bool(datos.get('refresh_token'))
        return {'id': ID, 'nombre': NOMBRE, 'configurado': configurado,
                'aviso': datos.get('email') if configurado else None}

    def modelos(self):
        if not self.configurado():
            return []
        credencial = acceso()
        try:
            with cliente_de_lista() as http:
                respuesta = http.get(RECURSO + '/models', headers={'Authorization': 'Bearer ' + credencial})
                respuesta.raise_for_status()
                datos = respuesta.json()
        except (httpx.HTTPError, ValueError):
            raise ErrorDeProveedor('No se pudo consultar el catálogo del plan de ChatGPT.') from None
        filas = datos.get('models') if isinstance(datos, dict) else None
        if not isinstance(filas, list):
            raise ErrorDeProveedor('El plan de ChatGPT devolvió un catálogo inválido.')
        modelos = []
        for fila in filas:
            if not isinstance(fila, dict) or fila.get('visibility') != 'list':
                continue
            slug = fila.get('slug')
            if not isinstance(slug, str) or not openai.es_de_chat(slug):
                continue
            nombre = fila.get('display_name')
            info = openai.mostrar(slug)
            vision = None if info.get('vision_conocida') is False else 'vision' in info['capabilities']
            modelos.append({'name': ID + '/' + slug, 'model': ID + '/' + slug, 'proveedor': ID,
                            'nombre': nombre if isinstance(nombre, str) and nombre else slug,
                            'ventana': info['model_info']['openai.context_length'],
                            'vision': vision})
        return modelos
