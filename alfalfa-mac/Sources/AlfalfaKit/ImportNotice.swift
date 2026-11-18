import Foundation

/// El aviso con el que empieza una sesión si la carpeta trae de OpenCode, Codex o Claude Code algo sin autorizar
/// (`importadas` de un `evento` del sistema, de `Importadas.aviso` en el motor). La conversación lo pinta con
/// «Revisar y autorizar…». Sólo dice de qué se avisó: antes de autorizar, la hoja vuelve a pedir la lista
/// (`alfalfa --importar --json`) y autoriza `clave@huella` de lo que la persona ve entonces.
public struct ImportNotice: Equatable, Sendable {

    // MARK: - Tipos

    /// Un servidor MCP, un agente o una orden «/» sin autorizar.
    public struct Pending: Equatable, Identifiable, Sendable {
        /// Clave completa (tipo, nombre y archivo de origen), la de `--autorizar`.
        public var key: String
        /// Huella pública de la definición cuando se avisó.
        public var fingerprint: String
        public var kind: ImportedItem.Kind
        public var name: String
        /// OpenCode, Codex o Claude Code.
        public var tool: String
        /// pendiente, o cambio (se autorizó otra definición).
        public var state: String

        public var id: String { key }

        /// Con su «/» si es una orden.
        public var displayName: String { (kind == .comando ? "/" : "") + name }

        public init(key: String, fingerprint: String, kind: ImportedItem.Kind, name: String, tool: String,
                    state: String = "pendiente") {
            self.key = key
            self.fingerprint = fingerprint
            self.kind = kind
            self.name = name
            self.tool = tool
            self.state = state
        }

        /// De la lista de `--importar --json` (el atajo de la barra lateral no tiene aviso).
        public init(_ item: ImportedItem) {
            self.init(key: item.key, fingerprint: item.fingerprint, kind: item.kind, name: item.name, tool: item.tool,
                      state: item.state)
        }

        init?(_ row: [String: Any]) {
            guard let key = row["clave"] as? String, !key.isEmpty,
                  let kind = (row["tipo"] as? String).flatMap(ImportedItem.Kind.init(rawValue:)), kind != .instrucciones,
                  let name = row["nombre"] as? String else { return nil }
            self.init(key: key, fingerprint: row["huella"] as? String ?? "", kind: kind, name: name,
                      tool: row["herramienta"] as? String ?? "", state: row["estado"] as? String ?? "pendiente")
        }
    }

    // MARK: - Propiedades

    public var pending: [Pending]
    /// El texto entero del motor, para la terminal y la exportación.
    public var text: String

    /// «5 servidores MCP de Codex sin autorizar».
    public var headline: String {
        let counts: [(ImportedItem.Kind, String, String)] = [(.mcp, "servidor MCP", "servidores MCP"),
                                                             (.agente, "agente", "agentes"),
                                                             (.comando, "orden «/»", "órdenes «/»")]
        let parts = counts.compactMap { kind, one, many -> String? in
            let count = pending.filter { $0.kind == kind }.count
            return count == 0 ? nil : "\(count) " + (count == 1 ? one : many)
        }
        var tools: [String] = []
        for item in pending where !item.tool.isEmpty && !tools.contains(item.tool) { tools.append(item.tool) }
        return Self.list(parts) + (tools.isEmpty ? "" : " de " + Self.list(tools)) + " sin autorizar"
    }

    /// «chiyo, cuyimage, cuycleaner, cuyworld-atlas y meshy»: hasta seis nombres y «y N más».
    public var names: String {
        let shown = pending.prefix(6).map(\.displayName)
        let rest = pending.count - shown.count
        return rest > 0 ? shown.joined(separator: ", ") + " y \(rest) más" : Self.list(Array(shown))
    }

    // MARK: - Inicializadores

    public init(pending: [Pending], text: String) {
        self.pending = pending
        self.text = text
    }

    // MARK: - Métodos

    /// Lo que llega en el campo `importadas` del evento; nil si no trae nada autorizable.
    static func parse(_ value: Any?, text: String) -> ImportNotice? {
        guard let object = value as? [String: Any], let rows = object["pendientes"] as? [[String: Any]] else {
            return nil
        }
        let pending = rows.compactMap(Pending.init)
        return pending.isEmpty ? nil : ImportNotice(pending: pending, text: text)
    }

    /// Avisa de lo mismo (las mismas claves, en cualquier orden): cada proceso de la sesión avisa al empezar su
    /// primer turno, y reabrir la sesión no debe repetir la tarjeta.
    func isSame(as other: ImportNotice) -> Bool { Set(pending.map(\.key)) == Set(other.pending.map(\.key)) }

    /// «a», «a y b», «a, b y c».
    public static func list(_ parts: [String]) -> String {
        guard let last = parts.last else { return "" }
        return parts.count == 1 ? last : parts.dropLast().joined(separator: ", ") + " y " + last
    }
}

/// La hoja «Revisar y autorizar…» (del aviso de una sesión, de la barra lateral o del menú): lo que sigue sin
/// autorizar según la lista recién leída.
public enum ImportReview {

    /// Lo que se ofrece: lo que la carpeta tiene sin autorizar ahora (`needsAuthorization`), con lo que nombraba el
    /// aviso delante y en su orden. Lo que ya se autorizó desde el aviso (en Ajustes, en una terminal) no sale.
    public static func items(_ config: ImportedConfig, notice: ImportNotice? = nil) -> [ImportedItem] {
        let rows = notice?.pending ?? []
        let order = Dictionary(rows.enumerated().map { ($0.element.key, $0.offset) }) { first, _ in first }
        return config.pending.enumerated().sorted { lhs, rhs in
            (order[lhs.element.key] ?? Int.max, lhs.offset) < (order[rhs.element.key] ?? Int.max, rhs.offset)
        }.map(\.element)
    }

    /// `clave@huella` de lo marcado, en el orden de la lista. La huella es la de la definición que se ve en la hoja:
    /// si el archivo cambió desde entonces, el motor no lo autoriza.
    public static func authorizationKeys(_ items: [ImportedItem], selected: Set<String>) -> [String] {
        items.filter { selected.contains($0.key) }.map(\.authorizationKey)
    }

    /// De lo que se pidió autorizar (`keys`, claves completas), lo que la lista nueva da por autorizado.
    public static func authorized(_ keys: Set<String>, in config: ImportedConfig) -> [ImportedItem] {
        config.items.filter { keys.contains($0.key) && $0.state == "autorizado" }
    }

    /// «chiyo, /probar y revisor».
    public static func names(_ items: [ImportedItem]) -> String {
        ImportNotice.list(items.map { ($0.kind == .comando ? "/" : "") + $0.name })
    }

    /// El atajo de la barra lateral: «5 MCP sin autorizar» o, si hay de varios tipos, «7 importados sin autorizar»;
    /// nil si no queda nada (`items`: lo pendiente, `ImportedConfig.pending`).
    public static func shortcutTitle(_ items: [ImportedItem]) -> String? {
        guard !items.isEmpty else { return nil }
        let count = items.count
        let kinds = Set(items.map(\.kind))
        let what: String
        switch kinds.count == 1 ? kinds.first : nil {
        case .mcp?: what = "MCP"
        case .agente?: what = count == 1 ? "agente" : "agentes"
        case .comando?: what = count == 1 ? "orden «/»" : "órdenes «/»"
        default: what = count == 1 ? "importado" : "importados"
        }
        return "\(count) " + what + " sin autorizar"
    }

    /// «5 servidores MCP de Codex sin autorizar», para la ayuda del atajo y VoiceOver.
    public static func headline(_ items: [ImportedItem]) -> String {
        ImportNotice(pending: items.map(ImportNotice.Pending.init), text: "").headline
    }
}
