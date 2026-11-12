import Foundation

/// Respuesta de `alfalfa --cuybrowser-estado --json` y de la descarga explícita.
public struct CuyBrowserPreparation: Equatable, Sendable {
    public enum Status: String, Sendable {
        case available = "disponible"
        case needsChromium = "sin_chromium"
        case missing = "sin_cuybrowser"
        case error
    }

    public let status: Status
    public let source: String?
    public let browser: String?
    public let message: String?

    init?(object: [String: Any]) {
        guard let value = object["estado"] as? String, let status = Status(rawValue: value) else { return nil }
        self.status = status
        source = object["origen"] as? String
        browser = object["navegador"] as? String
        message = object["mensaje"] as? String
    }
}
