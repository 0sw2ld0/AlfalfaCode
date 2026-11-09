"""Conectar un proveedor sin pegar su clave: OpenRouter con OAuth PKCE.

OpenRouter deja que una aplicación pida al usuario, en su propia web, una clave
para ella (https://openrouter.ai/docs/guides/overview/auth/oauth):

1. Se abre en el navegador `https://openrouter.ai/auth?callback_url=…&code_challenge=…`.
   El usuario entra (o crea su cuenta gratis) y pulsa «Authorize».
2. OpenRouter vuelve a `callback_url` con `?code=…`. Aquí es un servidor HTTP
   efímero en `localhost` (OpenRouter admite localhost en cualquier puerto) que
   sólo atiende esa ruta, comprueba `state` y se cierra.
3. El código y el `code_verifier` se cambian por la clave en
   `POST https://openrouter.ai/api/v1/auth/keys`. La clave va derecha al Llavero,
   como la de `--guardar-clave openrouter`: nunca se escribe ni se emite.

Así la primera vez basta con un clic para usar los modelos gratis de OpenRouter.
"""
from __future__ import annotations

import base64
import hashlib
import html
import http.server
import secrets
import socket
import subprocess
import threading
from urllib.parse import parse_qs, urlencode, urlsplit

AUTORIZAR = 'https://openrouter.ai/auth'
CANJEAR = 'https://openrouter.ai/api/v1/auth/keys'
RUTA = '/alfalfa/openrouter'
ESPERA = 300  # los códigos caducan a los 10 minutos; el usuario tiene 5 para autorizar
CONECTABLES = ('openrouter',)

_PAGINA = '''<!doctype html><meta charset="utf-8"><title>AlfalfaCode</title>
<body style="font:16px -apple-system,sans-serif;max-width:32em;margin:4em auto;padding:0 1em">
<h1 style="font-size:1.4em">{titulo}</h1><p>{texto}</p></body>'''


class ErrorAlConectar(RuntimeError):
    """El texto se enseña tal cual al usuario."""


def pkce():
    """(verifier, challenge S256) como pide RFC 7636."""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).rstrip(b'=').decode()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
    return verifier, challenge


class _Espera:
    """Lo que trae la vuelta de OpenRouter, para el hilo que espera."""

    def __init__(self, estado):
        self.estado = estado
        self.listo = threading.Event()
        self.codigo = None
        self.error = None


def _manejador(espera):
    class Manejador(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 (nombre de http.server)
            partes = urlsplit(self.path)
            if partes.path != RUTA:
                self.send_error(404)
                return
            datos = parse_qs(partes.query)
            codigo = (datos.get('code') or [''])[0]
            estado = (datos.get('state') or [''])[0]
            if not codigo:
                espera.error = 'OpenRouter no dio permiso (¿cancelaste la autorización?).'
                titulo, texto = 'No se conectó', 'No se autorizó la clave. Puedes cerrar esta pestaña.'
            elif not secrets.compare_digest(estado, espera.estado):
                # Otra pestaña o una página ajena: no se acepta, y se sigue esperando la buena.
                self._responder(400, 'No vale', 'Esta respuesta no es la que AlfalfaCode esperaba.')
                return
            else:
                espera.codigo = codigo
                titulo, texto = 'AlfalfaCode ya está conectado', ('OpenRouter autorizó la clave. '
                                                                  'Puedes cerrar esta pestaña y volver.')
            self._responder(200, titulo, texto)
            espera.listo.set()

        def _responder(self, codigo, titulo, texto):
            cuerpo = _PAGINA.format(titulo=html.escape(titulo), texto=html.escape(texto)).encode()
            self.send_response(codigo)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(cuerpo)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(cuerpo)

        def log_message(self, *args):
            pass  # nada de la petición (lleva el código) a la terminal

    return Manejador


class _Servidor(http.server.HTTPServer):
    allow_reuse_address = False


class _Servidor6(_Servidor):
    address_family = socket.AF_INET6


def _servidores(espera):
    """El de 127.0.0.1 en un puerto libre y, si se puede, el de ::1 en el mismo: el navegador
    puede resolver `localhost` a cualquiera de los dos."""
    manejador = _manejador(espera)
    principal = _Servidor(('127.0.0.1', 0), manejador)
    puerto = principal.server_address[1]
    lista = [principal]
    try:
        lista.append(_Servidor6(('::1', puerto), manejador))
    except OSError:
        pass
    return lista, puerto


def abrir_en_navegador(url):
    subprocess.run(['/usr/bin/open', url], check=False, capture_output=True, timeout=30)


def _canjear(codigo, verifier, post=None):
    if post is None:
        import httpx
        post = httpx.post
    try:
        r = post(CANJEAR, json={'code': codigo, 'code_verifier': verifier, 'code_challenge_method': 'S256'},
                 timeout=30)
    except Exception as exc:
        raise ErrorAlConectar('No se pudo hablar con OpenRouter: ' + (str(exc) or type(exc).__name__)) from None
    if r.status_code >= 400:
        raise ErrorAlConectar(f'OpenRouter no dio la clave (HTTP {r.status_code}). Vuelve a intentarlo.')
    try:
        clave = r.json().get('key')
    except Exception:
        clave = None
    if not isinstance(clave, str) or not clave.strip():
        raise ErrorAlConectar('OpenRouter respondió sin clave. Vuelve a intentarlo.')
    return clave.strip()


def obtener_clave(abrir=abrir_en_navegador, post=None, espera=ESPERA, al_abrir=None):
    """La clave que el usuario autoriza en OpenRouter. `al_abrir(url)` avisa de la URL por si
    el navegador no se abre solo."""
    estado = secrets.token_urlsafe(24)
    verifier, challenge = pkce()
    esperando = _Espera(estado)
    servidores, puerto = _servidores(esperando)
    hilos = [threading.Thread(target=s.serve_forever, kwargs={'poll_interval': 0.2}, daemon=True)
             for s in servidores]
    for hilo in hilos:
        hilo.start()
    try:
        url = AUTORIZAR + '?' + urlencode({
            'callback_url': f'http://localhost:{puerto}{RUTA}', 'code_challenge': challenge,
            'code_challenge_method': 'S256', 'state': estado, 'key_label': 'AlfalfaCode'})
        if al_abrir:
            al_abrir(url)
        abrir(url)
        if not esperando.listo.wait(espera):
            raise ErrorAlConectar('No llegó la autorización de OpenRouter a tiempo. Vuelve a intentarlo.')
        if esperando.error:
            raise ErrorAlConectar(esperando.error)
    finally:
        for s in servidores:
            s.shutdown()
            s.server_close()
    return _canjear(esperando.codigo, verifier, post)


def conectar(proveedor='openrouter', **opciones):
    """Conecta `proveedor` y lo deja como el de por defecto, con un modelo gratis elegido.
    Devuelve {proveedor, modelo} (sin la clave)."""
    if proveedor not in CONECTABLES:
        raise ErrorAlConectar('Sólo se puede conectar así OpenRouter; para ' + str(proveedor)
                              + ' guarda su clave con `alfalfa --guardar-clave ' + str(proveedor) + '`.')
    from . import configuracion, llavero
    from .compatible import Compatible
    clave = obtener_clave(**opciones)
    url = Compatible(proveedor).url()
    llavero.guardar(proveedor, clave)
    configuracion.cambiar(proveedor, 'url', url)
    configuracion.cambiar(proveedor, 'guardada', True)
    from .. import primera_vez
    modelo = primera_vez.recomendado(proveedor)
    if modelo:
        configuracion.cambiar(proveedor, 'modelo', modelo)
    configuracion.cambiar(None, 'por_defecto', proveedor)
    return {'proveedor': proveedor, 'modelo': proveedor + '/' + modelo if modelo else None}
