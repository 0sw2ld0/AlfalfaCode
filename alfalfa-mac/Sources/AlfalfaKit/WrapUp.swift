import Foundation

/// Caja «siguiente paso» al final de un turno: resumen y, si el usuario decide, pregunta y botones.
/// También lleva las sesiones que propone el agente; sin `wrap_up`, el resumen queda vacío.
public struct WrapUp: Equatable, Sendable {

    // MARK: - Propiedades

    public var recap: String
    public var ask: String?
    public var options: [String]
    public var sessions: [SessionProposal]

    // MARK: - Inicializadores

    public init(recap: String, ask: String? = nil, options: [String] = [], sessions: [SessionProposal] = []) {
        self.recap = recap
        self.ask = ask
        self.options = options
        self.sessions = sessions
    }

    /// El `cierre` de `turno_terminado` o del historial; nil si no trae ni resumen ni sesiones.
    public init?(cierre value: Any?) {
        guard let object = value as? [String: Any] else { return nil }
        let recap = Self.text(object["resumen"]) ?? ""
        let sessions = SessionProposal.list(object["sesiones"])
        guard !recap.isEmpty || !sessions.isEmpty else { return nil }
        let ask = recap.isEmpty ? nil : Self.text(object["pregunta"])
        let options = ask == nil ? [] : (object["opciones"] as? [Any] ?? []).compactMap { Self.text($0) }.prefix(2)
        self.init(recap: recap, ask: ask, options: Array(options), sessions: sessions)
    }

    // MARK: - Métodos privados

    private static func text(_ value: Any?) -> String? {
        guard let string = value as? String, !string.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            return nil
        }
        return string
    }
}
