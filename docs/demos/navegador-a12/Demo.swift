import AlfalfaKit
import AppKit
import SwiftUI
import WebKit

/// Host de depuración. No importa AppModel, proveedores, conversaciones ni credenciales.
@MainActor
final class Demo: ObservableObject {
    let engine = BrowserEngine()
    @Published var status = "Esperando la página ficticia"
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

    private func finish() {
        engine.close()
        NSApplication.shared.terminate(nil)
    }

    private func ownFile(_ name: String) -> URL? {
        guard name.range(of: #"^[A-Za-z0-9][A-Za-z0-9._-]*\.png$"#, options: .regularExpression) != nil,
              let root = environment["ALFALFA_DEMO_ROOT"] else { return nil }
        return URL(fileURLWithPath: root).appendingPathComponent(name)
    }

    private func receive(_ line: String) async -> String {
        if let object = try? JSONSerialization.jsonObject(with: Data(line.utf8)) as? [String: Any],
           object["tipo"] as? String == "demo" {
            if let label = object["rotulo"] as? String { status = label }
            if let name = object["ventana"] as? String, let url = ownFile(name) {
                // Sólo la ventana del proceso de depuración, sin capturar el escritorio.
                try? await Task.sleep(nanoseconds: 200_000_000)
                if let view = NSApplication.shared.windows.first(where: { $0.title == "Lecturas A12" })?.contentView,
                   let bitmap = view.bitmapImageRepForCachingDisplay(in: view.bounds) {
                    view.cacheDisplay(in: view.bounds, to: bitmap)
                    if let png = bitmap.representation(using: .png, properties: [:]) {
                        do {
                            try png.write(to: url, options: .withoutOverwriting)
                            return #"{"ok":true}"#
                        } catch { }
                    }
                }
                return #"{"ok":false,"error":"No se pudo capturar la ventana propia."}"#
            }
            return #"{"ok":true}"#
        }
        guard case let .browser(request)? = AlfalfaMessage.decode(line: line),
              ["estado", "abrir", "leer", "pulsar", "captura"].contains(request.action) else {
            return #"{"ok":false,"error":"Petición fuera del ensayo."}"#
        }
        if request.action == "abrir" {
            guard let address = request.string("url"), address == environment["ALFALFA_DEMO_URL"],
                  let url = URL(string: address), url.scheme == "http", url.host == "127.0.0.1",
                  let port = url.port, port > 1024, port != 8765 else {
                return #"{"ok":false,"error":"El ensayo sólo abre su página local."}"#
            }
        }
        if request.action == "captura" {
            guard let path = request.string("ruta"), let url = ownFile(URL(fileURLWithPath: path).lastPathComponent),
                  url.path == path else {
                return #"{"ok":false,"error":"Captura fuera de la carpeta de ensayo."}"#
            }
        }
        let reply = await engine.perform(request)
        return AlfalfaCommand.browserReply(reply).line.trimmingCharacters(in: .newlines)
    }
}

struct Pagina: NSViewRepresentable {
    let webView: WKWebView

    func makeNSView(context: Context) -> WKWebView { webView }

    func updateNSView(_ view: WKWebView, context: Context) { }
}

@main
@MainActor
struct LecturasA12: App {
    @StateObject private var demo = Demo()

    var body: some Scene {
        WindowGroup("Lecturas A12") {
            VStack(spacing: 0) {
                Text(demo.status)
                    .font(.system(size: 16, weight: .semibold))
                    .padding(12)
                    .frame(maxWidth: .infinity, alignment: .leading)
                Pagina(webView: demo.engine.webView)
            }
            .frame(minWidth: 740, minHeight: 520)
            .task { demo.start() }
        }
    }
}
