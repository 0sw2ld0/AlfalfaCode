import Foundation

/// Qué vuelve a antes de un mensaje (`--volver ID --turno T --alcance …`).
public enum UndoScope: String, CaseIterable, Identifiable, Sendable {
    case all = "todo"
    case conversation = "conversacion"
    case code = "codigo"

    public var id: String { rawValue }

    public var title: String {
        switch self {
        case .all: return "Conversación y código"
        case .conversation: return "Sólo conversación"
        case .code: return "Sólo código"
        }
    }

    public var detail: String {
        switch self {
        case .all: return "La conversación sigue desde antes del mensaje y los archivos vuelven a como estaban."
        case .conversation: return "La conversación sigue desde antes del mensaje. Los archivos no cambian."
        case .code: return "Los archivos vuelven a como estaban; la conversación sigue igual."
        }
    }

    /// Toca los archivos: hace falta la instantánea del turno.
    public var touchesCode: Bool { self != .conversation }
}

/// Un archivo que se restaura, o que se queda como está y por qué.
public struct UndoFile: Equatable, Hashable, Sendable {
    public var path: String
    /// `restaurar`, `recrear`, `borrar` o `permisos` (sólo cambiaron sus permisos).
    public var action: String
    /// Por qué se queda (`editado_despues`, `ajeno`, `orden`, `grande`, `ignorado`, `repo_anidado`…);
    /// nil en los restaurables.
    public var reason: String?

    public init(path: String, action: String, reason: String? = nil) {
        self.path = path
        self.action = action
        self.reason = reason
    }

    public var actionLabel: String {
        switch action {
        case "borrar": return "se borra"
        case "recrear": return "vuelve a estar"
        case "permisos": return "vuelven sus permisos"
        default: return "se restaura"
        }
    }

    /// Probablemente lo cambió una orden del agente: la hoja lo trae marcado para restaurar.
    public var suggested: Bool { reason == "orden" }

    public var reasonLabel: String {
        switch reason {
        case "editado_despues": return "Lo cambiaste después del turno"
        case "ajeno": return "No lo cambió el agente"
        case "orden": return "Lo cambió una orden del agente, sin huella propia"
        case "adoptado": return "Una orden lo tomó como del agente: puede llevar lo que cambiaste mientras corría"
        case "grande": return "Demasiado grande para guardarlo"
        case "ignorado": return "Está ignorado: no se guarda"
        case "repo_anidado": return "Está en un repositorio anidado: no se guarda"
        case "ilegible": return "No se puede leer"
        case "sin_registro": return "No hay registro de cómo estaba"
        default: return reason ?? ""
        }
    }
}

/// Lo que un turno hizo y no se deshace: publicar, instalar, git, red, fuera de la carpeta…
public struct UndoWarning: Equatable, Sendable {
    public var kind: String
    public var title: String
    public var commands: [String]

    public init(kind: String, title: String, commands: [String]) {
        self.kind = kind
        self.title = title
        self.commands = commands
    }
}

/// `deshacer_vista` / `rehacer_vista`: lo que haría volver (o rehacer) sin tocar nada.
public struct UndoPreview: Equatable, Sendable {
    public var session: String
    public var turn: String?
    /// Con rehacer: el deshacer que se rehace.
    public var redoOf: String?
    public var available: Bool
    public var reason: String?
    public var restorable: [UndoFile]
    public var conflicts: [UndoFile]
    /// Lo que no se toca porque las instantáneas no lo guardan (`no_cubiertos`).
    public var untouched: [UndoFile]
    public var warnings: [UndoWarning]
    /// Es el primer mensaje: la conversación vuelve a empezar en una sesión nueva.
    public var firstTurn: Bool

    public init(session: String, turn: String? = nil, redoOf: String? = nil, available: Bool, reason: String? = nil,
                restorable: [UndoFile] = [], conflicts: [UndoFile] = [], untouched: [UndoFile] = [],
                warnings: [UndoWarning] = [], firstTurn: Bool = false) {
        self.session = session
        self.turn = turn
        self.redoOf = redoOf
        self.available = available
        self.reason = reason
        self.restorable = restorable
        self.conflicts = conflicts
        self.untouched = untouched
        self.warnings = warnings
        self.firstTurn = firstTurn
    }

    /// Nada que restaurar ni que decidir: el código ya está como antes.
    public var nothingToDo: Bool { restorable.isEmpty && conflicts.isEmpty }

    static func parse(_ object: [String: Any]) -> UndoPreview? {
        guard let session = object["sesion"] as? String else { return nil }
        return UndoPreview(session: session, turn: object["turno"] as? String, redoOf: object["deshecho"] as? String,
                           available: object["disponible"] as? Bool ?? false, reason: object["motivo"] as? String,
                           restorable: files(object["restaurables"]), conflicts: files(object["conflictos"]),
                           untouched: files(object["no_cubiertos"]), warnings: warnings(object["irreversibles"]),
                           firstTurn: object["primer_turno"] as? Bool ?? false)
    }

    static func files(_ value: Any?) -> [UndoFile] {
        (value as? [[String: Any]] ?? []).compactMap { row in
            guard let path = row["ruta"] as? String else { return nil }
            return UndoFile(path: path, action: row["accion"] as? String ?? "restaurar",
                            reason: row["motivo"] as? String)
        }
    }

    static func warnings(_ value: Any?) -> [UndoWarning] {
        (value as? [[String: Any]] ?? []).compactMap { row in
            guard let kind = row["tipo"] as? String, let title = row["titulo"] as? String else { return nil }
            return UndoWarning(kind: kind, title: title, commands: row["ordenes"] as? [String] ?? [])
        }
    }
}

/// `deshecho` (y la fila `deshacer` del historial): el código se deshizo o se rehízo.
public struct UndoStep: Equatable, Sendable {
    public var id: String
    /// `deshacer` o `rehacer`.
    public var kind: String
    /// La sesión donde quedó apuntado (la bifurcada, con «Conversación y código»).
    public var session: String?
    public var turn: String?
    /// Con rehacer: el deshacer que devolvió.
    public var redoOf: String?
    public var restored: [String]
    public var kept: [String]
    public var failed: [String]
    public var warnings: [UndoWarning]
    /// Era el primer mensaje: la conversación empieza de nuevo (la app abre un encargo con su texto).
    public var newConversation: Bool
    public var text: String

    public init(id: String, kind: String, session: String? = nil, turn: String? = nil, redoOf: String? = nil,
                restored: [String] = [], kept: [String] = [], failed: [String] = [], warnings: [UndoWarning] = [],
                newConversation: Bool = false, text: String = "") {
        self.id = id
        self.kind = kind
        self.session = session
        self.turn = turn
        self.redoOf = redoOf
        self.restored = restored
        self.kept = kept
        self.failed = failed
        self.warnings = warnings
        self.newConversation = newConversation
        self.text = text
    }

    public var isRedo: Bool { kind == "rehacer" }

    static func parse(_ value: Any?, text: String, session: String? = nil) -> UndoStep? {
        guard let object = value as? [String: Any], let id = object["id"] as? String,
              let kind = object["paso"] as? String else { return nil }
        let failed = (object["fallos"] as? [[String: Any]] ?? []).compactMap { $0["ruta"] as? String }
        return UndoStep(id: id, kind: kind, session: session ?? object["sesion"] as? String,
                        turn: object["turno"] as? String, redoOf: object["de"] as? String,
                        restored: object["restaurados"] as? [String] ?? [],
                        kept: object["conservados"] as? [String] ?? [],
                        failed: failed, warnings: UndoPreview.warnings(object["irreversibles"]),
                        newConversation: object["nueva_conversacion"] as? Bool ?? false,
                        text: object["texto"] as? String ?? text)
    }
}
