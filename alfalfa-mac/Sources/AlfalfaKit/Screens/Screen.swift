import Foundation

/// Dónde se enseña una pantalla que el agente actualiza en un mensaje posterior (`placement`).
public enum ScreenPlacement: String, Equatable, Sendable {
    /// Se queda en el mensaje donde apareció.
    case inline
    /// Se mueve al último mensaje que la tocó; donde estaba queda una copia apagada.
    case latest
}

/// Un bloque `pacai.ui/1`: un cambio (`patch`) o la pantalla entera (`replace`).
public struct ScreenUpdate: Equatable, Sendable {

    // MARK: - Constantes

    public static let schema = "pacai.ui/1"

    // MARK: - Propiedades

    public var view: String
    public var replace: Bool
    public var rev: Int
    public var nodes: [ScreenNode]
    public var remove: [String]
    public var state: [String: ScreenValue]
    public var alt: String?
    public var placement: ScreenPlacement?

    // MARK: - Inicializadores

    public init(view: String, replace: Bool = false, rev: Int, nodes: [ScreenNode] = [], remove: [String] = [],
                state: [String: ScreenValue] = [:], alt: String? = nil, placement: ScreenPlacement? = nil) {
        self.view = view
        self.replace = replace
        self.rev = rev
        self.nodes = nodes
        self.remove = remove
        self.state = state
        self.alt = alt
        self.placement = placement
    }

    /// El `data` de un bloque `pacai.ui/1`; nil si le falta `view`, `op` o `rev`.
    public init?(json data: [String: Any]) {
        guard let view = data["view"] as? String, let op = data["op"] as? String, op == "patch" || op == "replace",
              let rev = data["rev"] as? Int else { return nil }
        self.init(view: view, replace: op == "replace", rev: rev,
                  nodes: (data["nodes"] as? [[String: Any]] ?? []).compactMap(ScreenNode.init(json:)),
                  remove: data["remove"] as? [String] ?? [], state: ScreenValue.dictionary(data["state"]),
                  alt: data["alt"] as? String,
                  placement: (data["placement"] as? String).flatMap(ScreenPlacement.init(rawValue:)))
    }

    // MARK: - Métodos públicos

    /// Los `data` de los bloques `pacai.ui/1` de una lista de bloques PACAI.
    public static func blocks(_ blocks: Any?) -> [[String: Any]] {
        (blocks as? [[String: Any]] ?? []).compactMap {
            $0["type"] as? String == "structured" && $0["schema"] as? String == schema ? $0["data"] as? [String: Any]
                : nil
        }
    }

    /// Una lista de `data` (como la lleva `pantallas` en el evento del mensaje).
    public static func list(_ value: Any?) -> [ScreenUpdate] {
        (value as? [[String: Any]] ?? []).compactMap(ScreenUpdate.init(json:))
    }
}

/// Una pantalla del perfil `ui`: sus nodos, el estado que le dio el agente y su revisión. Es pura: `applying`
/// devuelve la pantalla siguiente.
public struct Screen: Equatable, Sendable, Identifiable {

    // MARK: - Constantes

    public static let root = "root"
    /// Como el límite por defecto del perfil: un ciclo o un árbol absurdo no cuelga la vista.
    public static let maxDepth = 12

    // MARK: - Propiedades

    public var id: String { name }
    public var name: String
    public var rev: Int
    public var nodes: [String: ScreenNode]
    public var state: [String: ScreenValue]
    public var alt: String
    public var placement: ScreenPlacement

    // MARK: - Inicializadores

    public init(name: String, rev: Int = 0, nodes: [String: ScreenNode] = [:], state: [String: ScreenValue] = [:],
                alt: String = "", placement: ScreenPlacement = .inline) {
        self.name = name
        self.rev = rev
        self.nodes = nodes
        self.state = state
        self.alt = alt
        self.placement = placement
    }

    // MARK: - Métodos públicos

    /// La pantalla tras un bloque; nil si el bloque es de otra pantalla o de una revisión que ya pasó.
    public func applying(_ update: ScreenUpdate) -> Screen? {
        guard update.view == name, update.rev > rev else { return nil }
        var next = self
        next.rev = update.rev
        if update.replace {
            next.nodes = [:]
            next.state = [:]
        }
        for node in update.nodes { next.nodes[node.id] = node }
        for id in update.remove { next.nodes.removeValue(forKey: id) }
        next.state.merge(update.state) { _, new in new }
        if let alt = update.alt { next.alt = alt }
        if let placement = update.placement { next.placement = placement }
        return next
    }

    /// Los hijos de un nodo que ya llegaron, en orden (un id repetido sale una vez).
    public func children(of node: ScreenNode) -> [ScreenNode] {
        var seen = Set<String>()
        return node.children.compactMap { seen.insert($0).inserted ? nodes[$0] : nil }
    }

    /// Lo que pasa cuando el usuario dispara `event` en `node`: el estado después de los `set` y, si hay un `send`,
    /// lo que vuelve al agente. `item` es la fila (`row_press`) o el id (`item_press`) sobre el que fue el evento.
    public func handle(_ event: String, on node: ScreenNode, state current: [String: ScreenValue],
                       item: ScreenValue? = nil) -> ScreenEventResult {
        var result = ScreenEventResult(state: current)
        for action in node.events[event] ?? [] {
            switch action {
            case let .set(values):
                result.state.merge(values) { _, new in new }
            case let .openURL(url):
                result.urls.append(url)
            case let .send(name, keys):
                guard result.request == nil else { continue }       // como mucho un `send` por evento
                let wanted = keys ?? Array(result.state.keys)
                var values: [String: ScreenValue] = [:]
                for key in wanted { values[key] = result.state[key] ?? .null }
                result.request = ScreenActionRequest(view: self.name, rev: rev, node: node.id, event: event,
                                                     name: name, values: values, item: item,
                                                     label: Self.label(of: node, in: result.state))
            }
        }
        return result
    }

    // MARK: - Métodos privados

    private static func label(of node: ScreenNode, in state: [String: ScreenValue]) -> String? {
        ["label", "submit_label", "title"].lazy.compactMap { node.text($0, in: state) }.first { !$0.isEmpty }
    }
}

/// El resultado de un evento: el estado nuevo, la acción para el agente (si hay `send`) y los enlaces que abrir.
public struct ScreenEventResult: Equatable, Sendable {
    public var state: [String: ScreenValue]
    public var request: ScreenActionRequest?
    public var urls: [URL] = []
}

/// Lo que vuelve al agente por un `send`: el bloque `pacai.ui.action/1` y un texto legible que lo acompaña.
public struct ScreenActionRequest: Equatable, Sendable {

    // MARK: - Constantes

    public static let schema = "pacai.ui.action/1"

    // MARK: - Propiedades

    public var view: String
    public var rev: Int
    public var node: String
    public var event: String
    public var name: String
    public var values: [String: ScreenValue]
    public var item: ScreenValue?
    /// La etiqueta del control que lo disparó («Crear ticket»), para el texto legible.
    public var label: String?

    /// Lo que el usuario «dijo», para la conversación y para un cliente que no dibuje pantallas.
    public var summary: String {
        let what = label.map { "«\($0)»" } ?? "«\(name)»"
        switch event {
        case "submit": return "Envié el formulario " + what
        case "row_press", "item_press": return "Elegí " + (item.map { Self.describe($0) } ?? what)
        default: return "Pulsé " + what
        }
    }

    /// El `data` del bloque `pacai.ui.action/1`.
    public var data: [String: Any] {
        var data: [String: Any] = ["view": view, "rev": rev, "node": node, "event": event, "name": name,
                                   "values": values.mapValues(\.json)]
        data["item"] = item?.json
        return data
    }

    // MARK: - Métodos privados

    /// Una fila como «T-12 · Login falla en Safari»; un id, tal cual.
    private static func describe(_ item: ScreenValue) -> String {
        guard let fields = item.object else { return "«\(item.text)»" }
        let shown = fields.keys.sorted().prefix(3).compactMap { fields[$0]?.text }.filter { !$0.isEmpty }
        return "«" + shown.joined(separator: " · ") + "»"
    }
}
