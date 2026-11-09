import AlfalfaKit
import Foundation

enum Presentation {
    static let efforts: [(value: String, label: String)] = [
        ("low", "Bajo"), ("medium", "Medio"), ("high", "Alto"), ("xhigh", "Muy alto"), ("max", "Máximo")
    ]
    static let defaultModel = "deepseek-v4.1-flash:cloud"

    static func effortLabel(_ value: String) -> String { efforts.first { $0.value == value }?.label ?? value }

    static func shortModel(_ name: String) -> String { ModelProvider.shortName(name) }

    /// El proveedor por el prefijo del modelo, como en el motor: sin prefijo es Ollama.
    static func provider(of name: String) -> String { ModelProvider.id(of: name) }

    static func abbreviate(_ path: String) -> String {
        let home = NSHomeDirectory()
        return path.hasPrefix(home) ? "~" + path.dropFirst(home.count) : path
    }

    /// Estado guardado en disco de una sesión que esta app no tiene abierta.
    static func stored(_ state: String) -> (label: String, tone: SidebarRow.Tone) {
        switch state {
        case "completed": return ("completada", .done)
        case "failed": return ("fallida", .problem)
        case "blocked": return ("bloqueada", .problem)
        case "corrupta": return ("ilegible", .problem)
        // Una bifurcación recién hecha: espera su primer mensaje.
        case "created": return ("lista", .done)
        default: return ("Interrumpida · se puede reanudar", .attention)
        }
    }

    /// Se puede reanudar sin mensaje: sólo lo que se quedó a medias.
    static func resumable(_ state: String) -> Bool { !["completed", "corrupta", "created"].contains(state) }
}

/// Lo que enseña el aviso de «Buscar actualización».
enum UpdateCheck: Equatable {
    case checking
    case available(UpdateInfo)
    case updating
    case message(title: String, text: String)
}
