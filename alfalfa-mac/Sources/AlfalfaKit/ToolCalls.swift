import Foundation

/// Datos de una llamada a herramienta que manda el motor en `herramienta` del evento:
/// su nombre, si abre o cierra la llamada y, al abrir, qué hace dicho para la persona.
public struct ToolInfo: Equatable, Sendable {
    public enum Phase: String, Sendable { case start = "inicio", end = "fin" }

    public var name: String
    public var phase: Phase
    /// «Buscar dónde se guarda session.json»: el `description` de la orden o el generado.
    public var description: String?
    /// La orden tal cual («git status --short»), sólo en `run_command` y `run_checks`.
    public var command: String?
    public var cwd: String?
    /// El archivo o la página de la llamada (`objetivo`).
    public var target: String?
    public var exitCode: Int?
    public var failed: Bool

    public init(name: String, phase: Phase, description: String? = nil, command: String? = nil, cwd: String? = nil,
                target: String? = nil, exitCode: Int? = nil, failed: Bool = false) {
        self.name = name
        self.phase = phase
        self.description = description
        self.command = command
        self.cwd = cwd
        self.target = target
        self.exitCode = exitCode
        self.failed = failed
    }

    /// Lee `herramienta`; nil si falta o no tiene nombre y fase.
    public static func parse(_ value: Any?) -> ToolInfo? {
        guard let object = value as? [String: Any], let name = object["nombre"] as? String, !name.isEmpty,
              let phase = (object["fase"] as? String).flatMap(Phase.init(rawValue:)) else { return nil }
        func text(_ key: String) -> String? {
            guard let value = object[key] as? String else { return nil }
            let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
            return trimmed.isEmpty ? nil : trimmed
        }
        return ToolInfo(name: name, phase: phase, description: text("descripcion"), command: text("orden"),
                        cwd: text("cwd"), target: text("objetivo"), exitCode: object["codigo"] as? Int, failed: object["error"] as? Bool ?? false)
    }
}

/// Clase de herramienta, para el resumen del grupo («Ejecutó 3 comandos, leyó 2 archivos»).
public enum ToolKind: Equatable, Sendable {
    case command, read, list, search, edit, web, image, app, agent, plan, tools, other

    public init(tool name: String) {
        switch name {
        case "run_command", "run_checks": self = .command
        case "read_file": self = .read
        case "list_files": self = .list
        case "search": self = .search
        case "write_file", "edit_file", "apply_patch": self = .edit
        case "web_search", "read_web": self = .web
        case "inspect_image": self = .image
        case "app_list", "app_state", "app_action": self = .app
        case "spawn_agent", "wait_agent", "send_input", "close_agent", "list_agents": self = .agent
        case "update_plan": self = .plan
        case "buscar_herramientas": self = .tools
        default: self = .other
        }
    }
}

/// Una llamada a herramienta: su inicio y, cuando llega, su resultado.
public struct ToolCall: Equatable, Identifiable, Sendable {
    public var id: Int
    public var name: String
    public var kind: ToolKind
    /// Lo que enseña la fila: la descripción del motor o un texto de respaldo.
    public var description: String
    /// La orden de terminal, para el bloque con `$`.
    public var command: String?
    /// La llamada tal como la pidió el modelo, para las herramientas que no son órdenes.
    public var request: String?
    /// Archivo o página a la que se refiere, para no contar dos veces el mismo al resumir.
    public var target: String?
    public var output: String?
    public var exitCode: Int?
    public var failed = false
    public var finished = false
}

/// Una fila del grupo: una llamada o un aviso del sistema entre llamadas.
public enum WorkEntry: Equatable, Identifiable, Sendable {
    case call(ToolCall)
    case note(ToolEvent)

    public var id: Int {
        switch self {
        case let .call(call): return call.id
        case let .note(event): return event.id
        }
    }
}

public enum ToolCalls {
    /// Hasta dónde se enseña la salida de una llamada; el resto queda en la sesión.
    public static let outputLimit = 20000

    /// Junta cada inicio con su resultado. Las herramientas corren una tras otra, así que un
    /// resultado cierra la última llamada abierta con su nombre; uno sin inicio (sesiones
    /// antiguas, operaciones heredadas) es una llamada ya terminada.
    public static func entries(from events: [ToolEvent]) -> [WorkEntry] {
        var entries: [WorkEntry] = []
        for event in events where !event.text.isEmpty {
            guard let info = event.toolInfo else {
                entries.append(.note(event))
                continue
            }
            switch info.phase {
            case .start:
                entries.append(.call(start(event, info)))
            case .end:
                if let index = entries.lastIndex(where: {
                    if case let .call(call) = $0 { return !call.finished && call.name == info.name }
                    return false
                }), case var .call(call) = entries[index] {
                    finish(&call, event, info)
                    entries[index] = .call(call)
                } else {
                    var call = ToolCall(id: event.id, name: info.name, kind: ToolKind(tool: info.name),
                                        description: fallbackDescription(info.name, nil))
                    finish(&call, event, info)
                    entries.append(.call(call))
                }
            }
        }
        return entries
    }

    public static func calls(from events: [ToolEvent]) -> [ToolCall] {
        entries(from: events).compactMap { if case let .call(call) = $0 { return call } else { return nil } }
    }

    /// La llamada que todavía no tiene resultado (la que está corriendo), si la hay.
    public static func running(in events: [ToolEvent]) -> ToolCall? {
        calls(from: events).last { !$0.finished }
    }

    /// «Ejecutó 3 comandos, leyó 2 archivos, editó 1 archivo», en el orden en que aparecen.
    public static func summary(_ calls: [ToolCall]) -> String {
        var order: [ToolKind] = []
        var groups: [ToolKind: [ToolCall]] = [:]
        for call in calls {
            if groups[call.kind] == nil { order.append(call.kind) }
            groups[call.kind, default: []].append(call)
        }
        let parts = order.map { phrase($0, groups[$0] ?? []) }
        guard let first = parts.first else { return "" }
        return ([first.prefix(1).uppercased() + first.dropFirst()] + parts.dropFirst()).joined(separator: ", ")
    }

    // MARK: - Privado

    private static func phrase(_ kind: ToolKind, _ calls: [ToolCall]) -> String {
        let count = calls.count
        let distinct = Set(calls.map { $0.target ?? "#\($0.id)" }).count
        func n(_ value: Int, _ one: String, _ many: String) -> String { "\(value) \(value == 1 ? one : many)" }
        switch kind {
        case .command: return "ejecutó " + n(count, "comando", "comandos")
        case .read: return "leyó " + n(distinct, "archivo", "archivos")
        case .list: return "exploró " + n(distinct, "carpeta", "carpetas")
        case .search: return "hizo " + n(count, "búsqueda", "búsquedas")
        case .edit: return "editó " + n(distinct, "archivo", "archivos")
        case .web: return count == 1 ? "consultó la web" : "consultó la web \(count) veces"
        case .image: return "miró " + n(distinct, "imagen", "imágenes")
        case .app: return count == 1 ? "usó una app" : "hizo \(count) acciones en apps"
        case .agent:
            let started = calls.filter { $0.name == "spawn_agent" }.count
            return started > 0 ? "lanzó " + n(started, "subagente", "subagentes") : "coordinó subagentes"
        case .plan: return "actualizó el plan"
        case .tools: return "cargó herramientas"
        case .other: return "usó " + n(count, "herramienta", "herramientas")
        }
    }

    private static func start(_ event: ToolEvent, _ info: ToolInfo) -> ToolCall {
        let request = event.title
        let path = info.target ?? pythonField("path", in: request)
        let command = info.command
        let description = info.description ?? pythonField("description", in: request)
            ?? pythonField("purpose", in: request) ?? fallbackDescription(info.name, path)
        let kind = ToolKind(tool: info.name)
        return ToolCall(id: event.id, name: info.name, kind: kind, description: description, command: command,
                        request: kind == .command && command != nil ? nil : request,
                        target: path ?? pythonField("url", in: request))
    }

    private static func finish(_ call: inout ToolCall, _ event: ToolEvent, _ info: ToolInfo) {
        call.finished = true
        let body = event.text.split(separator: "\n", maxSplits: 1, omittingEmptySubsequences: false)
        let raw = body.count > 1 ? String(body[1]) : ""
        var object = (raw.data(using: .utf8).flatMap { try? JSONSerialization.jsonObject(with: $0) }) as? [String: Any]
        // Una salida demasiado larga llega envuelta: {"truncated": true, "text": "{…"}.
        if let wrapped = object, wrapped["truncated"] as? Bool == true, let inner = wrapped["text"] as? String {
            object = nil
            call.output = clip(inner)
        }
        if let object {
            if call.command == nil, let argv = object["argv"] as? [String] {
                call.command = argv.map(shellQuoted).joined(separator: " ")
            }
            if call.description == fallbackDescription(call.name, call.target),
               let purpose = (object["purpose"] as? String)?.trimmingCharacters(in: .whitespacesAndNewlines),
               !purpose.isEmpty {
                call.description = purpose
            }
            if call.target == nil, let path = object["path"] as? String { call.target = path }
            call.exitCode = info.exitCode ?? object["exit_code"] as? Int
            if let error = object["error"] as? String {
                call.failed = true
                call.output = clip(error)
            } else if let output = object["output"] as? String {
                call.output = clip(output)
            } else if let text = object["text"] as? String {
                call.output = clip(text)
            } else {
                call.output = clip(pretty(object) ?? raw)
            }
        } else if call.output == nil {
            call.output = clip(raw)
        }
        if info.failed { call.failed = true }
        if let code = call.exitCode, code != 0 { call.failed = true }
        if call.kind == .command, call.request != nil, call.command != nil { call.request = nil }
    }

    /// Lo que se dice si el motor no mandó descripción (motores anteriores, sesiones guardadas).
    static func fallbackDescription(_ name: String, _ path: String?) -> String {
        let file = path.map { ($0 as NSString).lastPathComponent }.flatMap { $0.isEmpty ? nil : $0 }
        switch name {
        case "run_command": return "Ejecutar una orden"
        case "run_checks": return "Pasar una comprobación"
        case "read_file": return "Leer " + (file ?? "un archivo")
        case "list_files": return file.map { "Ver los archivos de " + $0 } ?? "Ver los archivos del proyecto"
        case "search": return "Buscar en el proyecto"
        case "write_file": return "Escribir " + (file ?? "un archivo")
        case "edit_file": return "Editar " + (file ?? "un archivo")
        case "apply_patch": return "Aplicar un parche"
        case "inspect_image": return "Mirar " + (file ?? "una imagen")
        case "web_search": return "Buscar en la web"
        case "read_web": return "Leer una página web"
        case "update_plan": return "Actualizar el plan"
        case "command_status": return "Ver cómo va un proceso"
        case "spawn_agent": return "Encargar a un subagente"
        case "wait_agent": return "Esperar a los subagentes"
        case "wrap_up": return "Cerrar el turno"
        default: return "Usar " + name
        }
    }

    /// `'clave': 'valor'` del repr de Python que llevan los inicios («Inicio read_file · {'path': 'x'}»).
    private static func pythonField(_ key: String, in text: String) -> String? {
        guard let range = text.range(of: "'\(key)': '") else { return nil }
        var value = ""
        var escaped = false
        for character in text[range.upperBound...] {
            if escaped { value.append(character); escaped = false; continue }
            if character == "\\" { escaped = true; continue }
            if character == "'" { return value.isEmpty ? nil : value }
            value.append(character)
        }
        return nil
    }

    private static func shellQuoted(_ argument: String) -> String {
        let safe = CharacterSet(charactersIn: "@%+=:,./-_").union(.alphanumerics)
        if !argument.isEmpty, argument.unicodeScalars.allSatisfy(safe.contains) { return argument }
        return "'" + argument.replacingOccurrences(of: "'", with: "'\"'\"'") + "'"
    }

    private static func pretty(_ object: [String: Any]) -> String? {
        guard let data = try? JSONSerialization.data(withJSONObject: object, options: [.prettyPrinted, .sortedKeys])
        else { return nil }
        return String(data: data, encoding: .utf8)
    }

    private static func clip(_ text: String) -> String {
        text.count > outputLimit ? String(text.prefix(outputLimit)) + "\n…" : text
    }
}

extension ToolEvent {
    /// Los datos de la llamada: los del motor o, si no vienen, los que se leen del texto
    /// («Inicio read_file · …» abre; «read_file\n{…}» cierra). nil en los avisos del sistema.
    public var toolInfo: ToolInfo? {
        if let tool { return tool }
        guard role == "tool" else { return nil }
        if text.hasPrefix("Inicio ") {
            let head = title
            let name = head.components(separatedBy: " · ").first ?? head
            return name.isEmpty ? nil : ToolInfo(name: name, phase: .start)
        }
        let name = text.split(separator: "\n", maxSplits: 1, omittingEmptySubsequences: false).first.map(String.init) ?? ""
        return name.isEmpty || name.contains(" ") ? nil : ToolInfo(name: name, phase: .end)
    }
}
