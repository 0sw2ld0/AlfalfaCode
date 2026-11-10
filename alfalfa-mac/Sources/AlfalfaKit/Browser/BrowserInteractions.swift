import AppKit
import Foundation
import UniformTypeIdentifiers

// MARK: - Espera, teclas, puntero y archivos

extension BrowserEngine {
    func waitForPage(_ request: BrowserRequest) async throws -> [String: Any] {
        let milliseconds = (request.fields["tiempo_ms"] as? NSNumber)?.doubleValue ?? 10000
        let deadline = Date().addingTimeInterval(min(30000, max(1, milliseconds)) / 1000)
        repeat {
            try Task.checkCancellation()
            let matched: Bool
            if let fragment = request.string("url_contiene") {
                matched = webView.url?.absoluteString.contains(fragment) == true
            } else if request.bool("carga") {
                matched = !webView.isLoading && webView.url != nil
            } else {
                let result = try await evaluate(BrowserScripts.waitCondition, request.fields)
                matched = result["cumplida"] as? Bool == true
            }
            if matched { return ["cumplida": true] }
            if isClosed { throw BrowserFailure("El navegador de la sesión se cerró.") }
            try await Task.sleep(nanoseconds: 50_000_000)
        } while Date() < deadline
        throw BrowserFailure("La condición no se cumplió dentro del tiempo de espera.")
    }

    func pressKey(_ request: BrowserRequest) async throws -> [String: Any] {
        let keys: [String: (UInt16, String)] = ["Enter": (36, "\r"), "Escape": (53, "\u{1b}"), "Tab": (48, "\t"),
            "ArrowLeft": (123, "\u{f702}"), "ArrowRight": (124, "\u{f703}"),
            "ArrowDown": (125, "\u{f701}"), "ArrowUp": (126, "\u{f700}")]
        guard let name = request.string("tecla"), let key = keys[name], let window = webView.window else {
            throw BrowserFailure("La tecla no está admitida o el navegador no tiene ventana.")
        }
        let result = try await evaluate(BrowserScripts.keyTarget, request.fields)
        if result["permiso"] != nil { return result }
        window.makeFirstResponder(webView)
        for type in [NSEvent.EventType.keyDown, .keyUp] {
            guard let event = NSEvent.keyEvent(with: type, location: .zero, modifierFlags: [],
                                               timestamp: ProcessInfo.processInfo.systemUptime,
                                               windowNumber: window.windowNumber, context: nil,
                                               characters: key.1, charactersIgnoringModifiers: key.1,
                                               isARepeat: false, keyCode: key.0) else { continue }
            if type == .keyDown { webView.keyDown(with: event) } else { webView.keyUp(with: event) }
        }
        try await Task.sleep(nanoseconds: 150_000_000)
        if webView.isLoading {
            _ = await waitUntil(timeout: 15) { [unowned self] in !webView.isLoading }
        }
        try checkBlocked()
        return ["tecla": name]
    }

    func hover(_ request: BrowserRequest) async throws -> [String: Any] {
        try await BrowserDeadline.run(seconds: 5) { try await self.hoverNow(request) }
    }

    private func hoverNow(_ request: BrowserRequest) async throws -> [String: Any] {
        // Al abrir el panel, SwiftUI necesita un turno para sacar el WKWebView de su ventana aparcada.
        let visible = await waitUntil(timeout: 1) { [unowned self] in
            guard let window = webView.window, window.isVisible else { return false }
            return NSScreen.screens.contains { $0.frame.intersects(window.frame) }
        }
        guard visible else {
            throw BrowserFailure("Para pasar el puntero, el panel del navegador debe estar visible en la app.")
        }
        let result = try await evaluate(BrowserScripts.hoverTarget, request.fields)
        try Task.checkCancellation()
        guard let x = result["x"] as? Double, let y = result["y"] as? Double, let window = webView.window else {
            throw BrowserFailure("No se pudo localizar el elemento para pasar el puntero.")
        }
        if !window.isKeyWindow {
            window.makeKeyAndOrderFront(nil)
            NSApp.activate()
            _ = await waitUntil(timeout: 1) { window.isKeyWindow }
        }
        let local = NSPoint(x: x, y: webView.isFlipped ? y : webView.bounds.height - y)
        try Task.checkCancellation()
        try BrowserPointer.move(in: webView, to: webView.convert(local, to: nil), window: window)
        try await Task.sleep(nanoseconds: 100_000_000)
        return ["elemento": result["elemento"] ?? ""]
    }

    func upload(_ request: BrowserRequest) async throws -> [String: Any] {
        guard let rootPath = request.string("proyecto"), rootPath.hasPrefix("/") else {
            throw BrowserFailure("La subida necesita la raíz del proyecto aprobada por el motor.")
        }
        let root = URL(fileURLWithPath: rootPath).resolvingSymlinksInPath().standardizedFileURL
        let paths = request.strings("rutas")
        guard (1...10).contains(paths.count) else { throw BrowserFailure("Indica entre 1 y 10 archivos aprobados.") }
        var total = 0
        var files: [[String: Any]] = []
        for path in paths {
            let url = URL(fileURLWithPath: path).standardizedFileURL
            let data = try BrowserProjectFile.read(path: url, root: root, remaining: 20 * 1024 * 1024 - total)
            total += data.count
            let mime = UTType(filenameExtension: url.pathExtension)?.preferredMIMEType ?? "application/octet-stream"
            files.append(["nombre": url.lastPathComponent, "base64": data.base64EncodedString(), "tipo": mime])
        }
        var fields = request.fields
        fields["archivos"] = files
        return try await evaluate(BrowserScripts.uploadFiles, fields)
    }
}
