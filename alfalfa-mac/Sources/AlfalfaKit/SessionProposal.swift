import Foundation

/// Una sesión que propone el agente con `propose_session`, como las tarjetas «Nueva sesión» de
/// Claude Code: un título y el encargo completo, porque la sesión nueva no ve la conversación.
public struct SessionProposal: Equatable, Identifiable, Sendable {

    // MARK: - Propiedades

    public var title: String
    public var task: String
    public var id: String { title }

    // MARK: - Inicializadores

    public init(title: String, task: String) {
        self.title = title
        self.task = task
    }

    // MARK: - Métodos públicos

    /// Las propuestas del cierre de un turno (`cierre.sesiones`), sin repetir título y como mucho tres.
    public static func list(_ value: Any?) -> [SessionProposal] {
        var seen = Set<String>()
        let rows = (value as? [Any] ?? []).compactMap { $0 as? [String: Any] }
        return rows.compactMap { row -> SessionProposal? in
            guard let title = clean(row["titulo"]), let task = clean(row["encargo"]),
                  seen.insert(title).inserted else { return nil }
            return SessionProposal(title: title, task: task)
        }
        .prefix(3).map { $0 }
    }

    /// Lo que manda «Hacerlo aquí»: el agente ya sabe qué propuso, basta con el título.
    public static func doItHere(_ proposal: SessionProposal) -> String {
        "Hazlo aquí: «\(proposal.title)»."
    }

    // MARK: - Métodos privados

    private static func clean(_ value: Any?) -> String? {
        guard let text = (value as? String)?.trimmingCharacters(in: .whitespacesAndNewlines), !text.isEmpty else {
            return nil
        }
        return text
    }
}
