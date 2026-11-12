import AlfalfaKit
import AppKit
import SwiftUI
import WebKit

/// App propia de depuración. Sólo instancia WebKit y la tarjeta; nunca AppModel ni sesiones del usuario.
@main
struct InterfaceDemo {
    @MainActor static func main() {
        let app = NSApplication.shared
        app.setActivationPolicy(.accessory)
        let engine = BrowserEngine()
        let lines = AsyncStream<String> { continuation in
            Task.detached {
                while let line = readLine() { continuation.yield(line) }
                continuation.finish()
            }
        }
        Task { @MainActor in
            for await line in lines {
                do {
                    let object = try JSONSerialization.jsonObject(with: Data(line.utf8)) as? [String: Any] ?? [:]
                    let action = object["accion"] as? String ?? ""
                    let fields = object["campos"] as? [String: Any] ?? [:]
                    let reply: BrowserReply
                    if action == "demo_cargar" {
                        _ = await engine.perform(BrowserRequest(id: "viewport", action: "viewport",
                                                              fields: ["ancho": 760, "alto": 400,
                                                                       "sitios": fields["sitios"] ?? [:]]))
                        engine.webView.loadHTMLString(fields["html"] as? String ?? "",
                                                      baseURL: URL(string: "https://demo.alfalfa.test/"))
                        try await Task.sleep(nanoseconds: 600_000_000)
                        reply = .success("demo", ["url": "https://demo.alfalfa.test/"])
                    } else if action == "demo_tarjeta" {
                        let proof = InterfaceProof(value: fields["prueba"])!
                        try await card(proof, path: fields["ruta"] as! String)
                        reply = .success("demo", ["tarjeta": true])
                    } else {
                        reply = await engine.perform(BrowserRequest(id: "demo", action: action, fields: fields))
                    }
                    print(reply.json)
                    fflush(stdout)
                } catch {
                    print(BrowserReply.failure("demo", error.localizedDescription).json)
                    fflush(stdout)
                }
            }
            engine.close()
            app.terminate(nil)
        }
        app.run()
    }

    @MainActor private static func card(_ proof: InterfaceProof, path: String) async throws {
        let view = VStack(alignment: .leading, spacing: 20) {
            Text("Botón actualizado de rojo a verde.").font(.system(size: 13.5)).foregroundStyle(Theme.secondary)
            InterfaceProofCard(proof: proof)
        }
        .padding(24).frame(width: 880).background(Theme.background).environment(\.colorScheme, .dark)
        let host = NSHostingView(rootView: view)
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 880, height: 450),
                              styleMask: [.borderless], backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        window.contentView = host
        window.orderBack(nil)
        defer { window.close() }
        try await Task.sleep(nanoseconds: 800_000_000)
        host.layoutSubtreeIfNeeded()
        let bitmap = host.bitmapImageRepForCachingDisplay(in: host.bounds)!
        host.cacheDisplay(in: host.bounds, to: bitmap)
        try bitmap.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: path))
    }
}
