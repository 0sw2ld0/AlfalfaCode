import Foundation

/// Un resultado de `alfalfa --buscar-sesiones CONSULTA --todas --json`: un fragmento real de la
/// conversación de una sesión guardada, con lo necesario para abrirla en ese mensaje.
public struct SessionSearchHit: Equatable, Identifiable, Sendable {

    // MARK: - Propiedades

    /// `sesion#posicion`: lo que pide `--alrededor`.
    public var ref: String
    public var sessionID: String
    public var title: String
    public var folder: String?
    /// `user`, `assistant` o `tool`.
    public var role: String
    public var date: Double
    public var fragment: String
    /// El principio del mensaje que pinta la conversación (del usuario o del agente) donde está el resultado.
    public var anchor: String
    /// Viene de fuera (web, navegador, MCP) o lo dijo el agente justo después de leerlo (`origen: externo`).
    public var external = false
    public var id: String { ref }

    /// Quién lo dijo, para la fila de la barra lateral.
    public var roleLabel: String {
        switch role {
        case "user": return "Tú"
        case "assistant": return "Agente"
        case "tool": return "Herramienta"
        default: return role
        }
    }

    // MARK: - Inicializadores

    public init(ref: String, sessionID: String, title: String, folder: String? = nil, role: String,
                date: Double = 0, fragment: String, anchor: String = "") {
        self.ref = ref
        self.sessionID = sessionID
        self.title = title
        self.folder = folder
        self.role = role
        self.date = date
        self.fragment = fragment
        self.anchor = anchor
    }

    init?(object: [String: Any]) {
        guard let ref = object["ref"] as? String, let session = object["sesion"] as? String,
              let fragment = object["fragmento"] as? String else { return nil }
        let title = (object["titulo"] as? String).flatMap { $0.isEmpty ? nil : $0 } ?? "Sesión \(session)"
        self.init(ref: ref, sessionID: session, title: title, folder: object["cwd"] as? String,
                  role: object["rol"] as? String ?? "", date: (object["hora"] as? NSNumber)?.doubleValue ?? 0,
                  fragment: fragment, anchor: object["ancla"] as? String ?? "")
        external = object["origen"] as? String == "externo"
    }
}

/// La respuesta entera de una búsqueda en las sesiones.
public struct SessionSearch: Equatable, Sendable {
    public var query: String
    public var hits: [SessionSearchHit]
    /// El índice no terminó de ponerse al día en el tiempo de la búsqueda: puede faltar lo más viejo.
    public var upToDate: Bool

    public init(query: String, hits: [SessionSearchHit], upToDate: Bool = true) {
        self.query = query
        self.hits = hits
        self.upToDate = upToDate
    }

    init?(object: [String: Any]) {
        guard let query = object["consulta"] as? String, let rows = object["resultados"] as? [[String: Any]] else {
            return nil
        }
        self.init(query: query, hits: rows.compactMap(SessionSearchHit.init(object:)),
                  upToDate: object["indice_al_dia"] as? Bool ?? true)
    }

    /// Los argumentos de `alfalfa` para buscar desde la barra lateral: en todas las carpetas,
    /// porque la barra lateral las enseña todas y la búsqueda la pide el usuario. La consulta va
    /// pegada a la opción (`--buscar-sesiones=-x`): si empieza por «-» no se toma por otra opción.
    /// El índice se pone al día como mucho `timeLimit` segundos. `--incluir-externas`: lo lee el usuario,
    /// no un modelo, así que también salen las salidas web y MCP de todas las carpetas, marcadas.
    public static func arguments(for query: String) -> [String] {
        ["--buscar-sesiones=" + query, "--todas", "--incluir-externas", "--json", "--tiempo", String(timeLimit)]
    }

    public static let timeLimit = 3

    /// Lo mínimo que se busca: con menos letras salen demasiados resultados para una barra lateral.
    public static let minimumLength = 3
}

extension Timeline {

    /// El mensaje del usuario o del agente que empieza como `anchor` (sin distinguir espacios,
    /// mayúsculas ni tildes): donde se abre una sesión desde un resultado de búsqueda.
    public func itemID(matching anchor: String) -> Int? {
        let wanted = Self.comparable(anchor)
        guard !wanted.isEmpty else { return nil }
        // El ancla trae como mucho 120 caracteres; la conversación puede haberlo cortado distinto.
        let prefix = String(wanted.prefix(60))
        for item in items {
            switch item {
            case let .user(id, text, _), let .agent(id, text):
                let have = Self.comparable(text)
                if have.hasPrefix(prefix) || have.contains(prefix) { return id }
            default:
                continue
            }
        }
        return nil
    }

    static func comparable(_ text: String) -> String {
        text.folding(options: [.caseInsensitive, .diacriticInsensitive], locale: nil)
            .split(whereSeparator: \.isWhitespace).joined(separator: " ")
    }
}
