// Ayudante de AlfalfaCode para mirar y manejar otras apps del Mac (control de apps).
//
// Lo compila AlfalfaCode la primera vez (swiftc) y lo llama con una orden y JSON por
// stdout. Usa la accesibilidad de macOS (AXUIElement) para leer la ventana como un
// árbol de elementos numerados, `screencapture -l` para la captura y eventos mandados
// al proceso de la app (postToPid) para su propio ratón y teclado, como Computer Use de
// Codex: no mueve el ratón del usuario ni trae la app al frente, y dibuja su cursor. Necesita los permisos de Accesibilidad y Grabación de pantalla
// de la app que lanzó AlfalfaCode (Terminal, la app AlfalfaCode…).
//
// Órdenes (todas devuelven un objeto JSON; los errores, {"error": "..."} y código 1):
//   permisos [--pedir]
//   lista
//   estado --pid=P [--ventana=N] [--captura=RUTA] [--max=N]
//   accion --pid=P --ventana=N --tipo=clic|doble|derecho|escribir|tecla|desplazar|valor|pulsar|arrastrar|activar|pasar|menu
//          [--ruta=0.3.1 --rol=AXButton] [--x=X --y=Y] [--x2=X --y2=Y] [--texto=T] [--tecla=cmd+s]
//          [--dy=N --dx=N] [--accion=AXPress] [--raton] [--sin-cursor]
//   cursor --x=X --y=Y [--x2=X --y2=Y] [--tipo=clic|mover]   (puntos globales; lo lanza accion)
// Las coordenadas son puntos relativos a la esquina superior izquierda de la ventana.
import AppKit
import ApplicationServices
import Foundation

func salir(_ objeto: Any, codigo: Int32 = 0) -> Never {
    let datos = (try? JSONSerialization.data(withJSONObject: objeto, options: [.sortedKeys])) ?? Data("{}".utf8)
    FileHandle.standardOutput.write(datos)
    FileHandle.standardOutput.write(Data("\n".utf8))
    exit(codigo)
}

func fallar(_ texto: String) -> Never { salir(["error": texto], codigo: 1) }

var argumentos: [String: String] = [:]
var banderas: Set<String> = []
let lista = Array(CommandLine.arguments.dropFirst())
guard let orden = lista.first else { fallar("Falta la orden.") }
// `--clave=valor` (el valor puede empezar por guiones o llevar espacios) o `--bandera`.
for parte in lista.dropFirst() where parte.hasPrefix("--") {
    let cuerpo = parte.dropFirst(2)
    if let igual = cuerpo.firstIndex(of: "=") {
        argumentos[String(cuerpo[..<igual])] = String(cuerpo[cuerpo.index(after: igual)...])
    } else {
        banderas.insert(String(cuerpo))
    }
}

func entero(_ clave: String) -> Int? { argumentos[clave].flatMap { Int($0) } }
func real(_ clave: String) -> Double? { argumentos[clave].flatMap { Double($0) } }

// MARK: - Accesibilidad

func atributo(_ elemento: AXUIElement, _ nombre: String) -> AnyObject? {
    var valor: AnyObject?
    guard AXUIElementCopyAttributeValue(elemento, nombre as CFString, &valor) == .success else { return nil }
    return valor
}

func texto(_ elemento: AXUIElement, _ nombre: String) -> String? {
    guard let valor = atributo(elemento, nombre) else { return nil }
    if let cadena = valor as? String { return cadena.isEmpty ? nil : cadena }
    if let numero = valor as? NSNumber { return numero.stringValue }
    return nil
}

func marco(_ elemento: AXUIElement) -> CGRect? {
    guard let posicion = atributo(elemento, kAXPositionAttribute as String),
          let tamano = atributo(elemento, kAXSizeAttribute as String) else { return nil }
    var punto = CGPoint.zero
    var medida = CGSize.zero
    guard AXValueGetValue(posicion as! AXValue, .cgPoint, &punto),
          AXValueGetValue(tamano as! AXValue, .cgSize, &medida) else { return nil }
    return CGRect(origin: punto, size: medida)
}

func hijos(_ elemento: AXUIElement) -> [AXUIElement] {
    (atributo(elemento, kAXChildrenAttribute as String) as? [AXUIElement]) ?? []
}

func acciones(_ elemento: AXUIElement) -> [String] {
    var nombres: CFArray?
    guard AXUIElementCopyActionNames(elemento, &nombres) == .success else { return [] }
    return (nombres as? [String]) ?? []
}

func ventanas(_ pid: pid_t) -> [AXUIElement] {
    let app = AXUIElementCreateApplication(pid)
    return (atributo(app, kAXWindowsAttribute as String) as? [AXUIElement]) ?? []
}

func ventana(_ pid: pid_t, _ numero: Int?) -> (AXUIElement, Int) {
    let todas = ventanas(pid)
    guard !todas.isEmpty else {
        fallar(AXIsProcessTrusted() ? "La app no tiene ventanas abiertas que se puedan leer."
               : "Falta el permiso de Accesibilidad (Ajustes del Sistema → Privacidad y seguridad → Accesibilidad).")
    }
    let indice = numero ?? 0
    guard todas.indices.contains(indice) else { fallar("No hay una ventana \(indice); hay \(todas.count).") }
    return (todas[indice], indice)
}

func recorrer(ruta: String, desde raiz: AXUIElement) -> AXUIElement {
    var actual = raiz
    if ruta.isEmpty { return actual }
    for paso in ruta.split(separator: ".") {
        guard let n = Int(paso) else { fallar("Ruta de elemento inválida.") }
        let lista = hijos(actual)
        guard lista.indices.contains(n) else {
            fallar("El elemento ya no está donde estaba: la ventana cambió. Vuelve a mirarla con app_state.")
        }
        actual = lista[n]
    }
    return actual
}

// MARK: - Ventanas de CoreGraphics (id para la captura)

func idDeVentana(pid: pid_t, marco objetivo: CGRect?, titulo: String?) -> CGWindowID? {
    let opciones: CGWindowListOption = [.optionOnScreenOnly, .excludeDesktopElements]
    guard let info = CGWindowListCopyWindowInfo(opciones, kCGNullWindowID) as? [[String: Any]] else { return nil }
    let propias = info.filter { ($0[kCGWindowOwnerPID as String] as? Int32) == pid
        && ($0[kCGWindowLayer as String] as? Int) == 0 }
    func rect(_ fila: [String: Any]) -> CGRect? {
        guard let d = fila[kCGWindowBounds as String] as? NSDictionary else { return nil }
        return CGRect(dictionaryRepresentation: d as CFDictionary)
    }
    if let objetivo {
        if let exacta = propias.first(where: { rect($0).map { abs($0.minX - objetivo.minX) < 2 && abs($0.minY - objetivo.minY) < 2
            && abs($0.width - objetivo.width) < 2 && abs($0.height - objetivo.height) < 2 } ?? false }) {
            return (exacta[kCGWindowNumber as String] as? NSNumber).map { CGWindowID($0.uint32Value) }
        }
    }
    if let titulo, let porTitulo = propias.first(where: { ($0[kCGWindowName as String] as? String) == titulo }) {
        return (porTitulo[kCGWindowNumber as String] as? NSNumber).map { CGWindowID($0.uint32Value) }
    }
    return (propias.first?[kCGWindowNumber as String] as? NSNumber).map { CGWindowID($0.uint32Value) }
}

// MARK: - Órdenes

func permisos() -> Never {
    if banderas.contains("pedir") {
        let opciones = [kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: true] as CFDictionary
        _ = AXIsProcessTrustedWithOptions(opciones)
        if !CGPreflightScreenCaptureAccess() { _ = CGRequestScreenCaptureAccess() }
    }
    salir(["accesibilidad": AXIsProcessTrusted(), "grabacion_de_pantalla": CGPreflightScreenCaptureAccess()])
}

func listar() -> Never {
    var filas: [[String: Any]] = []
    for app in NSWorkspace.shared.runningApplications where app.activationPolicy == .regular {
        let pid = app.processIdentifier
        let titulos = ventanas(pid).enumerated().map { indice, ventana -> [String: Any] in
            var fila: [String: Any] = ["ventana": indice]
            if let titulo = texto(ventana, kAXTitleAttribute as String) { fila["titulo"] = String(titulo.prefix(120)) }
            if let m = marco(ventana) { fila["ancho"] = Int(m.width); fila["alto"] = Int(m.height) }
            return fila
        }
        var fila: [String: Any] = ["pid": Int(pid), "nombre": app.localizedName ?? "", "ventanas": titulos,
                                   "activa": app.isActive, "oculta": app.isHidden]
        if let id = app.bundleIdentifier { fila["bundle"] = id }
        if let url = app.executableURL?.path { fila["ejecutable"] = url }
        filas.append(fila)
    }
    salir(["apps": filas, "accesibilidad": AXIsProcessTrusted()])
}

func estado() -> Never {
    guard let pidNumero = entero("pid") else { fallar("Falta --pid.") }
    let pid = pid_t(pidNumero)
    let (raiz, indice) = ventana(pid, entero("ventana"))
    guard let origen = marco(raiz) else { fallar("No se pudo leer el marco de la ventana.") }
    let maximo = entero("max") ?? 400
    var elementos: [[String: Any]] = []
    var lineas: [String] = []
    var recortado = false
    func visitar(_ elemento: AXUIElement, ruta: String, nivel: Int) {
        if elementos.count >= maximo { recortado = true; return }
        let rol = texto(elemento, kAXRoleAttribute as String) ?? "?"
        var fila: [String: Any] = ["i": elementos.count, "ruta": ruta, "rol": rol]
        var partes = [rol.replacingOccurrences(of: "AX", with: "")]
        if let sub = texto(elemento, kAXSubroleAttribute as String) { fila["subrol"] = sub }
        for (clave, nombre) in [("titulo", kAXTitleAttribute), ("descripcion", kAXDescriptionAttribute),
                                ("valor", kAXValueAttribute), ("ayuda", kAXHelpAttribute),
                                ("id", kAXIdentifierAttribute)] {
            if let valor = texto(elemento, nombre as String) {
                let corto = String(valor.prefix(clave == "valor" ? 200 : 120))
                fila[clave] = corto
                // La ayuda es el texto del tooltip: así se lee sin pasar el ratón por encima.
                if clave != "id" { partes.append("\(clave)=\"\(corto.replacingOccurrences(of: "\n", with: " "))\"") }
            }
        }
        if let m = marco(elemento) {
            let x = Int(m.minX - origen.minX), y = Int(m.minY - origen.minY)
            fila["x"] = x; fila["y"] = y; fila["ancho"] = Int(m.width); fila["alto"] = Int(m.height)
            partes.append("(\(x),\(y) \(Int(m.width))×\(Int(m.height)))")
        }
        if (atributo(elemento, kAXEnabledAttribute as String) as? Bool) == false { fila["inactivo"] = true; partes.append("inactivo") }
        if (atributo(elemento, kAXFocusedAttribute as String) as? Bool) == true { fila["foco"] = true; partes.append("foco") }
        let lista = acciones(elemento).filter { $0 != "AXShowMenu" && $0 != "AXScrollToVisible" }
        if !lista.isEmpty { fila["acciones"] = lista }
        elementos.append(fila)
        lineas.append(String(repeating: "  ", count: nivel) + "[\(fila["i"]!)] " + partes.joined(separator: " "))
        for (n, hijo) in hijos(elemento).enumerated() {
            visitar(hijo, ruta: ruta.isEmpty ? "\(n)" : "\(ruta).\(n)", nivel: nivel + 1)
        }
    }
    visitar(raiz, ruta: "", nivel: 0)
    var resultado: [String: Any] = [
        "ventana": indice, "titulo": texto(raiz, kAXTitleAttribute as String) ?? "",
        "ancho": Int(origen.width), "alto": Int(origen.height), "elementos": elementos,
        "arbol": lineas.joined(separator: "\n"), "recortado": recortado,
    ]
    if let ruta = argumentos["captura"] {
        if !CGPreflightScreenCaptureAccess() {
            resultado["captura_error"] = "Falta el permiso de Grabación de pantalla (Ajustes del Sistema → Privacidad y seguridad)."
        } else if let id = idDeVentana(pid: pid, marco: origen, titulo: texto(raiz, kAXTitleAttribute as String)) {
            let proceso = Process()
            proceso.executableURL = URL(fileURLWithPath: "/usr/sbin/screencapture")
            proceso.arguments = ["-x", "-o", "-l", String(id), ruta]
            do {
                try proceso.run()
                proceso.waitUntilExit()
                if proceso.terminationStatus == 0, FileManager.default.fileExists(atPath: ruta) {
                    resultado["captura"] = ruta
                } else {
                    resultado["captura_error"] = "screencapture no pudo capturar la ventana."
                }
            } catch {
                resultado["captura_error"] = "No se pudo lanzar screencapture."
            }
        } else {
            resultado["captura_error"] = "No se encontró la ventana en pantalla (¿está minimizada o en otro escritorio?)."
        }
    }
    salir(resultado)
}

// MARK: - Entrada

let teclas: [String: CGKeyCode] = [
    "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7, "c": 8, "v": 9, "b": 11, "q": 12, "w": 13,
    "e": 14, "r": 15, "y": 16, "t": 17, "1": 18, "2": 19, "3": 20, "4": 21, "6": 22, "5": 23, "=": 24, "9": 25,
    "7": 26, "-": 27, "8": 28, "0": 29, "]": 30, "o": 31, "u": 32, "[": 33, "i": 34, "p": 35, "l": 37, "j": 38,
    "'": 39, "k": 40, ";": 41, "\\": 42, ",": 43, "/": 44, "n": 45, "m": 46, ".": 47, "`": 50,
    "return": 36, "enter": 36, "tab": 48, "space": 49, "espacio": 49, "delete": 51, "backspace": 51, "borrar": 51,
    "escape": 53, "esc": 53, "forwarddelete": 117, "suprimir": 117, "home": 115, "end": 119, "pageup": 116,
    "pagedown": 121, "left": 123, "izquierda": 123, "right": 124, "derecha": 124, "down": 125, "abajo": 125,
    "up": 126, "arriba": 126, "f1": 122, "f2": 120, "f3": 99, "f4": 118, "f5": 96, "f6": 97, "f7": 98, "f8": 100,
    "f9": 101, "f10": 109, "f11": 103, "f12": 111,
]
let modificadores: [String: CGEventFlags] = [
    "cmd": .maskCommand, "command": .maskCommand, "comando": .maskCommand, "shift": .maskShift,
    "mayus": .maskShift, "alt": .maskAlternate, "option": .maskAlternate, "opcion": .maskAlternate,
    "ctrl": .maskControl, "control": .maskControl, "fn": .maskSecondaryFn,
]

func activar(_ pid: pid_t, _ raiz: AXUIElement) {
    // Sólo con real_mouse o focus. Desde macOS 14 un proceso de fondo no puede activar
    // otra app con NSRunningApplication; por accesibilidad (AXFrontmost y AXRaise) a veces sí.
    NSRunningApplication(processIdentifier: pid)?.activate(options: [])
    AXUIElementSetAttributeValue(AXUIElementCreateApplication(pid), kAXFrontmostAttribute as CFString, kCFBooleanTrue)
    AXUIElementSetAttributeValue(raiz, kAXMainAttribute as CFString, kCFBooleanTrue)
    AXUIElementPerformAction(raiz, kAXRaiseAction as CFString)
    for _ in 0..<20 where NSWorkspace.shared.frontmostApplication?.processIdentifier != pid {
        usleep(50_000)
    }
    usleep(100_000)
}

/// El ratón de verdad cae en la ventana que esté encima del punto: con real_mouse, antes de
/// pulsar se comprueba que es de la app elegida, para no hacer clic en otra.
func esDeLaApp(_ lugar: CGPoint, _ pid: pid_t) -> Bool {
    let opciones: CGWindowListOption = [.optionOnScreenOnly, .excludeDesktopElements]
    let info = (CGWindowListCopyWindowInfo(opciones, kCGNullWindowID) as? [[String: Any]]) ?? []
    let propio = ProcessInfo.processInfo.processIdentifier
    for fila in info {
        guard let d = fila[kCGWindowBounds as String] as? NSDictionary,
              let rect = CGRect(dictionaryRepresentation: d as CFDictionary), rect.contains(lugar) else { continue }
        if let alfa = fila[kCGWindowAlpha as String] as? Double, alfa <= 0.01 { continue }
        let dueño = fila[kCGWindowOwnerPID as String] as? Int32
        if dueño == pid { return true }
        let nombre = fila[kCGWindowOwnerName as String] as? String ?? "otra app"
        // Las capas del sistema (el cursor, efectos) y el cursor propio no reciben el clic.
        if nombre == "Window Server" || dueño == propio || nombre.hasPrefix("alfalfa-apps") { continue }
        // El Dock pone una ventana transparente a pantalla completa que no recibe clics.
        if nombre == "Dock", (fila[kCGWindowLayer as String] as? Int) == 20, rect.width >= 600, rect.height >= 400 { continue }
        return false
    }
    return false
}

/// Desplegables y menús bajo un punto global. Un clic de ratón en fondo no los abre: AppKit
/// sigue el menú con el ratón de verdad y lo cierra al momento, así que se abren por accesibilidad.
let rolesDeDesplegable: Set<String> = ["AXPopUpButton", "AXMenuButton", "AXComboBox", "AXMenuItem"]

func desplegableEn(_ pid: pid_t, _ lugar: CGPoint) -> (AXUIElement, String)? {
    var encontrado: AXUIElement?
    guard AXUIElementCopyElementAtPosition(AXUIElementCreateApplication(pid), Float(lugar.x), Float(lugar.y),
                                           &encontrado) == .success, var actual = encontrado else { return nil }
    for _ in 0..<3 {
        if let rol = texto(actual, kAXRoleAttribute as String), rolesDeDesplegable.contains(rol) {
            let lista = acciones(actual)
            if lista.contains(kAXPressAction as String) { return (actual, kAXPressAction as String) }
            if lista.contains(kAXShowMenuAction as String) { return (actual, kAXShowMenuAction as String) }
            return nil
        }
        var padre: CFTypeRef?
        guard AXUIElementCopyAttributeValue(actual, kAXParentAttribute as CFString, &padre) == .success,
              let siguiente = padre, CFGetTypeID(siguiente) == AXUIElementGetTypeID() else { return nil }
        actual = siguiente as! AXUIElement
    }
    return nil
}

func punto(_ origen: CGRect, _ x: Double?, _ y: Double?) -> CGPoint {
    guard let x, let y else { fallar("Faltan --x y --y (puntos relativos a la ventana).") }
    guard x >= 0, y >= 0, x <= origen.width, y <= origen.height else {
        fallar("El punto (\(Int(x)),\(Int(y))) cae fuera de la ventana de \(Int(origen.width))×\(Int(origen.height)).")
    }
    return CGPoint(x: x, y: y)
}

// MARK: - Ratón propio, en fondo
//
// Como Computer Use de Codex, que fabrica los clics con NSEvent y el número de ventana:
// el evento lleva la posición relativa a la ventana y su número (campo 51), y se manda al
// proceso de la app con postToPid. Así no se mueve el ratón del usuario, la app no pasa al
// frente y da igual qué ventana la tape. Comprobado con una app AppKit inactiva y tapada:
// el botón se pulsa y el usuario sigue en la suya.
let campoVentana = CGEventField(rawValue: 51)!

// Una ventana inactiva se traga el primer clic de una vista que no acepta «first mouse»
// (casi todas, y el lienzo de un juego). Como Codex (kCPSNotifyKeyFocusTaken en su
// servicio), se le dice sólo a esa app que tiene el foco y que su ventana es la «key», con
// los registros de eventos del gestor de procesos (SLPSPostEventRecordTo, el truco de yabai).
// El proceso de delante no cambia: el teclado y el ratón del usuario siguen en su app.
// Al terminar se le devuelve el «sin foco». Si macOS no tiene la función, se sigue sin ella.
typealias EnviarRegistro = @convention(c) (UnsafeRawPointer, UnsafePointer<UInt8>) -> Int32
typealias ProcesoDePid = @convention(c) (pid_t, UnsafeMutablePointer<ProcessSerialNumber>) -> OSStatus
let enviarRegistro: EnviarRegistro? = dlopen("/System/Library/PrivateFrameworks/SkyLight.framework/SkyLight", RTLD_NOW)
    .flatMap { dlsym($0, "SLPSPostEventRecordTo") }.map { unsafeBitCast($0, to: EnviarRegistro.self) }
let procesoDePid: ProcesoDePid? = dlopen("/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices", RTLD_NOW)
    .flatMap { dlsym($0, "GetProcessForPID") }.map { unsafeBitCast($0, to: ProcesoDePid.self) }

func registroDeFoco(_ ventana: CGWindowID, _ ajustes: [(Int, UInt8)], relleno: Bool) -> [UInt8] {
    var bytes = [UInt8](repeating: 0, count: 0xf8)
    bytes[0x04] = 0xf8
    for (indice, valor) in ajustes { bytes[indice] = valor }
    withUnsafeBytes(of: ventana.littleEndian) { for (i, b) in $0.enumerated() { bytes[0x3c + i] = b } }
    if relleno { for i in 0x20..<0x30 { bytes[i] = 0xff } }
    return bytes
}

func avisarFoco(_ pid: pid_t, _ ventana: CGWindowID, tomar: Bool) {
    guard let enviarRegistro, let procesoDePid else { return }
    var psn = ProcessSerialNumber()
    guard procesoDePid(pid, &psn) == noErr else { return }
    var registros = [registroDeFoco(ventana, [(0x08, 0x0d), (0x8a, tomar ? 0x01 : 0x02)], relleno: false)]
    if tomar {
        registros.append(registroDeFoco(ventana, [(0x08, 0x01), (0x3a, 0x10)], relleno: true))
        registros.append(registroDeFoco(ventana, [(0x08, 0x02), (0x3a, 0x10)], relleno: true))
    }
    for registro in registros { _ = withUnsafePointer(to: &psn) { enviarRegistro($0, registro) } }
    usleep(30_000)
}

func eventoDeVentana(_ tipo: NSEvent.EventType, _ local: CGPoint, ventana: CGWindowID, clics: Int) -> CGEvent? {
    let alto = NSScreen.screens.first?.frame.height ?? 0
    let presion: Float = [.leftMouseDown, .rightMouseDown, .leftMouseDragged].contains(tipo) ? 1 : 0
    guard let evento = NSEvent.mouseEvent(with: tipo, location: NSPoint(x: local.x, y: alto - local.y),
                                          modifierFlags: [], timestamp: ProcessInfo.processInfo.systemUptime,
                                          windowNumber: Int(ventana), context: nil, eventNumber: 0,
                                          clickCount: clics, pressure: presion)?.cgEvent else { return nil }
    // No tocar `location`: cambiarla borra la posición relativa a la ventana que puso NSEvent.
    evento.setIntegerValueField(campoVentana, value: Int64(ventana))
    return evento
}

func clicEnFondo(_ local: CGPoint, pid: pid_t, ventana: CGWindowID, derecho: Bool, veces: Int) {
    let (abajo, arriba): (NSEvent.EventType, NSEvent.EventType) = derecho ? (.rightMouseDown, .rightMouseUp)
        : (.leftMouseDown, .leftMouseUp)
    eventoDeVentana(.mouseMoved, local, ventana: ventana, clics: 0)?.postToPid(pid)
    usleep(20_000)
    for n in 1...max(1, veces) {
        eventoDeVentana(abajo, local, ventana: ventana, clics: n)?.postToPid(pid)
        usleep(30_000)
        eventoDeVentana(arriba, local, ventana: ventana, clics: n)?.postToPid(pid)
        usleep(60_000)
    }
}

func arrastrarEnFondo(_ desde: CGPoint, _ hasta: CGPoint, pid: pid_t, ventana: CGWindowID) {
    eventoDeVentana(.leftMouseDown, desde, ventana: ventana, clics: 1)?.postToPid(pid)
    usleep(40_000)
    for paso in 1...16 {
        let t = Double(paso) / 16
        let lugar = CGPoint(x: desde.x + (hasta.x - desde.x) * t, y: desde.y + (hasta.y - desde.y) * t)
        eventoDeVentana(.leftMouseDragged, lugar, ventana: ventana, clics: 1)?.postToPid(pid)
        usleep(16_000)
    }
    eventoDeVentana(.leftMouseUp, hasta, ventana: ventana, clics: 1)?.postToPid(pid)
}

func desplazarEnFondo(_ local: CGPoint, dx: Double, dy: Double, pid: pid_t, ventana: CGWindowID) {
    eventoDeVentana(.mouseMoved, local, ventana: ventana, clics: 0)?.postToPid(pid)
    usleep(20_000)
    // NSEvent no fabrica desplazamientos: se parte de un movimiento suyo, que lleva la posición
    // en la ventana, y se convierte. En trozos, como una rueda o un trackpad.
    let pasos = max(1, Int((max(abs(dx), abs(dy)) / 40).rounded(.up)))
    for _ in 0..<pasos {
        guard let evento = eventoDeVentana(.mouseMoved, local, ventana: ventana, clics: 0) else { continue }
        evento.type = .scrollWheel
        let y = -dy / Double(pasos), x = -dx / Double(pasos)
        evento.setIntegerValueField(.scrollWheelEventDeltaAxis1, value: Int64((y / 10).rounded()))
        evento.setIntegerValueField(.scrollWheelEventDeltaAxis2, value: Int64((x / 10).rounded()))
        evento.setIntegerValueField(.scrollWheelEventPointDeltaAxis1, value: Int64(y.rounded()))
        evento.setIntegerValueField(.scrollWheelEventPointDeltaAxis2, value: Int64(x.rounded()))
        evento.setDoubleValueField(.scrollWheelEventFixedPtDeltaAxis1, value: y / 10)
        evento.setDoubleValueField(.scrollWheelEventFixedPtDeltaAxis2, value: x / 10)
        evento.setIntegerValueField(.scrollWheelEventIsContinuous, value: 1)
        evento.postToPid(pid)
        usleep(16_000)
    }
}

// MARK: - Ratón de verdad (real_mouse: true)

func clicReal(_ lugar: CGPoint, boton: CGMouseButton, veces: Int) {
    let (abajo, arriba): (CGEventType, CGEventType) = boton == .right ? (.rightMouseDown, .rightMouseUp)
        : (.leftMouseDown, .leftMouseUp)
    CGEvent(mouseEventSource: nil, mouseType: .mouseMoved, mouseCursorPosition: lugar, mouseButton: boton)?.post(tap: .cghidEventTap)
    usleep(40_000)
    for n in 1...max(1, veces) {
        let pulsa = CGEvent(mouseEventSource: nil, mouseType: abajo, mouseCursorPosition: lugar, mouseButton: boton)
        let suelta = CGEvent(mouseEventSource: nil, mouseType: arriba, mouseCursorPosition: lugar, mouseButton: boton)
        pulsa?.setIntegerValueField(.mouseEventClickState, value: Int64(n))
        suelta?.setIntegerValueField(.mouseEventClickState, value: Int64(n))
        pulsa?.post(tap: .cghidEventTap)
        usleep(30_000)
        suelta?.post(tap: .cghidEventTap)
        usleep(60_000)
    }
}

func exigirDelante(_ lugar: CGPoint, _ pid: pid_t, _ raiz: AXUIElement) {
    activar(pid, raiz)
    if !esDeLaApp(lugar, pid) {
        fallar("No se hizo nada: con real_mouse la app tiene que estar delante en ese punto y otra ventana lo tapa "
               + "(macOS no deja traerla al frente mientras el usuario usa otra). Prueba sin real_mouse.")
    }
}

// MARK: - Cursor propio dibujado

/// Lanza `ayudante cursor` aparte (no espera): dibuja un cursor donde actúa el agente y se va solo.
func mostrarCursor(_ global: CGPoint, hasta: CGPoint? = nil, tipo: String) {
    guard !banderas.contains("sin-cursor") else { return }
    let proceso = Process()
    proceso.executableURL = URL(fileURLWithPath: CommandLine.arguments[0]).standardizedFileURL
    var lista = ["cursor", "--x=\(global.x)", "--y=\(global.y)", "--tipo=\(tipo)"]
    if let hasta { lista += ["--x2=\(hasta.x)", "--y2=\(hasta.y)"] }
    proceso.arguments = lista
    proceso.standardInput = FileHandle.nullDevice
    proceso.standardOutput = FileHandle.nullDevice
    proceso.standardError = FileHandle.nullDevice
    try? proceso.run()
}

final class VistaDeCursor: NSView {
    var pulso: CGFloat = 0
    let punta = CGPoint(x: 14, y: 46)

    override func draw(_ dirtyRect: NSRect) {
        let verde = NSColor(calibratedRed: 0.36, green: 0.72, blue: 0.29, alpha: 1)
        if pulso > 0 {
            let radio = 6 + 14 * pulso
            let aro = NSBezierPath(ovalIn: NSRect(x: punta.x - radio, y: punta.y - radio, width: radio * 2, height: radio * 2))
            verde.withAlphaComponent(0.55 * (1 - pulso)).setStroke()
            aro.lineWidth = 3
            aro.stroke()
        }
        let flecha = NSBezierPath()
        let puntos: [(CGFloat, CGFloat)] = [(0, 0), (0, -24), (6.5, -18.5), (11, -28), (15, -26.2), (10.6, -16.8), (19, -16.8)]
        for (n, (x, y)) in puntos.enumerated() {
            let p = NSPoint(x: punta.x + x, y: punta.y + y)
            if n == 0 { flecha.move(to: p) } else { flecha.line(to: p) }
        }
        flecha.close()
        verde.setFill()
        flecha.fill()
        NSColor.white.setStroke()
        flecha.lineWidth = 1.6
        flecha.lineJoinStyle = .round
        flecha.stroke()
    }
}

func cursor() -> Never {
    let app = NSApplication.shared
    app.setActivationPolicy(.accessory)
    let alto = NSScreen.screens.first?.frame.height ?? 0
    let tamano = NSSize(width: 64, height: 64)
    let vista = VistaDeCursor(frame: NSRect(origin: .zero, size: tamano))
    // De arriba-izquierda (Quartz) a abajo-izquierda (AppKit), con la punta de la flecha en el punto.
    func origen(_ x: Double, _ y: Double) -> NSPoint {
        NSPoint(x: x - vista.punta.x, y: alto - y - vista.punta.y)
    }
    let x = real("x") ?? 0, y = real("y") ?? 0
    let ventana = NSWindow(contentRect: NSRect(origin: origen(x, y), size: tamano), styleMask: .borderless,
                           backing: .buffered, defer: false)
    ventana.isOpaque = false
    ventana.backgroundColor = .clear
    ventana.hasShadow = false
    ventana.ignoresMouseEvents = true
    ventana.level = .screenSaver
    ventana.collectionBehavior = [.canJoinAllSpaces, .stationary, .ignoresCycle, .fullScreenAuxiliary]
    ventana.sharingType = .none  // no sale en las capturas del agente ni al compartir pantalla
    ventana.contentView = vista
    ventana.orderFrontRegardless()
    let inicio = Date()
    let destino = (real("x2"), real("y2"))
    let clic = argumentos["tipo"] != "mover"
    Timer.scheduledTimer(withTimeInterval: 1.0 / 60, repeats: true) { _ in
        let t = Date().timeIntervalSince(inicio)
        if let x2 = destino.0, let y2 = destino.1 {
            let avance = min(1, t / 0.45)
            ventana.setFrameOrigin(origen(x + (x2 - x) * avance, y + (y2 - y) * avance))
        }
        if clic { vista.pulso = CGFloat(min(1, t / 0.5)); vista.needsDisplay = true }
        if t > 1.1 { ventana.alphaValue = CGFloat(max(0, 1 - (t - 1.1) / 0.4)) }
        if t > 1.55 { exit(0) }
    }
    app.run()
    exit(0)
}

// El teclado va al proceso de la app elegida (postToPid), nunca a la que esté delante:
// si la app no llega al frente, el texto no cae en otra (una prueba lo tecleó en Claude).
func escribir(_ cadena: String, pid: pid_t) {
    for trozo in Array(cadena).chunked(16) {
        let unidades = Array(String(trozo).utf16)
        let pulsa = CGEvent(keyboardEventSource: nil, virtualKey: 0, keyDown: true)
        let suelta = CGEvent(keyboardEventSource: nil, virtualKey: 0, keyDown: false)
        unidades.withUnsafeBufferPointer { buffer in
            pulsa?.keyboardSetUnicodeString(stringLength: buffer.count, unicodeString: buffer.baseAddress)
            suelta?.keyboardSetUnicodeString(stringLength: buffer.count, unicodeString: buffer.baseAddress)
        }
        pulsa?.postToPid(pid)
        suelta?.postToPid(pid)
        usleep(20_000)
    }
}

extension Array {
    func chunked(_ tamano: Int) -> [[Element]] {
        stride(from: 0, to: count, by: tamano).map { Array(self[$0..<Swift.min($0 + tamano, count)]) }
    }
}

func tecla(_ combinacion: String, pid: pid_t) {
    var banderas = CGEventFlags()
    var codigo: CGKeyCode?
    for parte in combinacion.lowercased().split(separator: "+").map({ $0.trimmingCharacters(in: .whitespaces) }) {
        if let m = modificadores[parte] { banderas.insert(m) } else if let k = teclas[parte] { codigo = k }
        else { fallar("Tecla desconocida: \(parte). Usa nombres como return, tab, escape, left, cmd+s, shift+tab.") }
    }
    guard let codigo else { fallar("Falta la tecla principal en «\(combinacion)».") }
    let pulsa = CGEvent(keyboardEventSource: nil, virtualKey: codigo, keyDown: true)
    let suelta = CGEvent(keyboardEventSource: nil, virtualKey: codigo, keyDown: false)
    pulsa?.flags = banderas
    suelta?.flags = banderas
    pulsa?.postToPid(pid)
    usleep(30_000)
    suelta?.postToPid(pid)
}

/// Elige una opción de la barra de menús de la app («Archivo > Guardar»), por accesibilidad:
/// no hace falta que la app esté delante ni se abre el menú en pantalla.
func elegirMenu(_ pid: pid_t, _ ruta: String) -> Never {
    let pasos = ruta.split(separator: ">").map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty }
    guard !pasos.isEmpty else { fallar("Falta la ruta del menú, por ejemplo «Archivo > Guardar».") }
    guard let barra = atributo(AXUIElementCreateApplication(pid), kAXMenuBarAttribute as String) else {
        fallar("La app no tiene barra de menús que se pueda leer.")
    }
    var actual = barra as! AXUIElement
    func titulos(_ lista: [AXUIElement]) -> [String] { lista.compactMap { texto($0, kAXTitleAttribute as String) } }
    for (n, paso) in pasos.enumerated() {
        // Los elementos de un menú cuelgan de su AXMenu: se baja hasta ahí.
        var opciones = hijos(actual)
        if opciones.count == 1, texto(opciones[0], kAXRoleAttribute as String) == "AXMenu" { opciones = hijos(opciones[0]) }
        let bajo = paso.lowercased()
        let candidata = opciones.first { (texto($0, kAXTitleAttribute as String) ?? "").lowercased() == bajo }
            ?? opciones.first { (texto($0, kAXTitleAttribute as String) ?? "").lowercased().hasPrefix(bajo) }
        guard let elegida = candidata else {
            let hay = titulos(opciones).filter { !$0.isEmpty }.prefix(30).joined(separator: ", ")
            fallar("No hay «\(paso)» en el menú. Hay: \(hay).")
        }
        if n == pasos.count - 1 {
            if (atributo(elegida, kAXEnabledAttribute as String) as? Bool) == false {
                fallar("«\(paso)» está desactivado ahora mismo en el menú.")
            }
            let resultado = AXUIElementPerformAction(elegida, kAXPressAction as CFString)
            guard resultado == .success else { fallar("El menú no aceptó «\(paso)» (\(resultado.rawValue)).") }
        } else {
            actual = elegida
        }
    }
    usleep(250_000)
    salir(["hecho": true, "como": "menu"])
}

func accion() -> Never {
    guard AXIsProcessTrusted() else {
        fallar("Falta el permiso de Accesibilidad (Ajustes del Sistema → Privacidad y seguridad → Accesibilidad).")
    }
    guard let pidNumero = entero("pid"), let tipo = argumentos["tipo"] else { fallar("Faltan --pid y --tipo.") }
    let pid = pid_t(pidNumero)
    if tipo == "menu" { elegirMenu(pid, argumentos["texto"] ?? "") }
    let (raiz, _) = ventana(pid, entero("ventana"))
    guard let origen = marco(raiz) else { fallar("No se pudo leer el marco de la ventana.") }
    let deVerdad = banderas.contains("raton")
    var elemento: AXUIElement?
    if let ruta = argumentos["ruta"] {
        let encontrado = recorrer(ruta: ruta, desde: raiz)
        if let rol = argumentos["rol"], texto(encontrado, kAXRoleAttribute as String) != rol {
            fallar("El elemento cambió (ya no es \(rol)): vuelve a mirar la ventana con app_state.")
        }
        elemento = encontrado
    }
    /// Punto relativo a la ventana: el centro del elemento o x,y.
    func objetivo() -> CGPoint {
        if let elemento, let m = marco(elemento) { return CGPoint(x: m.midX - origen.minX, y: m.midY - origen.minY) }
        return punto(origen, real("x"), real("y"))
    }
    func global(_ local: CGPoint) -> CGPoint { CGPoint(x: origen.minX + local.x, y: origen.minY + local.y) }
    func numeroDeVentana() -> CGWindowID {
        guard let id = idDeVentana(pid: pid, marco: origen, titulo: texto(raiz, kAXTitleAttribute as String)) else {
            fallar("No se hizo nada: la ventana no está en pantalla (¿minimizada o en otro escritorio?).")
        }
        return id
    }
    var como = deVerdad ? "raton" : "fondo"
    var enfocada: CGWindowID?
    /// Ventana lista para recibir ratón en fondo: con el foco sólo para su app.
    func ventanaEnFondo() -> CGWindowID {
        let id = numeroDeVentana()
        avisarFoco(pid, id, tomar: true)
        enfocada = id
        return id
    }
    switch tipo {
    case "activar":
        activar(pid, raiz)
        como = "activar"
    case "pulsar":
        guard let elemento else { fallar("pulsar necesita un elemento.") }
        let nombre = argumentos["accion"] ?? kAXPressAction as String
        let resultado = AXUIElementPerformAction(elemento, nombre as CFString)
        guard resultado == .success else { fallar("El elemento no aceptó la acción \(nombre) (\(resultado.rawValue)).") }
        mostrarCursor(global(objetivo()), tipo: "clic")
        como = "AXPress"
    case "clic", "doble", "derecho":
        // Un menú abierto puede salir de la ventana: su opción se elige aunque caiga fuera.
        if tipo == "clic", !deVerdad, elemento == nil, let x = real("x"), let y = real("y"),
           let (desplegable, accion) = desplegableEn(pid, CGPoint(x: origen.minX + x, y: origen.minY + y)),
           AXUIElementPerformAction(desplegable, accion as CFString) == .success {
            mostrarCursor(CGPoint(x: origen.minX + x, y: origen.minY + y), tipo: "clic")
            como = "AXPress"
            break
        }
        let local = objetivo()
        // Un botón con AXPress se pulsa por accesibilidad: lo más fiable y sin ratón.
        if tipo == "clic", !deVerdad, let elemento, acciones(elemento).contains(kAXPressAction as String),
           AXUIElementPerformAction(elemento, kAXPressAction as CFString) == .success {
            como = "AXPress"
        } else if deVerdad {
            exigirDelante(global(local), pid, raiz)
            clicReal(global(local), boton: tipo == "derecho" ? .right : .left, veces: tipo == "doble" ? 2 : 1)
        } else {
            clicEnFondo(local, pid: pid, ventana: ventanaEnFondo(), derecho: tipo == "derecho",
                        veces: tipo == "doble" ? 2 : 1)
        }
        mostrarCursor(global(local), tipo: "clic")
    case "escribir", "tecla":
        // Las teclas van al proceso (postToPid); con el foco sólo para esa app, también a su
        // ventana «key» cuando no se dice en qué campo (un juego con flechas).
        if deVerdad { activar(pid, raiz) } else { _ = ventanaEnFondo() }
        if let elemento {
            AXUIElementSetAttributeValue(elemento, kAXFocusedAttribute as CFString, kCFBooleanTrue)
            usleep(80_000)
        }
        if tipo == "escribir" {
            guard let cadena = argumentos["texto"] else { fallar("Falta --texto.") }
            escribir(cadena, pid: pid)
        } else {
            guard let combinacion = argumentos["tecla"] else { fallar("Falta --tecla.") }
            tecla(combinacion, pid: pid)
        }
        como = deVerdad ? "teclado" : "teclado_en_fondo"
    case "pasar":
        // Pasar por encima (tooltips, menús que se abren al pasar): sólo movimiento, en fondo.
        let local = objetivo()
        if deVerdad {
            exigirDelante(global(local), pid, raiz)
            CGEvent(mouseEventSource: nil, mouseType: .mouseMoved, mouseCursorPosition: global(local), mouseButton: .left)?.post(tap: .cghidEventTap)
        } else {
            let id = ventanaEnFondo()
            for paso in 0..<3 {  // unos píxeles de movimiento, como una mano que llega
                eventoDeVentana(.mouseMoved, CGPoint(x: local.x - 2 + Double(paso), y: local.y), ventana: id, clics: 0)?.postToPid(pid)
                usleep(30_000)
            }
        }
        mostrarCursor(global(local), tipo: "mover")
        usleep(700_000)  // los tooltips tardan en salir
    case "valor":
        guard let elemento, let cadena = argumentos["texto"] else { fallar("valor necesita un elemento y --texto.") }
        let resultado = AXUIElementSetAttributeValue(elemento, kAXValueAttribute as CFString, cadena as CFString)
        guard resultado == .success else { fallar("El elemento no deja cambiar su valor (\(resultado.rawValue)).") }
        como = "AXValue"
    case "desplazar":
        let local = objetivo()
        let dx = real("dx") ?? 0, dy = real("dy") ?? 0
        if deVerdad {
            exigirDelante(global(local), pid, raiz)
            CGEvent(mouseEventSource: nil, mouseType: .mouseMoved, mouseCursorPosition: global(local), mouseButton: .left)?.post(tap: .cghidEventTap)
            let evento = CGEvent(scrollWheelEvent2Source: nil, units: .pixel, wheelCount: 2,
                                 wheel1: Int32(-dy), wheel2: Int32(-dx), wheel3: 0)
            evento?.location = global(local)
            evento?.post(tap: .cghidEventTap)
        } else {
            desplazarEnFondo(local, dx: dx, dy: dy, pid: pid, ventana: ventanaEnFondo())
        }
        mostrarCursor(global(local), tipo: "mover")
    case "arrastrar":
        let desde = objetivo()
        let hasta = punto(origen, real("x2"), real("y2"))
        if deVerdad {
            exigirDelante(global(desde), pid, raiz)
            exigirDelante(global(hasta), pid, raiz)
            CGEvent(mouseEventSource: nil, mouseType: .leftMouseDown, mouseCursorPosition: global(desde), mouseButton: .left)?.post(tap: .cghidEventTap)
            for paso in 1...12 {
                let t = Double(paso) / 12
                let lugar = CGPoint(x: global(desde).x + (global(hasta).x - global(desde).x) * t,
                                    y: global(desde).y + (global(hasta).y - global(desde).y) * t)
                CGEvent(mouseEventSource: nil, mouseType: .leftMouseDragged, mouseCursorPosition: lugar, mouseButton: .left)?.post(tap: .cghidEventTap)
                usleep(15_000)
            }
            CGEvent(mouseEventSource: nil, mouseType: .leftMouseUp, mouseCursorPosition: global(hasta), mouseButton: .left)?.post(tap: .cghidEventTap)
        } else {
            arrastrarEnFondo(desde, hasta, pid: pid, ventana: ventanaEnFondo())
        }
        mostrarCursor(global(desde), hasta: global(hasta), tipo: "mover")
    default:
        fallar("Acción desconocida: \(tipo).")
    }
    usleep(250_000)  // que la app pinte el resultado antes de volver a mirarla
    if let enfocada { avisarFoco(pid, enfocada, tomar: false) }
    salir(["hecho": true, "como": como])
}

switch orden {
case "permisos": permisos()
case "lista": listar()
case "estado": estado()
case "accion": accion()
case "cursor": cursor()
default: fallar("Orden desconocida: \(orden).")
}
