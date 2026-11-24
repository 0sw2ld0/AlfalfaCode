import Foundation

/// Lo que hace un evento de un nodo (`events` en el perfil `ui`): devolver la acción al agente, cambiar el estado
/// de la pantalla o abrir un enlace.
public enum ScreenAction: Equatable, Sendable {
    /// `send`: vuelve al agente con los valores de esas claves del estado (todas, si `keys` es nil).
    case send(name: String, keys: [String]?)
    /// `set`: cambia el estado sin llamar al agente.
    case set([String: ScreenValue])
    /// `open_url`: sólo `https:`.
    case openURL(URL)

    // MARK: - Métodos públicos

    /// Las acciones de un evento: una o una lista. Lo que no se entiende se descarta.
    static func list(_ value: Any?) -> [ScreenAction] {
        let rows = value as? [[String: Any]] ?? (value as? [String: Any]).map { [$0] } ?? []
        return rows.compactMap(action)
    }

    // MARK: - Métodos privados

    private static func action(_ row: [String: Any]) -> ScreenAction? {
        if let name = row["send"] as? String {
            return .send(name: name, keys: row["with"] as? [String])
        }
        if let values = row["set"] as? [String: Any] {
            return .set(values.mapValues { ScreenValue(json: $0) })
        }
        if let text = row["open_url"] as? String, let url = URL(string: text), url.scheme == "https" {
            return .openURL(url)
        }
        return nil
    }
}

/// Un nodo de una pantalla: un componente del catálogo con sus props, sus hijos (ids) y sus eventos.
public struct ScreenNode: Equatable, Sendable, Identifiable {

    // MARK: - Propiedades

    public var id: String
    public var component: String
    public var props: [String: ScreenValue]
    public var children: [String]
    public var events: [String: [ScreenAction]]

    // MARK: - Inicializadores

    public init(id: String, component: String, props: [String: ScreenValue] = [:], children: [String] = [],
                events: [String: [ScreenAction]] = [:]) {
        self.id = id
        self.component = component
        self.props = props
        self.children = children
        self.events = events
    }

    /// Un nodo de `pacai.ui/1`; nil sin `id` o sin `component`.
    init?(json row: [String: Any]) {
        guard let id = row["id"] as? String, !id.isEmpty, let component = row["component"] as? String else {
            return nil
        }
        self.init(id: id, component: component, props: ScreenValue.dictionary(row["props"]),
                  children: row["children"] as? [String] ?? [],
                  events: (row["events"] as? [String: Any] ?? [:]).mapValues(ScreenAction.list))
    }

    // MARK: - Métodos públicos

    /// La prop `key` con su enlace al estado ya resuelto: `{"$": "clave"}` lee `state["clave"]`.
    public func prop(_ key: String, in state: [String: ScreenValue]) -> ScreenValue? {
        guard let value = props[key] else { return nil }
        if case let .object(fields) = value, fields.count == 1, let binding = fields["$"]?.string {
            return state[binding]
        }
        return value
    }

    public func text(_ key: String, in state: [String: ScreenValue]) -> String? {
        prop(key, in: state).map(\.text)
    }
}
