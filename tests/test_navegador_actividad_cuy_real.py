"""Opt-in: CB-5 por Node y Chromium propio, sólo páginas y capturas ficticias."""
import asyncio
import json
import os
import shutil
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

from alfalfa import navegador_actividad as actividad
from alfalfa.navegador_cuybrowser import NavegadorCuyBrowser

pytestmark = pytest.mark.skipif(os.environ.get('PRUEBA_CUYBROWSER_ACTIVIDAD_REAL') != '1',
                                reason='Requiere el candidato CB-5 en PATH y Chromium de ensayo; opt-in.')

HTML = b'''<!doctype html><meta charset="utf-8"><title>Actividad A10: pagina ficticia</title>
<style>body{margin:52px;background:#e6efeb;color:#173a35;font:20px system-ui}
main{border:1px solid #73978e;background:white;max-width:620px;padding:30px;border-radius:14px}
h1{color:#185e50}p{line-height:1.5}</style>
<main><h1>Actividad del navegador</h1><p>Pagina ficticia de CuyBrowser para verificar una captura asociada.</p>
<p>Sin cuentas, formularios sensibles ni datos personales.</p></main>'''


def _privada(path):
    path.mkdir(mode=0o700)
    return path


def _copiar_demo(target, record, captures):
    home = _privada(target / 'alfalfa')
    folder = home
    for part in ('sesiones', 'ensayo-a10', 'native', 'ensayo-a10'):
        folder = _privada(folder / part)
    path = folder / actividad.ARCHIVO
    shutil.copyfile(record, path)
    path.chmod(0o600)
    images = _privada(folder / 'capturas')
    for capture in captures:
        shutil.copyfile(capture, images / capture.name)


def test_actividad_de_chromium_propio_con_cb5(monkeypatch):
    class Page(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(HTML)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Page)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory(prefix='a10-cb5-', dir='/private/tmp') as temp:
            root = Path(temp)
            home = _privada(root / 'alf')
            project = _privada(root / 'proyecto')
            own_home = _privada(root / 'home')
            monkeypatch.setenv('HOME', str(own_home))
            directory = home
            for part in ('sesiones', 'ensayo-a10', 'native', 'ensayo-a10'):
                directory = _privada(directory / part)
            assert server.server_port != 8765

            async def approve(request):
                # Sólo el inicio, el sitio y la red local del servidor ficticio.
                assert request.get('motivo') == 'navegador'
                return 'una_vez' if request.get('sitio') else None

            runtime = SimpleNamespace(tools=SimpleNamespace(root=project), manager=SimpleNamespace(home=home),
                                      directory=directory, task={'id': 'ensayo-a10', 'modo': 'auto'},
                                      approve=approve, state={})
            browser = NavegadorCuyBrowser('ensayo-a10')

            async def exercise():
                url = f'http://127.0.0.1:{server.server_port}/ensayo?token=CANARIO_PRIVADO#fragmento'
                try:
                    await actividad.ejecutar(runtime, browser, 'browser_navigate', {'url': url})
                    assert browser.http.capabilities.get('auditRecent') is True
                    read = await actividad.ejecutar(runtime, browser, 'browser_read', {})
                    assert 'Actividad del navegador' in read['arbol']
                    await actividad.ejecutar(runtime, browser, 'browser_screenshot', {})
                finally:
                    browser.close()

            asyncio.run(exercise())
            path = directory / actividad.ARCHIVO
            raw = path.read_text()
            events = json.loads(raw)['eventos']
            assert [row['accion'] for row in events] == ['browser_navigate', 'browser_read', 'browser_screenshot']
            assert all(row['cuy']['estado'] == 'disponible' for row in events)
            assert [row['cuy']['acciones'][-1]['tool'] for row in events] == ['navigate', 'snapshot', 'screenshot']
            assert len({a['id'].partition(':')[0] for e in events for a in e['cuy']['acciones']}) == 1
            assert {p['tipo'] for p in events[0]['permisos']} == {'arranque_cuy', 'sitio', 'red_local'}
            assert 'CANARIO_PRIVADO' not in raw and 'fragmento' not in raw and '/ensayo?' not in raw
            assert 'captura' not in events[0] and 'captura' not in events[1]
            png = directory / 'capturas' / events[2]['captura']
            assert png.stat().st_size > 1000 and png.read_bytes().startswith(b'\x89PNG\r\n\x1a\n')
            if target := os.environ.get('PRUEBA_A10_DESTINO'):
                _copiar_demo(Path(target), path, [png])
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
