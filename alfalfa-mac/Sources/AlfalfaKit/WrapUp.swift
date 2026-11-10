import Foundation

/// Caja «siguiente paso» al final de un turno: resumen y, si el usuario decide, pregunta y botones.
/// También lleva las sesiones que propone el agente; sin `wrap_up`, el resumen queda vacío.
/// `suggestion` es la frase sugerida, como la de la app de Codex: el siguiente mensaje del usuario,
/// en gris en el cuadro vacío. Nunca se envía sola: el usuario la acepta con Tab y la manda él.
public struct WrapUp: Equatable, Sendable {

    // MARK: - Propiedades

    public var recap: String
    public var ask: String?
    public var options: [String]
    public var sessions: [SessionProposal]
    public var suggestion: String?

    // MARK: - Inicializadores

    public init(recap: String, ask: String? = nil, options: [String] = [], sessions: [SessionProposal] = [],
                suggestion: String? = nil) {
        self.recap = recap
        self.ask = ask
        self.options = options
        self.sessions = sessions
        self.suggestion = suggestion
    }

    /// El `cierre` de `turno_terminado` o del historial; nil si no trae ni resumen ni sesiones.
    public init?(cierre value: Any?) {
        guard let object = value as? [String: Any] else { return nil }
        let recap = Self.text(object["resumen"]) ?? ""
        let sessions = SessionProposal.list(object["sesiones"])
        guard !recap.isEmpty || !sessions.isEmpty else { return nil }
        let ask = recap.isEmpty ? nil : Self.text(object["pregunta"])
        let options = ask == nil ? [] : (object["opciones"] as? [Any] ?? []).compactMap { Self.text($0) }.prefix(2)
        // Una sola línea, como la manda el motor: sin saltos que la conviertan en varios párrafos.
        let suggestion = recap.isEmpty ? nil : Self.text(object["sugerencia"]).map {
            $0.split(whereSeparator: \.isWhitespace).joined(separator: " ")
        }
        self.init(recap: recap, ask: ask, options: Array(options), sessions: sessions, suggestion: suggestion)
    }

    // MARK: - Métodos privados

    private static func text(_ value: Any?) -> String? {
        guard let string = value as? String, !string.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            return nil
        }
        return string
    }
}
