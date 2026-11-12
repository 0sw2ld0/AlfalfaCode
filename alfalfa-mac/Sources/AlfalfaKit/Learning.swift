import Foundation

/// Lo que aprende AlfalfaCode (alfalfa/aprendizaje): la memoria en tres almacenes, las habilidades
/// que escribió y lo que la revisión al cerrar el turno propone. Llega de `alfalfa --memoria --json`,
/// `alfalfa --aprendido --json` y, en una sesión, de los eventos `aprendizaje_propuesto` y
/// `aprendizaje_resuelto`.

/// Un almacén de memoria: usuario, entorno del Mac o proyecto.
public struct MemoryStore: Equatable, Identifiable, Sendable {

    // MARK: - Propiedades

    /// usuario, entorno o proyecto.
    public var store: String
    public var title: String
    public var path: String
    public var limit: Int
    public var used: Int
    public var entries: [String]
    /// Sólo en el del proyecto: la raíz a la que pertenece.
    public var project: String?

    public var id: String { store }

    /// El texto como lo edita el usuario: las entradas separadas por una línea «§».
    public var text: String { entries.joined(separator: "\n§\n") }

    // MARK: - Inicializadores

    public init(store: String, title: String, path: String = "", limit: Int, used: Int, entries: [String],
                project: String? = nil) {
        self.store = store
        self.title = title
        self.path = path
        self.limit = limit
        self.used = used
        self.entries = entries
        self.project = project
    }

    init?(_ row: [String: Any]) {
        guard let store = row["almacen"] as? String, let limit = row["limite"] as? Int else { return nil }
        self.init(store: store, title: row["titulo"] as? String ?? store, path: row["ruta"] as? String ?? "",
                  limit: limit, used: row["usados"] as? Int ?? 0, entries: row["entradas"] as? [String] ?? [],
                  project: row["proyecto"] as? String)
    }

    // MARK: - Métodos públicos

    /// Caracteres que ocuparía `text` tal como lo cuenta el motor (entradas sin espacios de los bordes).
    public static func characters(of text: String) -> Int {
        var entries: [String] = []
        var current: [Substring] = []
        for line in text.split(separator: "\n", omittingEmptySubsequences: false) {
            if line.trimmingCharacters(in: .whitespaces) == "§" {
                entries.append(current.joined(separator: "\n").trimmingCharacters(in: .whitespacesAndNewlines))
                current = []
            } else {
                current.append(line)
            }
        }
        entries.append(current.joined(separator: "\n").trimmingCharacters(in: .whitespacesAndNewlines))
        return entries.filter { !$0.isEmpty }.joined(separator: "\n§\n").count
    }
}

/// Los interruptores de Ajustes ▸ Memoria (`$ALFALFA_HOME/aprendizaje.json`).
public struct LearningSettings: Equatable, Sendable {

    // MARK: - Propiedades

    public var memory: Bool
    public var skills: Bool
    public var review: Bool
    public var reviewModel: String?

    // MARK: - Inicializadores

    public init(memory: Bool = true, skills: Bool = true, review: Bool = true, reviewModel: String? = nil) {
        self.memory = memory
        self.skills = skills
        self.review = review
        self.reviewModel = reviewModel
    }

    init(_ object: [String: Any]?) {
        let object = object ?? [:]
        self.init(memory: object["memoria"] as? Bool ?? true, skills: object["habilidades"] as? Bool ?? true,
                  review: object["revision"] as? Bool ?? true,
                  reviewModel: object["modelo_revision"] as? String)
    }

    /// La clave de `alfalfa --aprendido ajustes CLAVE VALOR` de cada interruptor. No hay ninguno para
    /// guardar sin tarjeta: cada escritura de memoria o de una habilidad se aprueba viendo su diff.
    public enum Key: String, CaseIterable, Sendable {
        case memory = "memoria"
        case skills = "habilidades"
        case review = "revision"
    }

    public func value(_ key: Key) -> Bool {
        switch key {
        case .memory: return memory
        case .skills: return skills
        case .review: return review
        }
    }
}

/// Una habilidad que escribió AlfalfaCode, con su origen.
public struct LearnedSkill: Equatable, Identifiable, Sendable {

    // MARK: - Propiedades

    /// `usuario:nombre` o `proyecto:clave:nombre`.
    public var key: String
    public var name: String
    /// usuario o proyecto.
    public var scope: String
    public var summary: String
    public var session: String?
    public var turn: String?
    /// agente, revision o aprender.
    public var origin: String?
    public var locked: Bool
    /// al_dia, cambiada, falta o ilegible.
    public var state: String
    public var thisFolder: Bool
    public var path: String

    public var id: String { key }

    /// Lo que acepta `alfalfa --aprendido ver|editar|borrar|bloquear`.
    public var reference: String { scope + ":" + name }

    public var originText: String {
        let how: String
        switch origin {
        case "revision": how = "Propuesta al cerrar un turno"
        case "aprender": how = "Con /aprender"
        default: how = "La guardó el agente"
        }
        guard let session else { return how }
        return how + " · sesión " + session + (turn.map { " · turno " + $0 } ?? "")
    }

    public var stateText: String? {
        switch state {
        case "cambiada": return "Cambiada a mano: el agente ya no la toca"
        case "falta": return "Falta su carpeta"
        case "ilegible": return "No se puede leer"
        default: return nil
        }
    }

    // MARK: - Inicializadores

    public init(key: String, name: String, scope: String, summary: String = "", session: String? = nil,
                turn: String? = nil, origin: String? = nil, locked: Bool = false, state: String = "al_dia",
                thisFolder: Bool = true, path: String = "") {
        self.key = key
        self.name = name
        self.scope = scope
        self.summary = summary
        self.session = session
        self.turn = turn
        self.origin = origin
        self.locked = locked
        self.state = state
        self.thisFolder = thisFolder
        self.path = path
    }

    init?(_ row: [String: Any]) {
        guard let key = row["clave"] as? String, let name = row["nombre"] as? String,
              let scope = row["alcance"] as? String else { return nil }
        self.init(key: key, name: name, scope: scope, summary: row["descripcion"] as? String ?? "",
                  session: row["sesion"] as? String, turn: row["turno"] as? String, origin: row["origen"] as? String,
                  locked: row["bloqueada"] as? Bool ?? false, state: row["estado"] as? String ?? "al_dia",
                  thisFolder: row["de_esta_carpeta"] as? Bool ?? true, path: row["ruta"] as? String ?? "")
    }
}

/// Lo que propone la revisión al cerrar el turno: guardar memoria o una habilidad, con su diff.
public struct LearningProposal: Equatable, Identifiable, Sendable {

    // MARK: - Propiedades

    public var id: String
    /// memoria o habilidad.
    public var kind: String
    public var store: String?
    public var action: String?
    public var scope: String?
    public var name: String?
    public var reason: String
    public var diff: String

    public var title: String {
        if kind == "memoria" { return "Guardar en la memoria " + (store ?? "") }
        let verb = action == "parchear" ? "Cambiar" : action == "borrar" ? "Borrar" : "Crear"
        return verb + " la habilidad " + (name ?? "") + (scope == "proyecto" ? " del proyecto" : "")
    }

    // MARK: - Inicializadores

    public init(id: String, kind: String, store: String? = nil, action: String? = nil, scope: String? = nil,
                name: String? = nil, reason: String = "", diff: String = "") {
        self.id = id
        self.kind = kind
        self.store = store
        self.action = action
        self.scope = scope
        self.name = name
        self.reason = reason
        self.diff = diff
    }

    init?(_ row: [String: Any]) {
        guard let id = row["id"] as? String, let kind = row["clase"] as? String else { return nil }
        self.init(id: id, kind: kind, store: row["almacen"] as? String, action: row["accion"] as? String,
                  scope: row["alcance"] as? String, name: row["nombre"] as? String,
                  reason: row["motivo"] as? String ?? "", diff: row["diff"] as? String ?? "")
    }
}

/// Cómo quedó la tarjeta de una propuesta.
public enum LearningProposalState: Equatable, Sendable {
    case pending
    case applied(String)
    case discarded
    case failed(String)
}

/// `memoria` (de `alfalfa --memoria --json`).
public struct MemoryState: Equatable, Sendable {
    public var stores: [MemoryStore]
    public var settings: LearningSettings
    public var project: String
    public var learnsHere: Bool

    public init(stores: [MemoryStore], settings: LearningSettings = LearningSettings(), project: String = "",
                learnsHere: Bool = true) {
        self.stores = stores
        self.settings = settings
        self.project = project
        self.learnsHere = learnsHere
    }

    init?(_ object: [String: Any]) {
        guard let rows = object["almacenes"] as? [[String: Any]] else { return nil }
        self.init(stores: rows.compactMap(MemoryStore.init), settings: LearningSettings(object["ajustes"] as? [String: Any]),
                  project: object["proyecto"] as? String ?? "", learnsHere: object["aprende_aqui"] as? Bool ?? true)
    }
}

/// `aprendido` (de `alfalfa --aprendido --json`).
public struct LearnedState: Equatable, Sendable {
    public var skills: [LearnedSkill]
    public var proposals: [LearningProposal]
    public var settings: LearningSettings
    public var project: String
    public var learnsHere: Bool
    /// Lo que dijo la orden que lo cambió («Bloqueada usuario:x.»).
    public var message: String?

    public init(skills: [LearnedSkill], proposals: [LearningProposal] = [], settings: LearningSettings = LearningSettings(),
                project: String = "", learnsHere: Bool = true, message: String? = nil) {
        self.skills = skills
        self.proposals = proposals
        self.settings = settings
        self.project = project
        self.learnsHere = learnsHere
        self.message = message
    }

    init?(_ object: [String: Any]) {
        guard let rows = object["habilidades"] as? [[String: Any]] else { return nil }
        self.init(skills: rows.compactMap(LearnedSkill.init),
                  proposals: (object["pendientes"] as? [[String: Any]] ?? []).compactMap(LearningProposal.init),
                  settings: LearningSettings(object["ajustes"] as? [String: Any]),
                  project: object["proyecto"] as? String ?? "", learnsHere: object["aprende_aqui"] as? Bool ?? true,
                  message: object["texto"] as? String)
    }
}
