import Foundation

/// Revisión voluntaria del diff de un turno, recibida por el mismo proceso de la sesión.
public struct CodeReview: Equatable, Sendable {
    public struct Finding: Equatable, Sendable, Identifiable {
        public var path: String
        public var line: Int
        public var priority: Int
        public var title: String
        public var detail: String
        public var id: String { "\(path):\(line):\(title)" }
    }

    public var turn: String
    public var state: String
    public var author: String
    public var model: String?
    public var recap: String?
    public var reason: String?
    public var findings: [Finding]
    public var omitted: [String]
    public var cost: Double?
    public var estimatedCost: Bool
    public var sessionTokens: Int?
    public var sessionCost: Double?

    public var canRequest: Bool { ["disponible", "fallida", "cancelada", "sin_presupuesto"].contains(state) }
    public var canCorrect: Bool { state == "terminada" && !findings.isEmpty }
    public var correction: String {
        "Corrige los hallazgos de la revisión del turno \(turn), que ya están en este hilo. "
            + "Comprueba cada hallazgo antes de cambiar el código y pasa las pruebas pertinentes."
    }

    public static func identity(of model: String) -> String {
        var name = String(model.split(separator: "/").last ?? "").lowercased()
        while let suffix = [":cloud", "-cloud", "-latest"].first(where: { name.hasSuffix($0) }) {
            name.removeLast(suffix.count)
        }
        return name
    }

    public func candidates(in models: [ModelInfo]) -> [ModelInfo] {
        models.filter { $0.missingKey == nil && !$0.name.lowercased().contains("embed")
            && Self.identity(of: $0.name) != Self.identity(of: author) }
    }

    public init?(value: Any?) {
        guard let object = value as? [String: Any], let turn = object["turno"] as? String, !turn.isEmpty,
              let state = object["estado"] as? String, let author = object["autor"] as? String else { return nil }
        self.turn = turn
        self.state = state
        self.author = author
        model = object["modelo"] as? String
        recap = object["resumen"] as? String
        reason = object["motivo"] as? String
        omitted = object["omitidos"] as? [String] ?? []
        cost = (object["coste"] as? NSNumber)?.doubleValue
        estimatedCost = object["coste_estimado"] as? Bool ?? false
        sessionTokens = object["tokens_sesion"] as? Int
        sessionCost = (object["coste_sesion"] as? NSNumber)?.doubleValue
        findings = (object["hallazgos"] as? [[String: Any]] ?? []).prefix(10).compactMap { item in
            guard let path = item["ruta"] as? String, !path.isEmpty, !path.hasPrefix("/"),
                  !path.split(separator: "/").contains(".."), let line = item["linea"] as? Int, line > 0,
                  let priority = item["prioridad"] as? Int, (0...3).contains(priority),
                  let title = item["titulo"] as? String, let detail = item["detalle"] as? String else { return nil }
            return Finding(path: path, line: line, priority: priority, title: title, detail: detail)
        }
    }
}
