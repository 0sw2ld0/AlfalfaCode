"""Opt-in: Chromium y HTTP propios. Nunca captura Chromium ni decide un diálogo sensible."""
import asyncio
import os
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

from alfalfa.navegador_cuybrowser import NavegadorCuyBrowser

pytestmark = pytest.mark.skipif(os.environ.get('PRUEBA_CUYBROWSER_DE_VERDAD') != '1',
                                reason='Requiere CuyBrowser compatible en PATH y Chromium propio; opt-in explícito.')

HTML = b'''<!doctype html><meta charset="utf-8"><title>Ensayo A7</title>
<style>body{min-height:2200px}#hover:hover{background:rgb(1,120,30)}</style>
<label>Escribir demo<input id="campo" aria-label="Escribir demo"></label>
<input aria-label="Segundo campo"><button id="hover">Hover demo</button>
<p id="estado">Esperando hover</p><p id="persistencia"></p>
<form method="post"><button>Enviar formulario de prueba</button></form>
<script>
document.querySelector('#hover').onmouseenter=()=>document.querySelector('#estado').textContent='Hover completado';
document.querySelector('#persistencia').textContent=localStorage.marca?'Perfil conservado':'Perfil nuevo';
localStorage.marca='datos de ensayo propios';console.error('Error de ensayo A7');
</script>'''


def test_http_real_y_reinicio_cerrado(monkeypatch):
    requests, posts = [], []

    class Page(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(HTML)

        def do_POST(self):
            posts.append(self.path)
            self.send_response(200)
            self.end_headers()

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Page)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        # Ruta corta para el socket Unix; no usa HOME, perfiles ni tokens reales.
        with tempfile.TemporaryDirectory(prefix='alf-a7-', dir='/private/tmp') as directory:
            home, root = Path(directory) / 'alf', Path(directory) / 'proyecto'
            home.mkdir(mode=0o700)
            root.mkdir()
            own_home = Path(directory) / 'home'
            own_home.mkdir(mode=0o700)
            monkeypatch.setenv('HOME', str(own_home))
            approvals = []

            async def approve(request):
                approvals.append(request)  # Sólo inicio del motor y nuestro servidor de prueba.

            runtime = SimpleNamespace(tools=SimpleNamespace(root=root), manager=SimpleNamespace(home=home),
                                      directory=Path(directory) / 'sesion', task={'modo': 'auto'},
                                      approve=approve, state={})
            url = f'http://127.0.0.1:{server.server_port}/'

            async def exercise():
                browser = NavegadorCuyBrowser('ensayo-a7')
                try:
                    await browser.llamar(runtime, 'browser_navigate', {'url': url})
                    tree = await browser.llamar(runtime, 'browser_read', {})
                    assert 'Ensayo A7' in str(tree)
                    await browser.llamar(runtime, 'browser_type', {'target': 'Escribir demo', 'text': 'hola'})
                    await browser.llamar(runtime, 'browser_key', {'key': 'Tab'})
                    await browser.llamar(runtime, 'browser_hover', {'target': 'Hover demo'})
                    await browser.llamar(runtime, 'browser_wait', {'text': 'Hover completado', 'timeout_ms': 3000})
                    await browser.llamar(runtime, 'browser_viewport', {'preset': 'mobile'})
                    assert (await browser.llamar(runtime, 'browser_scroll', {}))['y'] > 0
                    console = await browser.llamar(runtime, 'browser_console', {})
                    assert 'Error de ensayo A7' in str(console)
                    await browser.llamar(runtime, 'browser_network', {})
                    # No abre ni aprueba un diálogo: se prueba que el formulario queda retenido sin POST.
                    form = await browser.http.call('find', {'query': 'Enviar formulario de prueba'})
                    response = await browser.http.request('POST', '/v1/tools/click',
                                                          {'ref': form['matches'][0]['ref']})
                    assert response['error']['code'] == 'CONFIRMATION_REQUIRED'
                    assert posts == []
                    await browser.llamar(runtime, 'browser_navigate', {'url': url})
                    assert 'Perfil conservado' in str(await browser.llamar(runtime, 'browser_read', {'mode': 'text'}))
                    # Mismo adaptador de leases que A8, sin convertir la visita en permiso de sesión.
                    class Visit:
                        value = {'id': 'c' * 32, 'origen': url.rstrip('/')}

                        def consumir_visita(self, ident):
                            if self.value is not None and self.value['id'] == ident:
                                self.value = None

                    visit = Visit()
                    browser.sitios = lambda _: visit
                    browser.politica_sitios = lambda _: {'permitidos': [], 'bloqueados': [], 'estricto': False,
                                                        'visita': visit.value}
                    await browser.llamar(runtime, 'browser_read', {})
                    status = await browser.http.call('status')
                    original_tab = status['active_tab']
                    await browser.http.call('tab_new')
                    await browser.http.call('tab_close', {'id': original_tab})
                    await browser.llamar(runtime, 'browser_read', {})
                    assert visit.value is None
                    assert 'c' * 32 in (await browser.http.call('status'))['consumed_visits']
                    previous_contacts = len(requests)
                    with pytest.raises(ValueError, match='BLOCKED_BY_POLICY'):
                        await browser.http.call('navigate', {'url': url})
                    assert len(requests) == previous_contacts
                finally:
                    browser.close()
                previous = len(requests)
                second = NavegadorCuyBrowser('ensayo-a7')
                try:
                    # Restaurar el mismo perfil empieza con origins/local_network cerrados.
                    await second.llamar(runtime, 'browser_read', {})
                    await asyncio.sleep(0.4)
                    assert len(requests) == previous
                    await second.llamar(runtime, 'browser_navigate', {'url': url})
                    text = await second.llamar(runtime, 'browser_read', {'mode': 'text'})
                    assert 'Perfil conservado' in str(text)
                finally:
                    second.close()

            asyncio.run(exercise())
            assert approvals and all(r['motivo'] == 'navegador' for r in approvals)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
