import AlfalfaKit
import AppKit
import SwiftUI
import WebKit

/// Sólo el motor WebKit y la tarjeta de exportación reales; sin una sesión ni proveedores de la app.
@MainActor
final class Demo: ObservableObject {
    let engine = BrowserEngine()
    @Published var offer: BrowserExportOffer?
    private var command: String?
    private var started = false
    private let environment = ProcessInfo.processInfo.environment

    func start() {
        guard !started else { return }
        started = true
        Task.detached { [self] in
            while let line = readLine() {
                let reply = await receive(line)
                FileHandle.standardOutput.write(Data((reply + "\n").utf8))
            }
            await finish()
        }
    }

    func save(id: String, path: String) {
        command = AlfalfaCommand.exportBrowserTest(id: id, path: path).line
    }

    private func finish() {
        engine.close()
        NSApplication.shared.terminate(nil)
    }

    private var window: NSWindow? { NSApplication.shared.windows.first { $0.title == "Exportar A11" } }

    private func reply(_ object: [String: Any]) -> String {
        guard let data = try? JSONSerialization.data(withJSONObject: object) else { return #"{"ok":false}"# }
        return String(decoding: data, as: UTF8.self)
    }

    private func receive(_ line: String) async -> String {
        guard let object = try? JSONSerialization.jsonObject(with: Data(line.utf8)) as? [String: Any]
        else { return reply(["ok": false]) }
        switch object["tipo"] as? String {
        case "oferta":
            offer = WrapUp(cierre: object["cierre"])?.browserExport
            return reply(["ok": offer?.id != nil])
        case "gesto":
            return await pressSave()
        case "captura_tarjeta":
            return reply(["ok": captureCard()])
        default:
            return await browse(line)
        }
    }

    private func browse(_ line: String) async -> String {
        guard case let .browser(request)? = AlfalfaMessage.decode(line: line),
              ["estado", "abrir", "leer", "escribir", "pulsar", "esperar", "captura"].contains(request.action)
        else { return reply(["ok": false, "error": "Acción fuera del ensayo."]) }
        if request.action == "abrir" {
            guard let address = request.string("url"), address == environment["ALFALFA_DEMO_URL"],
                  let url = URL(string: address), url.scheme == "http", url.host == "127.0.0.1",
                  let port = url.port, port > 1024, port != 8765 else { return reply(["ok": false]) }
        }
        if request.action == "captura" {
            guard let root = environment["ALFALFA_DEMO_ROOT"], request.string("ruta") == root + "/webkit.png"
            else { return reply(["ok": false]) }
        }
        let result = await engine.perform(request)
        return AlfalfaCommand.browserReply(result).line.trimmingCharacters(in: .newlines)
    }

    private func captureCard() -> Bool {
        guard let root = environment["ALFALFA_DEMO_ROOT"], let view = window?.contentView,
              let bitmap = view.bitmapImageRepForCachingDisplay(in: view.bounds) else { return false }
        view.cacheDisplay(in: view.bounds, to: bitmap)
        guard let data = bitmap.representation(using: .png, properties: [:]) else { return false }
        do {
            try data.write(to: URL(fileURLWithPath: root).appendingPathComponent("tarjeta.png"),
                           options: .withoutOverwriting)
            return true
        } catch {
            return false
        }
    }

    /// Clic de prueba enviado sólo a la ventana de este helper, sin mover el puntero del sistema.
    private func pressSave() async -> String {
        guard command == nil, offer?.id != nil, let window, let content = window.contentView
        else { return reply(["ok": false]) }
        NSApplication.shared.activate()
        window.makeKeyAndOrderFront(nil)
        try? await Task.sleep(nanoseconds: 100_000_000)
        // Centro del botón nativo en la tarjeta del helper, en puntos de su vista (no de la pantalla).
        let bounds = content.bounds
        let y = content.isFlipped ? bounds.minY + 148 : bounds.maxY - 148
        let point = content.convert(NSPoint(x: bounds.maxX - 130, y: y), to: nil)
        for (index, kind) in [NSEvent.EventType.leftMouseDown, .leftMouseUp].enumerated() {
            guard let event = NSEvent.mouseEvent(with: kind, location: point, modifierFlags: [],
                                                timestamp: ProcessInfo.processInfo.systemUptime,
                                                windowNumber: window.windowNumber, context: nil,
                                                eventNumber: index + 1, clickCount: 1, pressure: index == 0 ? 1 : 0)
            else { return reply(["ok": false]) }
            NSApplication.shared.postEvent(event, atStart: false)
        }
        for _ in 0..<30 {
            if let command { return reply(["ok": true, "comando": command]) }
            try? await Task.sleep(nanoseconds: 50_000_000)
        }
        return reply(["ok": false, "error": "El botón de la ventana propia no respondió."])
    }
}

struct Pagina: NSViewRepresentable {
    let webView: WKWebView

    func makeNSView(context: Context) -> WKWebView { webView }

    func updateNSView(_ view: WKWebView, context: Context) { }
}

@main
@MainActor
struct ExportarA11: App {
    @StateObject private var demo = Demo()

    var body: some Scene {
        WindowGroup("Exportar A11") {
            VStack(spacing: 0) {
                if let offer = demo.offer {
                    BrowserExportCard(offer: offer, blocked: nil, save: demo.save(id:path:))
                        .padding(14)
                }
                Pagina(webView: demo.engine.webView)
            }
            .frame(minWidth: 730, minHeight: 460)
            .background(Theme.background)
            .task { demo.start() }
        }
        .defaultSize(width: 900, height: 520)
    }
}
