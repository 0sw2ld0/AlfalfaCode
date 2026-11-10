import Foundation

public enum TaskQueueAction: String, Sendable {
    case prepare = "preparar", start = "iniciar", pause = "pausar", stop = "detener", cancel = "cancelar"
}

/// Una cola de tareas independientes. Cada encargo conserva su sesión y, en paralelo, su worktree.
public struct TaskQueue: Equatable, Sendable {
    public struct Job: Equatable, Sendable, Identifiable {
        public var id: String
        public var title: String
        public var state: String
        public var recap: String?
        public var activity: String?
        public var session: String?
        public var folder: String?
        public var branch: String?
        public var stateText: String {
            switch state {
            case "completed": return "Terminado"
            case "ejecutando": return "En marcha"
            case "failed": return "Falló"
            case "blocked": return "Bloqueado"
            case "interrumpido", "interrupted": return "Interrumpido"
            case "cancelado": return "Cancelado"
            default: return "Pendiente"
            }
        }
    }
    public var id: String
    public var state: String
    public var mode: String
    public var jobs: [Job]
    public var recap: String?
    public var reason: String?
    public var sessionTokens: Int?
    public var sessionCost: Double?
    public var running: Bool { ["ejecutando", "pausando"].contains(state) }
    public var canStart: Bool { ["lista", "pausada"].contains(state) }
    public var finished: Bool { ["completada", "cancelada"].contains(state) }
    /// Al despertar llegan primero los datos guardados; todavía no son la respuesta al clic nuevo.
    public func acknowledges(_ action: TaskQueueAction, previousID: String?) -> Bool {
        switch action {
        case .prepare: return id != previousID && state == "lista"
        case .start: return id == previousID && running
        default: return true
        }
    }
    public var stateText: String {
        switch state {
        case "ejecutando": return "En marcha"
        case "pausando": return "Terminan los activos"
        case "completada": return "Terminada"
        case "cancelada": return "Cancelada"
        case "pausada": return "Pausada"
        default: return "Lista para empezar"
        }
    }

    public mutating func recover() {
        guard running else { return }
        state = "pausada"
        reason = "La ejecución se interrumpió. Reanuda la cola para continuar."
        for index in jobs.indices where jobs[index].state == "ejecutando" { jobs[index].state = "interrumpido" }
    }

    public init?(value: Any?) {
        guard let object = value as? [String: Any], let id = object["id"] as? String,
              let state = object["estado"] as? String, let mode = object["modo"] as? String,
              let rows = object["encargos"] as? [[String: Any]], (1...20).contains(rows.count) else { return nil }
        self.id = id
        self.state = state
        self.mode = mode
        recap = object["resumen"] as? String
        reason = object["motivo"] as? String
        sessionTokens = object["tokens_sesion"] as? Int
        sessionCost = (object["coste_sesion"] as? NSNumber)?.doubleValue
        jobs = rows.compactMap { row in
            guard let id = row["id"] as? String, let title = row["titulo"] as? String,
                  let state = row["estado"] as? String else { return nil }
            return Job(id: id, title: title, state: state, recap: row["resumen"] as? String,
                       activity: row["actividad"] as? String, session: row["sesion"] as? String,
                       folder: row["carpeta"] as? String, branch: row["rama"] as? String)
        }
        guard jobs.count == rows.count else { return nil }
    }
}
