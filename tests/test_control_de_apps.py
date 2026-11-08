"""Control de apps del Mac (app_list, app_state, app_action), como Computer Use de Codex."""
import asyncio
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from alfalfa import apps
from alfalfa.permisos import sesion

CONTADOR = {'pid': 4242, 'nombre': 'Contador Demo', 'bundle': 'com.alfalfacode.demo.contador',
            'ventanas': [{'ventana': 0, 'titulo': 'Factura de Ana', 'ancho': 420, 'alto': 252}]}
TERMINAL = {'pid': 7, 'nombre': 'Terminal', 'bundle': 'com.apple.Terminal', 'ventanas': []}
SAFARI = {'pid': 8, 'nombre': 'Safari', 'bundle': 'com.apple.Safari', 'ventanas': []}
LLAVES = {'pid': 9, 'nombre': '1Password', 'bundle': 'com.1password.1password', 'ventanas': []}
ESTADO = {'ventana': 0, 'titulo': 'Contador Demo', 'ancho': 420, 'alto': 252,
          'arbol': '[0] Window\n  [1] Button titulo="Sumar uno"\n  [2] StaticText valor="Pulsado 0 veces"',
          'elementos': [{'i': 0, 'ruta': '', 'rol': 'AXWindow'},
                        {'i': 1, 'ruta': '1', 'rol': 'AXButton', 'titulo': 'Sumar uno', 'acciones': ['AXPress']},
                        {'i': 2, 'ruta': '0', 'rol': 'AXStaticText', 'valor': 'Pulsado 0 veces'},
                        {'i': 3, 'ruta': '2', 'rol': 'AXStaticText', 'valor': 'Sumar uno o dos'}]}


class Falso:
    def __init__(self):
        self.llamadas = []
        self.cuenta = 0

    async def llamar(self, orden, **argumentos):
        self.llamadas.append((orden, argumentos))
        if orden == 'lista':
            return {'apps': [CONTADOR, TERMINAL, SAFARI, LLAVES], 'accesibilidad': True}
        if orden == 'estado':
            estado = dict(ESTADO)
            estado['arbol'] = estado['arbol'].replace('Pulsado 0', 'Pulsado ' + str(self.cuenta))
            return estado
        if argumentos.get('tipo') == 'clic':
            self.cuenta += 1
        if argumentos.get('tipo') == 'tecla' and argumentos.get('tecla') == 'mal':
            raise ValueError('Tecla desconocida: mal.')
        return {'hecho': True, 'como': 'AXPress'}


class Manager:
    home = None

    def __init__(self):
        self.eventos = []

    def event(self, task, rol, texto):
        self.eventos.append(texto)


class Runtime:
    def __init__(self, respuestas):
        self.task = {}
        self.manager = Manager()
        self.respuestas = list(respuestas)
        self.pedidos = []
        self.directory = None

    async def approve(self, solicitud):
        self.pedidos.append(solicitud)
        respuesta = self.respuestas.pop(0)
        if respuesta == 'no':
            raise ValueError('rechazado')
        return respuesta


def control(*respuestas):
    rt = Runtime(respuestas)
    return apps.ControlDeApps(rt, ayudante=Falso()), rt


def correr(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_niveles_de_acceso_como_los_de_claude():
    assert apps.nivel(TERMINAL) == (apps.CLIC, 'una terminal')
    assert apps.nivel({'bundle': 'com.googlecode.iterm2'}) == (apps.CLIC, 'una terminal')
    assert apps.nivel({'bundle': 'com.jetbrains.pycharm'}) == (apps.CLIC, 'un editor con terminal')
    assert apps.nivel(SAFARI) == (apps.LECTURA, 'un navegador')
    assert apps.nivel(LLAVES) == (apps.BLOQUEADA, 'un gestor de contraseñas')
    assert apps.nivel({'bundle': 'com.apple.systempreferences'}) == (apps.BLOQUEADA, 'Ajustes del Sistema')
    assert apps.bloqueo({'nombre': 'AlfalfaCode', 'ejecutable': '/x/.build/debug/AlfalfaCode'}) == 'AlfalfaCode'
    assert apps.nivel(CONTADOR) == (apps.COMPLETA, None)
    c, rt = control('sesion', 'sesion')
    # Una terminal nunca recibe texto, teclas ni clic derecho: ni siquiera se pregunta.
    for accion in ({'action': 'type', 'text': 'rm -rf ~'}, {'action': 'key', 'key': 'return'},
                   {'action': 'right_click', 'x': 1, 'y': 1}, {'action': 'drag', 'x': 1, 'y': 1, 'x2': 2, 'y2': 2}):
        with pytest.raises(ValueError, match='sólo se puede mirar y hacer clics sueltos'):
            correr(c.accion(dict(accion, app='Terminal')))
    assert rt.pedidos == []
    with pytest.raises(ValueError, match='no maneja 1Password'):
        correr(c.estado({'app': '1Password'}))
    # Un navegador se mira (con permiso, que lo dice) pero no se toca.
    estado = correr(c.estado({'app': 'Safari'}))
    assert estado['nivel'] == 'lectura' and 'Sólo se puede mirar porque es un navegador' in rt.pedidos[0]['purpose']
    with pytest.raises(ValueError, match='click no se permite'):
        correr(c.accion({'app': 'Safari', 'action': 'click', 'x': 1, 'y': 1}))
    # Un clic suelto en la terminal sí, tras su permiso.
    correr(c.estado({'app': 'Terminal'}))
    correr(c.accion({'app': 'Terminal', 'action': 'click', 'x': 1, 'y': 1}))


def test_la_lista_esconde_los_titulos_hasta_aprobar_la_app():
    c, rt = control('sesion')
    lista = correr(c.lista())
    fila = next(a for a in lista['apps'] if a['nombre'] == 'Contador Demo')
    assert fila['ventanas'] == [{'ventana': 0, 'ancho': 420, 'alto': 252}]
    assert next(a for a in lista['apps'] if a['nombre'] == 'Terminal')['nivel'] == 'clic'
    assert 'bloqueada' in next(a for a in lista['apps'] if a['nombre'] == '1Password')
    correr(c.estado({'app': 'Contador Demo'}))
    fila = next(a for a in correr(c.lista())['apps'] if a['nombre'] == 'Contador Demo')
    assert fila['ventanas'][0]['titulo'] == 'Factura de Ana'


def test_se_pregunta_una_vez_por_app_con_permitir_en_esta_sesion():
    c, rt = control('sesion')
    estado = correr(c.estado({'app': 'contador demo'}))
    assert estado['arbol'].startswith('[0] Window') and 'element' in estado['nota']
    pedido = rt.pedidos[0]
    assert pedido['motivo'] == 'app' and pedido['para_sesion'] is True
    assert pedido['app']['bundle'] == 'com.alfalfacode.demo.contador'
    assert rt.task[sesion.CAMPO] == [apps.clave_de_app(CONTADOR)]
    correr(c.accion({'app': 'com.alfalfacode.demo.contador', 'action': 'click', 'element': 1}))
    assert len(rt.pedidos) == 1


def test_aprobar_solo_esta_vez_vuelve_a_preguntar():
    c, rt = control(None, None)
    correr(c.estado({'app': '4242'}))
    correr(c.estado({'app': '4242'}))
    assert len(rt.pedidos) == 2 and sesion.CAMPO not in rt.task


def test_un_rechazo_no_deja_mirar_ni_tocar():
    c, rt = control('no')
    with pytest.raises(ValueError, match='no dejó controlar Contador Demo'):
        correr(c.estado({'app': 'Contador Demo'}))
    assert [o for o, _ in c.ayudante.llamadas] == ['lista']


def test_el_numero_de_elemento_va_con_su_ruta_y_su_rol():
    c, rt = control('sesion')
    with pytest.raises(ValueError, match='Mira antes la ventana'):
        correr(c.accion({'app': 'Contador Demo', 'action': 'click', 'element': 1}))
    correr(c.estado({'app': 'Contador Demo'}))
    resultado = correr(c.accion({'app': 'Contador Demo', 'action': 'click', 'element': '1'}))
    orden, argumentos = [l for l in c.ayudante.llamadas if l[0] == 'accion'][-1]
    assert argumentos == {'pid': 4242, 'ventana': 0, 'tipo': 'clic', 'ruta': '1', 'rol': 'AXButton'}
    assert resultado['hecho'] is True and resultado['como'] == 'AXPress' and resultado['arbol']
    assert any('App Contador Demo: hacer clic · elemento 1' in e for e in rt.manager.eventos)
    with pytest.raises(ValueError, match='No hay un elemento 9'):
        correr(c.accion({'app': 'Contador Demo', 'action': 'click', 'element': 9}))


def test_las_acciones_piden_lo_que_necesitan():
    c, rt = control('sesion')
    correr(c.estado({'app': 'Contador Demo'}))
    for args, error in (({'action': 'volar'}, 'action debe ser'), ({'action': 'type'}, 'necesita text'),
                        ({'action': 'key'}, 'necesita key'), ({'action': 'click'}, 'element, target o x e y'),
                        ({'action': 'menu'}, 'necesita menu'),
                        ({'action': 'press'}, 'necesita element'), ({'action': 'drag', 'x': 1, 'y': 1}, 'x2 e y2'),
                        ({'action': 'scroll', 'x': 1, 'y': 1}, 'dy')):
        with pytest.raises(ValueError, match=error):
            correr(c.accion(dict(args, app='Contador Demo')))
    correr(c.accion({'app': 'Contador Demo', 'action': 'key', 'key': 'cmd+s'}))
    assert c.ayudante.llamadas[-2][1]['tecla'] == 'cmd+s'


def test_objetivo_por_texto_lee_la_ventana_al_momento():
    c, rt = control('sesion')
    resultado = correr(c.accion({'app': 'Contador Demo', 'action': 'click', 'target': 'sumar uno'}))
    _, argumentos = [l for l in c.ayudante.llamadas if l[0] == 'accion'][-1]
    # «Sumar uno» exacto es el botón; el texto «Sumar uno o dos» sólo lo contiene.
    assert argumentos['ruta'] == '1' and argumentos['rol'] == 'AXButton' and resultado['hecho'] is True
    with pytest.raises(ValueError, match='No hay ningún elemento con el texto «Volar»'):
        correr(c.accion({'app': 'Contador Demo', 'action': 'click', 'target': 'Volar'}))


def test_objetivo_ambiguo_pide_el_numero(monkeypatch):
    c, rt = control('sesion')
    elementos = [dict(e) for e in ESTADO['elementos']]
    elementos[1]['acciones'] = []
    monkeypatch.setitem(ESTADO, 'elementos', elementos)
    with pytest.raises(ValueError, match=r'Varios elementos tienen «sumar»: \[1\] Button Sumar uno; \[3\]'):
        correr(c.accion({'app': 'Contador Demo', 'action': 'click', 'target': 'sumar'}))


def test_lote_hace_varios_pasos_y_para_en_el_que_falla():
    c, rt = control('sesion')
    correr(c.estado({'app': 'Contador Demo'}))
    resultado = correr(c.accion({'app': 'Contador Demo', 'steps': [
        {'action': 'click', 'target': 'Sumar uno'}, {'action': 'click', 'element': 1}]}))
    assert [h['paso'] for h in resultado['hechos']] == [1, 2] and 'Pulsado 2 veces' in resultado['arbol']
    assert resultado['cambios']['aparece'] == ['StaticText valor="Pulsado 2 veces"']
    assert resultado['cambios']['desaparece'] == ['StaticText valor="Pulsado 1 veces"']
    assert len(rt.pedidos) == 1
    fallo = correr(c.accion({'app': 'Contador Demo', 'steps': [
        {'action': 'click', 'element': 1}, {'action': 'key', 'key': 'mal'}, {'action': 'click', 'element': 1}]}))
    assert fallo['fallo'] == {'paso': 2, 'action': 'key', 'error': 'Tecla desconocida: mal.'}
    assert [h['paso'] for h in fallo['hechos']] == [1] and 'Pulsado 3 veces' in fallo['arbol']
    with pytest.raises(ValueError, match='steps debe ser una lista'):
        correr(c.accion({'app': 'Contador Demo', 'steps': []}))


def test_una_accion_sin_efecto_lo_dice():
    c, rt = control('sesion')
    correr(c.estado({'app': 'Contador Demo'}))
    resultado = correr(c.accion({'app': 'Contador Demo', 'action': 'hover', 'element': 1}))
    assert resultado['cambios'] == {'aparece': [], 'desaparece': []} and 'no cambió' in resultado['nota']


def test_esperar_un_texto_y_zoom():
    c, rt = control('sesion')
    c.ayudante.cuenta = 0

    async def mas_tarde():
        await asyncio.sleep(0.6)
        c.ayudante.cuenta = 7

    async def caso():
        tarea = asyncio.ensure_future(mas_tarde())
        resultado = await c.estado({'app': 'Contador Demo', 'wait_for': 'Pulsado 7', 'timeout_seconds': 5})
        await tarea
        return resultado
    resultado = correr(caso())
    assert resultado['espera'] == '«Pulsado 7» ya aparece.'
    nunca = correr(c.estado({'app': 'Contador Demo', 'wait_for': 'Ganaste', 'timeout_seconds': 1}))
    assert 'no apareció' in nunca['espera']
    assert apps._zoom({'x': 1, 'y': 2, 'width': 30, 'height': 40}) == (1, 2, 30, 40)
    with pytest.raises(ValueError, match='zoom'):
        apps._zoom({'x': 1})


def test_raton_propio_por_defecto_y_real_mouse_bajo_pedido(monkeypatch):
    c, rt = control('sesion')
    correr(c.estado({'app': 'Contador Demo'}))
    correr(c.accion({'app': 'Contador Demo', 'action': 'click', 'x': 10, 'y': 20}))
    _, argumentos = [l for l in c.ayudante.llamadas if l[0] == 'accion'][-1]
    assert 'raton' not in argumentos and 'sin-cursor' not in argumentos
    monkeypatch.setenv('ALFALFA_SIN_CURSOR', '1')
    correr(c.accion({'app': 'Contador Demo', 'action': 'click', 'x': 10, 'y': 20, 'real_mouse': True}))
    _, argumentos = [l for l in c.ayudante.llamadas if l[0] == 'accion'][-1]
    assert argumentos['raton'] is True and argumentos['sin-cursor'] is True


def test_las_herramientas_estan_en_alfalfacode_y_plan_no_actua():
    from alfalfa.proveedores.nativo.herramientas import ON_REQUEST_TOOLS, TOOLS
    from alfalfa.proveedores.nativo.runtime import SIN_EFECTOS
    from alfalfa.terminal import ESCRITURA
    nombres = [t['function']['name'] for t in ON_REQUEST_TOOLS]
    assert {'app_list', 'app_state', 'app_action'} <= set(nombres)
    assert 'app_action' not in [t['function']['name'] for t in TOOLS]  # los agentes del integrador no lo tienen
    assert 'app_action' in ESCRITURA and 'app_action' not in SIN_EFECTOS and 'app_state' in SIN_EFECTOS


@pytest.mark.skipif(sys.platform != 'darwin' or os.environ.get('PRUEBA_APPS_DE_VERDAD') != '1',
                    reason='Abre una app de verdad: PRUEBA_APPS_DE_VERDAD=1 en un Mac con Accesibilidad.')
def test_de_punta_a_punta_con_una_app_de_verdad(tmp_path):
    fuente = tmp_path / 'contador.swift'
    fuente.write_text('''import AppKit
final class D: NSObject, NSApplicationDelegate {
    var v: NSWindow!
    let e = NSTextField(labelWithString: "Pulsado 0 veces")
    var n = 0
    func applicationDidFinishLaunching(_ x: Notification) {
        v = NSWindow(contentRect: NSRect(x: 200, y: 300, width: 360, height: 300), styleMask: [.titled],
                     backing: .buffered, defer: false)
        v.title = "Prueba AlfalfaCode"
        let b = NSButton(title: "Sumar uno", target: self, action: #selector(s))
        let c = NSTextField(string: "")
        let destino = NSPopUpButton(frame: .zero, pullsDown: false)
        destino.addItems(withTitles: ["Cuy Café", "Plaza", "Bosque"])
        let p = NSStackView(views: [e, b, c, destino])
        p.orientation = .vertical
        p.frame = NSRect(x: 20, y: 10, width: 320, height: 135)
        v.contentView?.addSubview(p)
        let etiqueta = NSTextField(labelWithString: "")
        etiqueta.frame = NSRect(x: 20, y: 270, width: 330, height: 20)
        let lienzo = L(frame: NSRect(x: 20, y: 150, width: 320, height: 110))
        lienzo.e = etiqueta
        lienzo.pintar()
        v.contentView?.addSubview(etiqueta)
        v.contentView?.addSubview(lienzo)
        let barra = NSMenu()
        let primero = NSMenuItem(); primero.submenu = NSMenu(title: "Prueba"); barra.addItem(primero)
        let acciones = NSMenuItem(); let menu = NSMenu(title: "Acciones")
        menu.addItem(NSMenuItem(title: "Sumar uno", action: #selector(s), keyEquivalent: ""))
        acciones.submenu = menu; barra.addItem(acciones)
        NSApp.mainMenu = barra
        v.makeKeyAndOrderFront(nil)
    }
    @objc func s() { n += 1; e.stringValue = "Pulsado \\(n) veces" }
}
final class L: NSView {
    var clics = 0, arrastre = 0.0, inicio = NSPoint.zero
    var e: NSTextField!
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { false }
    func pintar() { e.stringValue = "lienzo clics=\\(clics) arrastre=\\(Int(arrastre)) raton=\\(Int(NSEvent.mouseLocation.x)),\\(Int(NSEvent.mouseLocation.y))" }
    override func mouseDown(with x: NSEvent) { clics += 1; inicio = x.locationInWindow; pintar() }
    override func mouseDragged(with x: NSEvent) { arrastre = hypot(x.locationInWindow.x - inicio.x, x.locationInWindow.y - inicio.y); pintar() }
}
let a = NSApplication.shared
let d = D()
a.delegate = d
a.setActivationPolicy(.regular)
a.run()
''')
    binario = tmp_path / 'PruebaAlfalfa'
    subprocess.run(['swiftc', '-O', str(fuente), '-o', str(binario)], check=True)
    proc = subprocess.Popen([str(binario)])
    try:
        rt = Runtime(['sesion'])
        rt.manager.home = tmp_path / 'hogar'
        rt.directory = tmp_path / 'sesion'
        c = apps.ControlDeApps(rt)
        fin = time.time() + 15
        while time.time() < fin:
            if any(a.get('pid') == proc.pid and a.get('ventanas') for a in correr(c.lista())['apps']):
                break
            time.sleep(0.3)
        estado = correr(c.estado({'app': str(proc.pid)}))
        boton = next(int(l.split(']')[0].strip(' [')) for l in estado['arbol'].splitlines() if 'Sumar uno' in l)
        assert Path(estado['captura']).is_file()
        despues = correr(c.accion({'app': str(proc.pid), 'action': 'click', 'element': boton}))
        assert 'Pulsado 1 veces' in despues['arbol']
        campo = next(int(l.split(']')[0].strip(' [')) for l in despues['arbol'].splitlines() if 'TextField' in l)
        despues = correr(c.accion({'app': str(proc.pid), 'action': 'type', 'element': campo, 'text': 'Señal ñ'}))
        assert 'Señal ñ' in despues['arbol']
        # Ratón propio: un lienzo que no acepta el primer clic, sin traer la app ni mover el ratón.
        import re
        raton = re.search(r'raton=(-?\d+),(-?\d+)', despues['arbol']).groups()
        despues = correr(c.accion({'app': str(proc.pid), 'action': 'click', 'x': 100, 'y': 80}))
        assert 'lienzo clics=1' in despues['arbol'], despues['arbol']
        despues = correr(c.accion({'app': str(proc.pid), 'action': 'drag', 'x': 40, 'y': 120, 'x2': 240, 'y2': 120}))
        assert 'arrastre=200' in despues['arbol'], despues['arbol']
        assert re.search(r'raton=(-?\d+),(-?\d+)', despues['arbol']).groups() == raton  # el ratón no se movió
        # Menú, objetivo por texto y varios pasos en una llamada.
        despues = correr(c.accion({'app': str(proc.pid), 'steps': [
            {'action': 'menu', 'menu': 'Acciones > Sumar uno'},
            {'action': 'click', 'target': 'Sumar uno'}]}))
        assert 'Pulsado 3 veces' in despues['arbol'], despues
        assert [h['como'] for h in despues['hechos']] == ['menu', 'AXPress']
        assert 'StaticText valor="Pulsado 3 veces"' in [l.split('(')[0].strip() for l in despues['cambios']['aparece']]
        # Desplegable por coordenadas: un clic en fondo no abre su menú; se abre por accesibilidad.
        def centro(arbol, texto):
            linea = next(l for l in arbol.splitlines() if texto in l)
            x, y, w, h = map(int, re.search(r'\((-?\d+),(-?\d+) (\d+)×(\d+)\)', linea).groups())
            return x + w // 2, y + h // 2
        x, y = centro(despues['arbol'], 'PopUpButton valor="Cuy Café"')
        despues = correr(c.accion({'app': str(proc.pid), 'action': 'click', 'x': x, 'y': y}))
        assert 'MenuItem titulo="Bosque"' in despues['arbol'], despues['arbol']
        x, y = centro(despues['arbol'], 'MenuItem titulo="Bosque"')
        despues = correr(c.accion({'app': str(proc.pid), 'action': 'click', 'x': x, 'y': y}))
        assert 'PopUpButton valor="Bosque"' in despues['arbol'], despues['arbol']
    finally:
        proc.terminate()
        proc.wait(5)
