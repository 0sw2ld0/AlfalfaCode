import Foundation
import WebKit

/// Diagnósticos declarados por la página: datos no fiables, con memoria acotada por navegación.
@MainActor
final class BrowserDiagnostics: NSObject {

    // MARK: - Constantes

    static let handler = "alfalfaDiagnostics"
    static let capacity = 200

    // MARK: - Propiedades privadas

    private var console: [[String: Any]] = []
    private var network: [[String: Any]] = []
    private var consoleDropped = 0
    private var networkDropped = 0

    // MARK: - Métodos

    func reset() {
        console.removeAll(keepingCapacity: true)
        network.removeAll(keepingCapacity: true)
        consoleDropped = 0
        networkDropped = 0
    }

    func result(network wantsNetwork: Bool, limit: Int) -> [String: Any] {
        let entries = wantsNetwork ? network : console
        let dropped = wantsNetwork ? networkDropped : consoleDropped
        let last = Array(entries.suffix(max(1, min(Self.capacity, limit))))
        return [wantsNetwork ? "peticiones" : "mensajes": last,
                "recortado": dropped > 0 || last.count < entries.count,
                "descartados": dropped + entries.count - last.count]
    }

    func receive(_ body: Any) {
        guard let entry = body as? [String: Any], let kind = entry["tipo"] as? String else { return }
        if kind == "consola" {
            let levels = ["log", "info", "warn", "error", "debug", "exception", "unhandledrejection"]
            guard let level = entry["nivel"] as? String, levels.contains(level),
                  let text = entry["texto"] as? String else { return }
            console.append(["nivel": level, "texto": String(text.prefix(2000))])
            if console.count > Self.capacity {
                console.removeFirst()
                consoleDropped += 1
            }
        } else if kind == "red" {
            guard let url = entry["url"] as? String, let method = entry["metodo"] as? String else { return }
            // No se admite el resto del objeto: jamás cuerpos ni cabeceras del mensaje de la página.
            network.append(["url": String(url.prefix(4000)), "metodo": String(method.prefix(16)),
                            "estado": max(0, min(599, (entry["estado"] as? Int) ?? 0)),
                            "duracion_ms": max(0, min(3_600_000, (entry["duracion_ms"] as? Double) ?? 0)),
                            "fallo": (entry["fallo"] as? Bool) ?? false])
            if network.count > Self.capacity {
                network.removeFirst()
                networkDropped += 1
            }
        }
    }
}

// MARK: - WKScriptMessageHandler

extension BrowserDiagnostics: WKScriptMessageHandler {
    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
        guard message.frameInfo.isMainFrame else { return }
        receive(message.body)
    }
}
