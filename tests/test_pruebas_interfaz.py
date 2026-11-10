"""Antes/después con proyectos propios y PNG sintéticos: sin apps, cuentas ni modelos reales."""
import asyncio
import io
import json
import stat
import struct
import zlib
from pathlib import Path

import pytest

from alfalfa import deshacer, sesiones
from alfalfa.navegador import Navegador, _podar_capturas
from alfalfa.proveedores.nativo.runtime import NativeRuntime
from alfalfa.pruebas_interfaz import leer_png
from alfalfa.stdio import Transport
from alfalfa.terminal import TerminalManager


def png(color, ancho=16, alto=16):
    def bloque(tipo, datos):
        return struct.pack('>I', len(datos)) + tipo + datos + struct.pack('>I', zlib.crc32(tipo + datos))
    cabecera = struct.pack('>IIBBBBB', ancho, alto, 8, 2, 0, 0, 0)
    filas = (b'\0' + bytes(color) * ancho) * alto
    return (b'\x89PNG\r\n\x1a\n' + bloque(b'IHDR', cabecera)
            + bloque(b'IDAT', zlib.compress(filas)) + bloque(b'IEND', b''))


ROJO, VERDE = png((220, 30, 30)), png((30, 180, 90))


@pytest.fixture
def demo(tmp_path, monkeypatch):
    root = tmp_path / 'proyecto'
    root.mkdir()
    monkeypatch.chdir(root)
    (root / 'index.html').write_text('<button style="color:red">Guardar</button>')
    salida = io.StringIO()
    m = TerminalManager('Cambia el botón.', home=tmp_path / 'hogar', model='autor', transport=Transport(salida),
                        navegador=True)
    rt = m.runtime()
    turno = sesiones.abrir_turno(m.task, 'Cambia el botón.', len(rt.state['messages']))
    deshacer.al_empezar_turno(m.home, m.task, turno['id'], rt.state)
    m.pruebas_interfaz.empezar(rt, turno['id'])
    control = {'png': ROJO, 'url': 'https://demo.test/', 'peticiones': []}

    async def app(accion, campos, tiempo):
        control['peticiones'].append((accion, campos))
        if accion == 'captura':
            Path(campos['ruta']).write_bytes(control['png'])
        return {'ok': True, 'resultado': {'url': control['url'], 'arbol': '- button «Guardar» [e1]'}}

    m.navegador = Navegador(app)
    m.navegador.url = control['url']
    try:
        yield m, rt, root, control
    finally:
        m.close()


def editar(root):
    (root / 'index.html').write_text('<button style="color:green">Guardar</button>')


async def cerrar(m, rt):
    m.task.update(status='completed', cierre={'resumen': 'Botón actualizado.', 'sugerencia': 'Revisa el botón.'})
    turno = m.task['turnos'][-1]
    sesiones.cerrar_turno(m.task, turno['id'], 'completed', len(rt.state['messages']), cierre=m.task['cierre'])
    deshacer.al_terminar_turno(m.home, m.task, turno['id'], rt.state)
    await m.pruebas_interfaz.terminar()
    rt.save()
    return m.task['cierre'].get('prueba_interfaz')


async def test_automatica_por_herramienta_y_cierre_persistido_sin_llamar_vision(demo, monkeypatch):
    m, rt, root, app = demo

    async def vision_prohibida(*args, **kw):
        pytest.fail('Archivar las capturas no puede llamar a un proveedor de visión.')

    monkeypatch.setattr(rt, 'vision', vision_prohibida)
    await rt.tools.call('browser_read', {})
    assert m.pruebas_interfaz.web['png'] == ROJO
    editar(root)
    app['png'] = VERDE
    prueba = await cerrar(m, rt)
    assert prueba['estado'] == 'completa'
    par, = prueba['comparaciones']
    assert par['medio'] == 'web' and par['archivos'] == ['index.html']
    assert Path(par['antes']).read_bytes() == ROJO and Path(par['despues']).read_bytes() == VERDE
    assert Path(par['antes']).parent == rt.directory / 'capturas'
    assert stat.S_IMODE(Path(par['antes']).stat().st_mode) == 0o600
    assert stat.S_IMODE(Path(par['antes']).parent.stat().st_mode) == 0o700
    assert [a for a, _ in app['peticiones']] == ['leer', 'captura', 'captura']
    assert all('question' not in campos for _, campos in app['peticiones'])
    guardado = json.loads((m.directory / 'task.json').read_text())
    assert guardado['cierre']['prueba_interfaz'] == prueba
    assert guardado['turnos'][-1]['cierre']['prueba_interfaz'] == prueba
    assert guardado['cierre']['sugerencia'] == 'Revisa el botón.'
    assert m.pruebas_interfaz.web is None


async def test_la_captura_explicita_no_se_duplica_y_sobrevive_a_la_poda(demo):
    m, rt, root, app = demo
    await rt.tools.call('browser_screenshot', {})
    carpeta = rt.directory / 'capturas'
    for i in range(55):
        (carpeta / f'navegador-prueba-{i}.png').write_bytes(VERDE)
    _podar_capturas(carpeta)
    editar(root)
    app['png'] = VERDE
    prueba = await cerrar(m, rt)
    assert Path(prueba['comparaciones'][0]['antes']).read_bytes() == ROJO
    assert len(list(carpeta.glob('navegador-*.png'))) <= 50
    assert len(app['peticiones']) == 2


@pytest.mark.parametrize('caso', ['sin_navegador', 'sin_cambios', 'solo_documentacion', 'cambio_revertido'])
async def test_no_adjunta_si_no_corresponde(demo, caso):
    m, rt, root, app = demo
    if caso != 'sin_navegador':
        await rt.tools.call('browser_read', {})
    if caso == 'solo_documentacion':
        (root / 'README.md').write_text('Documentación')
    elif caso == 'sin_navegador':
        editar(root)
    elif caso == 'cambio_revertido':
        original = (root / 'index.html').read_text()
        editar(root)
        (root / 'index.html').write_text(original)
    assert await cerrar(m, rt) is None
    assert len(app['peticiones']) <= 2  # nunca captura al cierre sin cambios de interfaz


async def test_no_usa_como_antes_una_captura_posterior_a_la_edicion(demo):
    m, rt, root, app = demo
    editar(root)
    await rt.tools.call('browser_read', {})
    prueba = await cerrar(m, rt)
    assert prueba['estado'] == 'incompleta' and not prueba['comparaciones']
    assert 'anterior' in prueba['notas'][0]
    assert [a for a, _ in app['peticiones']] == ['leer']


@pytest.mark.parametrize('caso', ['otra_url', 'otro_tamano', 'error', 'relevo', 'cerrado', 'timeout'])
async def test_no_inventa_parejas_si_falla_la_captura_final(demo, monkeypatch, caso):
    from alfalfa import pruebas_interfaz
    m, rt, root, app = demo
    await rt.tools.call('browser_read', {})
    editar(root)
    if caso == 'otra_url':
        m.navegador.url = app['url'] = 'https://otra.test/'
    elif caso == 'otro_tamano':
        app['png'] = png((0, 0, 0), 32, 16)
    elif caso == 'relevo':
        m.navegador.relevo_pendiente = True
    elif caso == 'cerrado':
        m.transport.stopped = True
    else:
        async def fallo(*args):
            if caso == 'timeout':
                await asyncio.sleep(1)
            raise ValueError('No se puede capturar')
        monkeypatch.setattr(m.navegador, 'llamar', fallo)
        monkeypatch.setattr(pruebas_interfaz, 'TIEMPO', .01)
    prueba = await cerrar(m, rt)
    assert prueba['estado'] == 'incompleta' and not prueba['comparaciones'] and prueba['notas']


async def test_no_arrastra_imagenes_a_otro_turno_ni_toma_las_de_subagentes(demo):
    from types import SimpleNamespace
    m, rt, root, app = demo
    await rt.tools.call('browser_read', {})
    editar(root)
    await cerrar(m, rt)
    turno = sesiones.abrir_turno(m.task, 'Otro turno.', len(rt.state['messages']))
    deshacer.al_empezar_turno(m.home, m.task, turno['id'], rt.state)
    m.pruebas_interfaz.empezar(rt, turno['id'])
    hijo = SimpleNamespace(task={'id': 'hijo'})
    await m.pruebas_interfaz.navegador(hijo, 'browser_read', {})
    assert not m.pruebas_interfaz.usados and m.pruebas_interfaz.web is None
    (root / 'index.html').write_text('Otra interfaz')
    assert await cerrar(m, rt) is None


async def app_captura(m, rt, contenido, pid=42, ventana=0, ejecutable=None):
    carpeta = rt.directory / 'capturas'
    carpeta.mkdir(exist_ok=True)
    ruta = carpeta / 'demo-mac.png'
    ruta.write_bytes(contenido)
    app = {'bundle': 'test.demo', 'pid': pid, 'nombre': 'Demo propia', 'ejecutable': ejecutable}
    await m.pruebas_interfaz.app(rt, app, {'captura': str(ruta), 'ventana': ventana})


@pytest.mark.parametrize('caso', ['completa', 'otra_ventana', 'otro_proceso', 'edicion_posterior', 'sin_final'])
async def test_mac_solo_reutiliza_capturas_autorizadas_de_la_misma_ventana(demo, caso):
    m, rt, root, app = demo
    await app_captura(m, rt, ROJO)
    editar(root)
    if caso != 'sin_final':
        await app_captura(m, rt, VERDE, pid=43 if caso == 'otro_proceso' else 42,
                         ventana=1 if caso == 'otra_ventana' else 0)
    if caso == 'edicion_posterior':
        (root / 'index.html').write_text('Último cambio aún sin mirar')
    prueba = await cerrar(m, rt)
    assert not app['peticiones']  # no se abren apps ni se captura ninguna ventana al terminar
    assert prueba['estado'] == ('completa' if caso == 'completa' else 'incompleta')
    if caso == 'completa':
        par, = prueba['comparaciones']
        assert par['medio'] == 'mac' and Path(par['despues']).read_bytes() == VERDE


async def test_mac_reconoce_el_mismo_ejecutable_tras_reconstruir_y_reabrir(demo):
    m, rt, root, _ = demo
    ejecutable = str(root / '.build' / 'MiAppDemo')
    await app_captura(m, rt, ROJO, ejecutable=ejecutable)
    editar(root)
    await app_captura(m, rt, VERDE, pid=43, ejecutable=ejecutable)
    prueba = await cerrar(m, rt)
    assert prueba['estado'] == 'completa'


@pytest.mark.parametrize('caso', ['fuera', 'traversal', 'enlace', 'enlace_duro', 'directorio_enlace', 'no_png',
                                'grande', 'directorio', 'truncado', 'crc'])
def test_imagenes_acotadas_solo_dentro_de_la_sesion(tmp_path, caso):
    raiz = tmp_path / 'sesion'
    raiz.mkdir()
    fuera = tmp_path / 'ajena.png'
    fuera.write_bytes(ROJO)
    ruta = raiz / 'captura.png'
    ruta.write_bytes(ROJO)
    if caso == 'fuera':
        ruta = fuera
    elif caso == 'traversal':
        ruta = raiz / '..' / 'ajena.png'
    elif caso == 'enlace':
        ruta.unlink()
        ruta.symlink_to(fuera)
    elif caso == 'enlace_duro':
        ruta.unlink()
        ruta.hardlink_to(fuera)
    elif caso == 'directorio_enlace':
        (raiz / 'enlace').symlink_to(tmp_path, target_is_directory=True)
        ruta = raiz / 'enlace' / 'ajena.png'
    elif caso == 'no_png':
        ruta.write_bytes(b'x' * 30)
    elif caso == 'truncado':
        ruta.write_bytes(ROJO[:-12])
    elif caso == 'crc':
        ruta.write_bytes(ROJO[:30] + b'x' + ROJO[31:])
    elif caso == 'grande':
        with ruta.open('wb') as archivo:
            archivo.truncate(9 * 1024 * 1024)
    elif caso == 'directorio':
        ruta = raiz
    with pytest.raises((ValueError, OSError)):
        leer_png(ruta, raiz)


async def test_la_captura_automatica_no_puede_pedir_permisos_nuevos(demo, monkeypatch):
    m, rt, root, _ = demo
    llamar = m.navegador.llamar

    async def captura_requiere_permiso(runtime, name, args):
        if name == 'browser_screenshot':
            await runtime.approve({'proposito': 'Un permiso nuevo'})
        return await llamar(runtime, name, args)

    async def no_preguntar(*args):
        pytest.fail('El cierre no debe abrir diálogos nuevos.')

    monkeypatch.setattr(rt, 'approve', no_preguntar)
    monkeypatch.setattr(m.navegador, 'llamar', captura_requiere_permiso)
    await rt.tools.call('browser_read', {})
    editar(root)
    prueba = await cerrar(m, rt)
    assert prueba['estado'] == 'incompleta' and not prueba['comparaciones']


async def test_sin_instantanea_final_no_se_adjudica_una_imagen_a_otro_codigo(demo):
    m, rt, root, _ = demo
    await rt.tools.call('browser_read', {})
    editar(root)
    m.task['status'] = 'completed'
    await m.pruebas_interfaz.terminar()
    assert 'prueba_interfaz' not in m.task.get('cierre', {})


@pytest.mark.parametrize('cerrado', [False, True])
async def test_mismo_contrato_con_cuybrowser_simulado_sin_arrancar_chromium(demo, monkeypatch, cerrado):
    import base64

    import httpx

    from alfalfa.navegador_cuybrowser import NavegadorCuyBrowser
    from tests.test_navegador_cuybrowser import Api, Process
    m, rt, root, control = demo
    api, cliente = Api(), httpx.AsyncClient

    def responder(request):
        respuesta = api(request)
        if request.url.path.endswith('/screenshot'):
            datos = respuesta.json()
            datos['data']['image']['base64'] = base64.b64encode(control['png']).decode()
            return httpx.Response(200, json=datos)
        return respuesta

    monkeypatch.setattr(httpx, 'AsyncClient',
                        lambda **kw: cliente(transport=httpx.MockTransport(responder), **kw))
    m.navegador = NavegadorCuyBrowser(process=Process(m.home / 'cuy-propio'))
    m.navegador.sitios(rt).guardar({'permitidos': ['https://example.com:443'], 'bloqueados': []})
    await rt.tools.call('browser_read', {})
    editar(root)
    control['png'] = VERDE
    if cerrado:
        m.navegador.close()
    arranques = m.navegador.process.started
    prueba = await cerrar(m, rt)
    assert m.navegador.process.started == arranques + (0 if cerrado else 1)
    if cerrado:
        assert prueba['estado'] == 'incompleta' and not prueba['comparaciones']
        return
    assert prueba['estado'] == 'completa'
    par, = prueba['comparaciones']
    assert Path(par['antes']).read_bytes() == ROJO and Path(par['despues']).read_bytes() == VERDE
    assert [tool for tool, _ in api.calls].count('screenshot') == 2


async def test_hook_de_app_state_con_ayudante_simulado_y_permisos_habituales(demo, monkeypatch):
    from alfalfa import apps
    from alfalfa.permisos import sesion
    from tests.test_control_de_apps import CONTADOR, Falso
    m, rt, root, control = demo
    pedidos = []

    class Ayudante(Falso):
        async def llamar(self, orden, **argumentos):
            resultado = await super().llamar(orden, **argumentos)
            if orden == 'estado' and argumentos.get('captura'):
                Path(argumentos['captura']).write_bytes(control['png'])
                resultado['captura'] = str(argumentos['captura'])
            return resultado

    async def aprobar(solicitud):
        pedidos.append(solicitud)
        return sesion.SESION

    async def a_puntos(*args):
        pass  # El PNG sintético ya está a la escala deseada.

    monkeypatch.setattr(rt, 'approve', aprobar)
    monkeypatch.setattr(apps, '_a_puntos', a_puntos)
    herramienta = apps.ControlDeApps(rt, ayudante=Ayudante())
    await herramienta.estado({'app': CONTADOR['nombre']})
    editar(root)
    control['png'] = VERDE
    await herramienta.estado({'app': CONTADOR['nombre']})
    prueba = await cerrar(m, rt)
    assert pedidos and prueba['estado'] == 'completa'
    assert prueba['comparaciones'][0]['medio'] == 'mac'


@pytest.mark.parametrize('estado', ['interrupted', 'failed'])
async def test_turno_interrumpido_o_fallido_no_captura_ni_retiene_imagenes(demo, estado):
    m, rt, root, app = demo
    await rt.tools.call('browser_read', {})
    editar(root)
    deshacer.al_terminar_turno(m.home, m.task, m.task['turnos'][-1]['id'], rt.state)
    m.task['status'] = estado
    await m.pruebas_interfaz.terminar()
    assert 'prueba_interfaz' not in m.task.get('cierre', {})
    assert [a for a, _ in app['peticiones']] == ['leer', 'captura']
    assert m.pruebas_interfaz.web is None and m.pruebas_interfaz.ident is None


async def test_ciclo_real_del_turno_no_pierde_el_cierre_ni_la_revision(tmp_path, monkeypatch):
    root = tmp_path / 'demo'
    root.mkdir()
    monkeypatch.chdir(root)
    (root / 'index.html').write_text('Antes')
    salida = io.StringIO()
    m = TerminalManager('Demo', home=tmp_path / 'hogar', model='autor', transport=Transport(salida), navegador=True)
    control = {'png': ROJO}

    async def pedir(accion, campos, tiempo):
        if accion == 'captura':
            Path(campos['ruta']).write_bytes(control['png'])
        return {'ok': True, 'resultado': {'url': 'https://demo.test/', 'arbol': 'Demo'}}

    async def run(rt, prompt):
        await rt.tools.call('browser_read', {})
        editar(root)
        control['png'] = VERDE
        await rt.tools.call('wrap_up', {'recap': 'Demo terminada.', 'suggestion': 'Mira el botón.'})
        rt.task['status'] = 'completed'

    m.navegador = Navegador(pedir)
    monkeypatch.setattr(NativeRuntime, 'run', run)
    try:
        assert await m.run('Cambia el botón.') == 0
        cierre = m.task['turnos'][-1]['cierre']
        assert cierre['prueba_interfaz']['estado'] == 'completa'
        assert cierre['revision_codigo']['estado'] == 'disponible'
        assert cierre['sugerencia'] == 'Mira el botón.'
        assert json.loads((m.directory / 'task.json').read_text())['turnos'][-1]['cierre'] == cierre
    finally:
        m.close()
