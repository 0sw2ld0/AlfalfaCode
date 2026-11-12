import Foundation

/// Oferta efímera de un turno. El código y los valores se quedan fuera del cierre.
public enum BrowserExportOffer: Equatable, Sendable {

    case ready(id: String, path: String, steps: Int, variables: [Variable])
    case manual(reason: String)

    // MARK: - Tipos

    public struct Variable: Equatable, Sendable, Identifiable {
        public let id: String
        public let step: Int
        public let use: String
        public let role: String?

        fileprivate init?(value: [String: Any], steps: Int) {
            guard let key = value["clave"] as? String,
                  key.range(of: #"\AALFALFA_E2E_(BASE_URL|(RUTA|NOMBRE|VALOR|TEXTO)_[0-9]{1,2})\z"#,
                            options: .regularExpression) != nil,
                  let step = value["paso"] as? Int, (1...steps).contains(step),
                  let use = value["uso"] as? String, !use.isEmpty, use.count <= 80 else { return nil }
            self.id = key
            self.step = step
            self.use = use
            self.role = (value["rol"] as? String).map { String($0.prefix(16)) }
        }
    }

    // MARK: - Propiedades

    public var id: String? {
        if case let .ready(id, _, _, _) = self { return id }
        return nil
    }

    // MARK: - Inicializadores

    public init?(value: Any?) {
        guard let object = value as? [String: Any] else { return nil }
        if let reason = object["motivo"] as? String, !reason.isEmpty {
            self = .manual(reason: String(reason.prefix(240)))
            return
        }
        guard let id = object["id"] as? String,
              id.range(of: #"\A[0-9a-f]{32}\z"#, options: .regularExpression) != nil,
              let path = object["ruta"] as? String,
              path.range(of: #"\Atests/e2e/[A-Za-z0-9][A-Za-z0-9_.-]{0,79}\.spec\.ts\z"#,
                         options: .regularExpression) != nil,
              let steps = object["pasos"] as? Int, (1...60).contains(steps),
              let values = object["variables"] as? [[String: Any]], (1...(2 * steps)).contains(values.count)
        else { return nil }
        let variables = values.compactMap { Variable(value: $0, steps: steps) }
        guard variables.count == values.count, Set(variables.map(\.id)).count == values.count else { return nil }
        self = .ready(id: id, path: path, steps: steps, variables: variables)
    }
}
