import Foundation

/// `--reportar`: si el reporte llegó al servicio Alfalfa o hay que escribir a `support`.
public struct ReportResult: Equatable, Sendable {
    public static let defaultSupport = "soporte@cuycoders.dev"

    public let sent: Bool
    public let id: Int?
    public let support: String
    public let reason: String?

    public init(sent: Bool, id: Int? = nil, support: String = ReportResult.defaultSupport, reason: String? = nil) {
        self.sent = sent
        self.id = id
        self.support = support
        self.reason = reason
    }

    /// La línea `{"tipo": "reporte", "enviado": …}` del motor; nil si es otro tipo o falta `enviado`.
    init?(kind: String, object: [String: Any]) {
        guard kind == "reporte", let sent = object["enviado"] as? Bool else { return nil }
        let support = (object["soporte"] as? String).flatMap { $0.contains("@") ? $0 : nil }
        let reason = (object["motivo"] as? String).flatMap { $0.isEmpty ? nil : $0 }
        self.init(sent: sent, id: object["id"] as? Int, support: support ?? Self.defaultSupport, reason: reason)
    }
}
