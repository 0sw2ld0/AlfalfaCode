"""Exporta por la tarjeta real y ejecuta el archivo generado sólo contra la página ficticia."""
import argparse
import asyncio
import io
import json
import os
import platform
import re
import shutil
import signal
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

from alfalfa.navegador import TOOLS, Navegador
from alfalfa.navegador_exportar import ExportacionNavegador
from alfalfa.navegador_sitios import Sitios, origen
from alfalfa.proveedores import llavero
from alfalfa.proveedores.nativo.herramientas import WorkspaceTools
from alfalfa.stdio import Transport

NOMBRE, BOTON, VALOR = 'Cantidad ficticia', 'Crear pedido ficticio', '2'
TEXTO = 'Pedido ficticio 1 · total 20 soles'
HTML = (f'''<!doctype html><meta charset="utf-8"><title>Pedido ficticio A11</title>
<style>body{{font:18px system-ui;margin:28px;color:#234}} input,button{{font:inherit;padding:8px}}</style>
<main><h1>Pedido ficticio A11</h1>
<input id="cantidad" aria-label="{NOMBRE}" type="text">
<button id="crear" type="button">{BOTON}</button>
<p id="estado">Sin pedido</p></main>
<script>
document.getElementById('crear').addEventListener('click', () => {{
  const cantidad = Number(document.getElementById('cantidad').value);
  document.getElementById('estado').textContent = `Pedido ficticio 1 · total ${{10 * cantidad}} soles`;
}});
</script>''').encode()


class Pagina(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != '/':
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(HTML)))
        self.end_headers()
        self.wfile.write(HTML)

    def log_message(self, *_):
        pass


def compilar(salida):
    demo = Path(__file__).resolve().parent
    paquete = demo.parents[2] / 'alfalfa-mac'
    bundle = salida / 'ExportarA11Demo.app/Contents'
    ejecutable = bundle / 'MacOS/ExportarA11Demo'
    ejecutable.parent.mkdir(parents=True)
    subprocess.run(['swift', 'build', '--target', 'AlfalfaKit', '--jobs', '4'], cwd=paquete, check=True)
    build = Path(subprocess.check_output(['swift', 'build', '--show-bin-path'], cwd=paquete, text=True).strip())
    fuentes = [demo / 'Demo.swift', paquete / 'Sources/AlfalfaCode/Theme.swift',
               paquete / 'Sources/AlfalfaCode/Session/BrowserExportCard.swift']
    objetos = sorted((build / 'AlfalfaKit.build').glob('*.swift.o'))
    if not objetos:
        raise RuntimeError('No se encontraron objetos del motor WebKit.')
    subprocess.run(['swiftc', '-parse-as-library', '-target', platform.machine() + '-apple-macos14.0',
                    '-I', str(build / 'Modules'), *map(str, fuentes), *map(str, objetos),
                    '-o', str(ejecutable)], check=True)
    (bundle / 'Info.plist').write_text('''<?xml version="1.0" encoding="UTF-8"?>
<plist version="1.0"><dict>
<key>CFBundleIdentifier</key><string>org.alfalfacode.demo.exportara11</string>
<key>CFBundleExecutable</key><string>ExportarA11Demo</string>
<key>CFBundleName</key><string>Exportar A11</string>
<key>CFBundlePackageType</key><string>APPL</string>
</dict></plist>''')
    return ejecutable


class Puente:
    def __init__(self, proceso):
        self.proceso = proceso
        self.id = 0

    async def enviar(self, objeto, tiempo=45):
        self.proceso.stdin.write((json.dumps(objeto, ensure_ascii=False) + '\n').encode())
        await self.proceso.stdin.drain()
        respuesta = await asyncio.wait_for(self.proceso.stdout.readline(), tiempo)
        if not respuesta:
            raise RuntimeError('La app de depuración cerró su salida.')
        return json.loads(respuesta)

    async def __call__(self, accion, campos, tiempo):
        self.id += 1
        return await self.enviar({'version': 2, 'tipo': 'navegador', 'id': str(self.id),
                                  'accion': accion, **campos}, tiempo + 2)


def ref(arbol, rol, nombre):
    refs = re.findall(r'^\s*- ' + rol + ' «' + re.escape(nombre) + r'» \[(e\d+)\]', arbol, re.MULTILINE)
    if len(refs) != 1:
        raise RuntimeError('La página de ensayo no tiene el elemento único esperado.')
    return refs[0]


async def observar_y_exportar(puente, salida, proyecto, url, informe):
    def prohibido():
        raise AssertionError('El ensayo no usa el Llavero.')

    llavero._almacen, llavero._leidos, llavero._security = {}, {}, prohibido
    hogar = salida / 'motor'
    hogar.mkdir(mode=0o700)
    Sitios(hogar, proyecto).guardar({'permitidos': [origen(url)], 'bloqueados': []})
    task = {'cwd': str(proyecto), 'modo': 'auto', 'status': 'running'}
    transport = Transport(io.StringIO())
    motor = SimpleNamespace(task=task, home=hogar, navegador=Navegador(puente), transport=transport,
                            read_only=False, cola_encargos=SimpleNamespace(activa=False))
    motor.exportacion_navegador = ExportacionNavegador(motor)
    motor.exportacion_navegador.empezar()
    transport.exportar_prueba = motor.exportacion_navegador.guardar

    async def permiso(solicitud):
        if solicitud.get('motivo') != 'navegador' or solicitud.get('argv') != ['navegador', 'abrir', url]:
            raise ValueError('El ensayo no aprobó ninguna otra acción.')

    runtime = SimpleNamespace(manager=motor, task=task, excluded_tools=(), tool_specs=TOOLS, state={},
                              directory=salida / 'sesion', approve=permiso)
    runtime.tools = WorkspaceTools(runtime)
    call = runtime.tools.call
    await call('browser_navigate', {'url': url})
    inicial = await call('browser_read', {})
    await call('browser_type', {'ref': ref(inicial['arbol'], 'textbox', NOMBRE), 'text': VALOR})
    relleno = await call('browser_read', {})
    await call('browser_click', {'ref': ref(relleno['arbol'], 'button', BOTON)})
    final = await call('browser_read', {})
    espera = await call('browser_wait', {'text': TEXTO, 'timeout_ms': 2000})
    if TEXTO not in final['arbol'] or espera.get('cumplida') is not True:
        raise RuntimeError('WebKit no confirmó el resultado ficticio.')
    informe['resultado_webkit'] = TEXTO
    task['status'] = 'completed'
    motor.exportacion_navegador.terminar()
    tarjeta = task['cierre']['prueba_navegador']
    if tarjeta.get('pasos') != 4:
        raise RuntimeError('No se ofreció el flujo comprobado: ' + tarjeta.get('motivo', 'pasos incompletos'))
    assert not (proyecto / 'tests').exists()
    assert (await puente.enviar({'tipo': 'oferta', 'cierre': task['cierre']}))['ok']
    assert not (proyecto / 'tests').exists()
    await asyncio.sleep(.25)
    informe['captura_tarjeta'] = (await puente.enviar({'tipo': 'captura_tarjeta'}))['ok']
    informe['captura_webkit'] = (await puente('captura', {'ruta': str(salida / 'webkit.png')}, 10))['ok']
    gesto = await puente.enviar({'tipo': 'gesto'})
    if not gesto.get('ok'):
        raise RuntimeError(gesto.get('error') or 'El botón de exportación no respondió.')
    transport.receive(gesto['comando'])
    await transport.exportacion
    informe['tarjeta'] = tarjeta
    informe['eventos_motor'] = [json.loads(linea) for linea in transport.output.getvalue().splitlines()]
    spec = proyecto / tarjeta['ruta']
    contenido = spec.read_text()
    for dato in (url, NOMBRE, BOTON, TEXTO, '127.0.0.1'):
        if dato in contenido:
            raise RuntimeError('La prueba exportó un dato literal de la fixture.')
    return spec


async def lanzar_app(binario, salida, proyecto, env, url, informe):
    with (salida / 'app.stderr').open('wb') as stderr:
        app = await asyncio.create_subprocess_exec(str(binario), cwd=salida, stderr=stderr,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, limit=1024 * 1024,
            env={**env, 'ALFALFA_DEMO_ROOT': str(salida), 'ALFALFA_DEMO_URL': url})
        try:
            return await observar_y_exportar(Puente(app), salida, proyecto, url, informe)
        finally:
            app.stdin.close()
            try:
                await asyncio.wait_for(app.wait(), 5)
            except TimeoutError:
                app.kill()
                await app.wait()
            informe['app_cerrada'] = app.returncode is not None


def ejecutar_playwright(args, proyecto, spec, salida, env, url, informe):
    (proyecto / 'playwright.config.cjs').write_text('''module.exports = {
  testDir: 'tests/e2e', workers: 1, retries: 0,
  use: { headless: true, screenshot: 'on', browserName: 'chromium',
    launchOptions: { executablePath: process.env.ALFALFA_DEMO_CHROME } },
};''')
    variables = {'ALFALFA_E2E_BASE_URL': url, 'ALFALFA_E2E_NOMBRE_2': NOMBRE, 'ALFALFA_E2E_VALOR_2': VALOR,
                 'ALFALFA_E2E_NOMBRE_3': BOTON, 'ALFALFA_E2E_TEXTO_4': TEXTO,
                 'ALFALFA_DEMO_CHROME': str(args.chrome.resolve(strict=True)), 'PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD': '1'}
    argv = [str(args.npx), '--no-install', 'playwright', 'test', str(spec.relative_to(proyecto))]
    with (salida / 'playwright.log').open('wb') as log:
        proc = subprocess.Popen(argv, cwd=proyecto, env={**env, **variables}, stdout=log,
                                stderr=subprocess.STDOUT, start_new_session=True)
        try:
            codigo = proc.wait(60)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)  # Sólo el grupo recién lanzado para este ensayo.
            proc.wait()
            raise RuntimeError('Playwright agotó su plazo.') from None
    informe['playwright'] = {'argv': argv[1:], 'codigo': codigo, 'resultado_esperado': TEXTO}
    if codigo:
        raise RuntimeError('El spec no pasó; consulta playwright.log.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--salida', required=True, type=Path, help='Carpeta de evidencia nueva')
    parser.add_argument('--deps', required=True, type=Path, help='Carpeta de prueba con node_modules de Playwright')
    parser.add_argument('--chrome', required=True, type=Path, help='Binario Chromium/Chrome, sin attach')
    parser.add_argument('--npx', required=True, type=Path, help='npx del Node de ensayo')
    args = parser.parse_args()
    salida = args.salida.resolve()
    salida.mkdir(mode=0o700)
    proyecto, home, temp = (salida / nombre for nombre in ('proyecto', 'home', 'tmp'))
    proyecto.mkdir(mode=0o700)
    (home / 'Library/Caches').mkdir(mode=0o700, parents=True)
    (home / 'Library/Application Support').mkdir(mode=0o700)
    temp.mkdir(mode=0o700)
    deps = args.deps.resolve(strict=True) / 'node_modules'
    if not (deps / '.bin/playwright').is_file():
        raise RuntimeError('Falta Playwright en las dependencias de prueba explícitas.')
    shutil.copytree(deps, proyecto / 'node_modules', symlinks=True)
    env = {'HOME': str(home), 'CFFIXED_USER_HOME': str(home), 'TMPDIR': str(temp),
           'PATH': str(args.npx.parent) + ':/usr/bin:/bin', 'ALFALFA_HOME': str(home / '.alfalfa')}
    informe = {}
    try:
        binario = compilar(salida)
        with ThreadingHTTPServer(('127.0.0.1', 0), Pagina) as server:
            assert server.server_port > 1024 and server.server_port != 8765
            url = f'http://127.0.0.1:{server.server_port}/'
            informe['fixture'] = url
            hilo = threading.Thread(target=server.serve_forever, daemon=True)
            hilo.start()
            try:
                spec = asyncio.run(lanzar_app(binario, salida, proyecto, env, url, informe))
                ejecutar_playwright(args, proyecto, spec, salida, env, url, informe)
            finally:
                server.shutdown()
                hilo.join()
        informe['exito'] = True
    except Exception as exc:
        informe['error'] = type(exc).__name__ + ': ' + str(exc)
    finally:
        (salida / 'resultado.json').write_text(json.dumps(informe, ensure_ascii=False, indent=2) + '\n')
    return 0 if informe.get('exito') else 1


if __name__ == '__main__':
    raise SystemExit(main())
