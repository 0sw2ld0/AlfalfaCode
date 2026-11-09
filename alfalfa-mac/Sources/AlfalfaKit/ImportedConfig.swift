import Foundation

/// Algo que trae una carpeta de OpenCode, Codex o Claude Code (`alfalfa --importar --json`):
/// un servidor MCP, un agente, una orden con «/» o unas instrucciones.
public struct ImportedItem: Equatable, Identifiable, Sendable {

    // MARK: - Tipos

    public enum Kind: String, CaseIterable, Sendable {
        case mcp, agente, comando, instrucciones

        public var title: String {
            switch self {
            case .mcp: return "Servidores MCP"
            case .agente: return "Agentes"
            case .comando: return "Órdenes con «/»"
            case .instrucciones: return "Instrucciones"
            }
        }
    }

    // MARK: - Propiedades

    /// Clave completa (tipo, nombre y archivo de origen) que acepta `--autorizar`.
    public var key: String
    public var kind: Kind
    public var name: String
    /// OpenCode, Codex, Claude Code o AGENTS.md.
    public var tool: String
    /// El archivo de origen, relativo al proyecto o con «~/».
    public var place: String
    /// autorizado, pendiente, cambio, activo, desactivado, duplicado o invalido.
    public var state: String
    public var detail: String
    public var problem: String?
    public var warnings: [String]
    public var coveredBy: String?
    public var authorizable: Bool
    /// Sólo en un servidor MCP autorizado: si corre con red.
    public var network: Bool?

    public var id: String { key }

    /// Sin autorizar o con una definición nueva desde que se autorizó.
    public var needsAuthorization: Bool { authorizable && (state == "pendiente" || state == "cambio") }

    public var stateTitle: String {
        switch state {
        case "autorizado": return network == false ? "Autorizado, sin red" : "Autorizado"
        case "pendiente": return "Sin autorizar"
        case "cambio": return "Cambió: vuelve a autorizarlo"
        case "activo": return "Activo"
        case "desactivado": return "Desactivado en su configuración"
        case "duplicado": return "Duplicado"
        case "invalido": return "No se puede usar"
        default: return state
        }
    }

    // MARK: - Inicializadores

    public init(key: String, kind: Kind, name: String, tool: String, place: String, state: String,
                detail: String = "", problem: String? = nil, warnings: [String] = [], coveredBy: String? = nil,
                authorizable: Bool = false, network: Bool? = nil) {
        self.key = key
        self.kind = kind
        self.name = name
        self.tool = tool
        self.place = place
        self.state = state
        self.detail = detail
        self.problem = problem
        self.warnings = warnings
        self.coveredBy = coveredBy
        self.authorizable = authorizable
        self.network = network
    }

    init?(_ row: [String: Any]) {
        guard let key = row["clave"] as? String, let kind = (row["tipo"] as? String).flatMap(Kind.init(rawValue:)),
              let name = row["nombre"] as? String else { return nil }
        self.init(key: key, kind: kind, name: name, tool: row["herramienta"] as? String ?? "",
                  place: row["lugar"] as? String ?? row["origen"] as? String ?? "",
                  state: row["estado"] as? String ?? "", detail: row["detalle"] as? String ?? "",
                  problem: row["problema"] as? String, warnings: row["avisos"] as? [String] ?? [],
                  coveredBy: row["tapado_por"] as? String, authorizable: row["autorizable"] as? Bool ?? false,
                  network: row["red"] as? Bool)
    }
}

/// Lo que hay en una carpeta y dónde se guardan las autorizaciones.
public struct ImportedConfig: Equatable, Sendable {

    // MARK: - Propiedades

    public var folder: String
    public var file: String
    public var items: [ImportedItem]
    public var warnings: [String]
    public var errors: [String]

    public var pending: [ImportedItem] { items.filter(\.needsAuthorization) }

    // MARK: - Inicializadores

    public init(folder: String, file: String = "", items: [ImportedItem], warnings: [String] = [],
                errors: [String] = []) {
        self.folder = folder
        self.file = file
        self.items = items
        self.warnings = warnings
        self.errors = errors
    }

    init?(_ object: [String: Any]) {
        guard let rows = object["elementos"] as? [[String: Any]] else { return nil }
        self.init(folder: object["cwd"] as? String ?? "", file: object["archivo"] as? String ?? "",
                  items: rows.compactMap(ImportedItem.init), warnings: object["avisos"] as? [String] ?? [],
                  errors: object["errores"] as? [String] ?? [])
    }

    // MARK: - Métodos públicos

    public func items(of kind: ImportedItem.Kind) -> [ImportedItem] { items.filter { $0.kind == kind } }

    /// Las órdenes «/» del proyecto que encajan con lo escrito, sin las que tapa una orden propia de la app.
    public func commands(matching prefix: String) -> [ImportedItem] {
        let wanted = prefix.lowercased()
        return items(of: .comando).filter { item in
            item.state == "activo" && SlashCommand.named(item.name) == nil
                && (wanted.isEmpty || item.name.lowercased().hasPrefix(wanted))
        }
    }
}
