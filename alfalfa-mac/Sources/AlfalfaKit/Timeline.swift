import Foundation

public enum PermissionState: Equatable, Sendable {
    case pending
    case approved
    /// Aprobado también para el resto de la sesión: no volverá a preguntar por lo mismo.
    case approvedForSession
    case denied(String)
}

public enum SessionStatus: Equatable, Sendable {
    case starting
    case working
    case waitingPermission
    case idle
    case blocked
    case completed
    case failed(String)
    case interrupted

    public var label: String {
        switch self {
        case .starting: return "iniciando"
        case .working: return "trabajando"
        case .waitingPermission: return "permiso"
        case .idle: return "listo"
        case .blocked: return "bloqueada"
        case .completed: return "completada"
        case .failed: return "fallida"
        case .interrupted: return "Interrumpida · se puede reanudar"
        }
    }

    /// Hay un turno en marcha: es lo que el botón de detener puede parar.
    public var isRunning: Bool {
        switch self {
        case .starting, .working, .waitingPermission: return true
        default: return false
        }
    }

    public var isActive: Bool {
        switch self {
        case .starting, .working, .waitingPermission, .idle, .blocked: return true
        default: return false
        }
    }
}

public struct ToolEvent: Equatable, Identifiable, Sendable {
    public var id: Int
    public var role: String
    public var text: String
    /// Hora del evento (segundos desde 1970); 0 si no se conoce.
    public var time: Double = 0
    /// Lo que el motor dice de la llamada (`herramienta`); nil en motores anteriores.
    public var tool: ToolInfo? = nil

    /// Segundos entre el primer y el último evento de un grupo, o nil si no hay horas.
    public static func duration(of events: [ToolEvent]) -> Double? {
        let times = events.map(\.time).filter { $0 > 0 }
        guard let first = times.min(), let last = times.max(), last > first else { return nil }
        return last - first
    }

    /// «54 s», «3 min 5 s», «1 h 2 min».
    public static func durationText(_ seconds: Double) -> String {
        let total = max(1, Int(seconds.rounded()))
        if total < 60 { return "\(total) s" }
        if total < 3600 { return total % 60 == 0 ? "\(total / 60) min" : "\(total / 60) min \(total % 60) s" }
        return "\(total / 3600) h \(total % 3600 / 60) min"
    }

    /// Primera línea, sin el prefijo «Inicio ».
    public var title: String {
        let first = text.split(separator: "\n", maxSplits: 1, omittingEmptySubsequences: true).first.map(String.init) ?? text
        return first.hasPrefix("Inicio ") ? String(first.dropFirst(7)) : first
    }
}

public enum TimelineItem: Equatable, Identifiable, Sendable {
    case user(id: Int, text: String, queued: Bool)
    case agent(id: Int, text: String)
    case events(id: Int, [ToolEvent])
    case permission(id: Int, request: PermissionRequest, state: PermissionState)
    case notice(id: Int, text: String)
    /// Cierre del turno; `answered` cuando ya no se puede contestar (llegó otro mensaje).
    case wrapUp(id: Int, WrapUp, answered: Bool)
    /// Información local de la app (p. ej. `/estado`); no es del agente ni un aviso.
    case info(id: Int, title: String, rows: [InfoRow])
    /// «Conversación compactada»: lo anterior quedó resumido para el modelo.
    case compaction(id: Int, Compaction)

    public var id: Int {
        switch self {
        case let .user(id, _, _), let .agent(id, _), let .events(id, _), let .permission(id, _, _), let .notice(id, _),
             let .wrapUp(id, _, _), let .info(id, _, _), let .compaction(id, _): return id
        }
    }
}

public struct InfoRow: Equatable, Sendable {
    public var name: String
    public var value: String

    public init(_ name: String, _ value: String) {
        self.name = name
        self.value = value
    }
}

/// Estado de una conversación, derivado de los mensajes del protocolo. Es puro
/// para poder probarlo con JSONL grabado.
/// Los resúmenes de bloqueo del motor llevan JSON («Operaciones sin resolver: {…}»,
/// «Verificación pendiente: {…}»); aquí se dicen con palabras. Si no se entiende, queda igual.
public enum BlockedSummary {
    public static func readable(_ summary: String) -> String {
        if let object = json(after: "Operaciones sin resolver: ", in: summary) {
            let reasons = object.values.compactMap { $0 as? String }.map {
                $0.trimmingCharacters(in: CharacterSet(charactersIn: ". "))
            }
            let lead = object.count == 1 ? "Quedó una operación sin resolver" : "Quedaron \(object.count) operaciones sin resolver"
            let detail = reasons.isEmpty ? "" : ": " + Set(reasons).sorted().joined(separator: " · ")
            return lead + detail + ". Pide al agente que la revise o la repita."
        }
        if let object = json(after: "Verificación pendiente: ", in: summary) {
            switch object["status"] as? String {
            case "failed": return "La última comprobación falló; los cambios están sin verificar."
            case "stale": return "Hubo cambios después de la última comprobación; están sin verificar."
            case "changed_externally": return "Los archivos cambiaron fuera del agente después de comprobarlos."
            default: return "La verificación quedó pendiente."
            }
        }
        return summary
    }

    private static func json(after prefix: String, in text: String) -> [String: Any]? {
        guard text.hasPrefix(prefix), let data = text.dropFirst(prefix.count).data(using: .utf8) else { return nil }
        return (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
    }
}

/// Una orden aprobada que todavía corre.
public struct RunningCommand: Equatable, Sendable {
    public var id: String
    public var since: Double
}

public struct Timeline: Equatable, Sendable {
    public private(set) var items: [TimelineItem] = []
    public private(set) var status: SessionStatus = .starting
    public private(set) var session: SessionInfo?
    public private(set) var contextTokens = 0
    public private(set) var contextLimit = 0
    /// Desglose MCP del último `contexto`; nil en sesiones sin servidores MCP.
    public private(set) var mcpContext: McpContextUsage?
    /// Reparto estimado del último contexto medido; nil si el motor no lo dio.
    public private(set) var contextBreakdown: ContextBreakdown?
    /// Uso estimado, umbral de compactación y archivos de memoria; nil en motores anteriores.
    public private(set) var contextDetails: ContextDetails?
    /// El motor está compactando la conversación (entre turnos o dentro de uno).
    public private(set) var compacting = false
    /// El arnés mantiene el Mac despierto: sólo mientras trabaja un turno y con el ajuste encendido.
    public private(set) var keepingAwake = false
    /// Órdenes en segundo plano de la sesión (juegos, apps, servidores).
    public private(set) var processes: [BackgroundProcess] = []
    public var liveProcesses: [BackgroundProcess] { processes.filter(\.alive) }
    /// Subagentes de la sesión y sus últimos pasos (como mucho `subagentStepLimit` por agente).
    public private(set) var subagents: [Subagent] = []
    public private(set) var subagentSteps: [String: [AgentEvent]] = [:]
    public var workingSubagents: [Subagent] { subagents.filter(\.working) }
    /// El plan del agente y el último mensaje del usuario que había cuando lo envió.
    public private(set) var plan: Plan?
    private var planUserID: Int?
    /// El usuario escribió después del último plan: es «el plan de antes de tu último mensaje».
    public var planIsFromBeforeLastMessage: Bool {
        guard plan != nil, let last = lastUserID else { return false }
        return last != planUserID
    }
    /// Se enseña mientras hay trabajo o le quedan pasos; acabado y sin turno, se retira.
    public var visiblePlan: Plan? {
        guard let plan, !plan.steps.isEmpty else { return nil }
        return status.isRunning || !plan.isDone ? plan : nil
    }
    private var lastUserID: Int? {
        for item in items.reversed() { if case let .user(id, _, _) = item { return id } }
        return nil
    }
    public static let subagentStepLimit = 200
    public private(set) var totalTokens = 0
    /// Coste estimado de la sesión en USD (precios de models.dev); nil si el modelo no cobra por token.
    public private(set) var totalCost: Double?
    /// Tokens y coste estimado del último turno terminado.
    public private(set) var lastTurnTokens: Int?
    public private(set) var lastTurnCost: Double?
    /// Hora de cada mensaje del agente, por id de elemento: la fila de acciones la enseña.
    public private(set) var agentTimes: [Int: Double] = [:]
    /// Hora y turno de cada mensaje del usuario, por id de elemento: «Hace 12 minutos», volver y bifurcar.
    public private(set) var userTimes: [Int: Double] = [:]
    public private(set) var userTurns: [Int: String] = [:]
    /// Orden aprobada que aún no ha devuelto resultado, y desde cuándo (segundos desde 1970).
    /// Una compilación o un script largo tarda: la tarjeta lo enseña en vez de parecer colgada.
    public private(set) var runningPermission: RunningCommand?
    public private(set) var finished = false
    private var counter = 0

    public init() {}

    /// Conversación de una sesión guardada, lista para seguirla: queda «lista» y el cierre
    /// del último turno conserva sus botones.
    public init(history events: [AgentEvent], wrapUp: WrapUp?, context: SavedContext? = nil, tokens: Int = 0,
                cost: Double? = nil) {
        for event in events { apply(event) }
        if let context {
            contextTokens = context.tokens
            contextLimit = context.limit
            contextBreakdown = context.breakdown
            contextDetails = context.details
        }
        totalTokens = tokens
        totalCost = cost
        // Una sesión guardada no está trabajando: lo que quedó sin resultado, quedó cortado.
        closeOpenCalls()
        if let wrapUp { items.append(.wrapUp(id: nextID(), wrapUp, answered: false)) }
        status = .idle
        compacting = false
    }

    /// El proceso vuelve a arrancar sobre una conversación ya pintada. Con `waiting` se abre
    /// sin turno (`--esperar`): sigue «lista» hasta que llegue un mensaje.
    public mutating func restart(waiting: Bool = false) {
        status = waiting ? .idle : .starting
        finished = false
    }

    private mutating func nextID() -> Int { counter += 1; return counter }

    /// Mensaje local del usuario: se muestra en cola hasta que el agente lo incorpora.
    public mutating func addUser(_ text: String, queued: Bool) {
        closeWrapUps()
        let id = nextID()
        items.append(.user(id: id, text: text, queued: queued))
        userTimes[id] = Date().timeIntervalSince1970
    }

    /// El turno anterior al del mensaje `id`: volver a ese mensaje es bifurcar hasta ahí.
    /// nil si es el primero (volver es empezar de cero) o si aún no se sabe su turno.
    public func turnBefore(user id: Int) -> String?? {
        guard let turn = userTurns[id] else { return nil }
        var previous: String?
        for item in items {
            if case let .user(other, _, _) = item {
                if other == id { return .some(previous) }
                if let t = userTurns[other], t != turn { previous = t }
            }
        }
        return nil
    }

    /// Quita la caja de cierre (la ✕ de la tarjeta).
    public mutating func dismissWrapUp(id: Int) {
        items.removeAll { if case .wrapUp(id, _, _) = $0 { return true } else { return false } }
    }

    /// Un mensaje nuevo deja contestadas las cajas anteriores: sus botones ya no aplican.
    private mutating func closeWrapUps() {
        for index in items.indices {
            if case let .wrapUp(id, wrapUp, false) = items[index] { items[index] = .wrapUp(id: id, wrapUp, answered: true) }
        }
    }

    public mutating func addNotice(_ text: String) {
        items.append(.notice(id: nextID(), text: text))
    }

    public mutating func addInfo(_ title: String, _ rows: [InfoRow]) {
        items.append(.info(id: nextID(), title: title, rows: rows))
    }

    /// Texto de la última respuesta del agente, para `/copiar`.
    public var lastAgentText: String? {
        for item in items.reversed() { if case let .agent(_, text) = item { return text } }
        return nil
    }

    /// La conversación como Markdown, para `/exportar`.
    public func markdown(title: String) -> String {
        var parts = ["# " + title]
        for item in items {
            switch item {
            case let .user(_, text, _): parts.append("## Tú\n\n" + text)
            case let .agent(_, text): parts.append("## Agente\n\n" + text)
            case let .notice(_, text): parts.append("> " + text)
            case let .wrapUp(_, wrapUp, _): parts.append("> **Siguiente paso:** " + wrapUp.recap)
            case let .compaction(_, compaction):
                parts.append("---\n\n*Conversación compactada.* Resumen para el modelo:\n\n" + compaction.summary)
            default: break
            }
        }
        return parts.joined(separator: "\n\n") + "\n"
    }

    /// Cuota mensual usada de los proveedores que la tienen (el servicio Alfalfa).
    public var quotas: [Quota] = []

    public var pendingPermissions: [PermissionRequest] {
        items.compactMap { if case let .permission(_, request, .pending) = $0 { return request } else { return nil } }
    }

    public mutating func apply(_ message: AlfalfaMessage) {
        switch message {
        case let .session(info):
            session = info
            if status == .starting { status = .working }
        case let .event(event): apply(event)
        case let .permission(request):
            items.append(.permission(id: nextID(), request: request, state: .pending))
            status = .waitingPermission
        case let .permissionResolved(id, approved, reason, session):
            resolve(id: id, state: approved ? (session ? .approvedForSession : .approved) : .denied(reason))
            runningPermission = approved ? RunningCommand(id: id, since: Date().timeIntervalSince1970) : nil
            if pendingPermissions.isEmpty && status == .waitingPermission { status = .working }
        case let .context(tokens, limit, mcp, breakdown, details):
            contextTokens = tokens
            contextLimit = limit
            if let mcp { mcpContext = mcp }
            if let breakdown { contextBreakdown = breakdown }
            if let details { contextDetails = details }
        case let .error(text):
            items.append(.notice(id: nextID(), text: text))
        case let .turnFinished(outcome):
            runningPermission = nil
            compacting = false
            keepingAwake = false
            closeOpenCalls()
            // Los mensajes ya entregados sin turno conocido son de este turno.
            if let turn = outcome.turn {
                for item in items {
                    if case let .user(id, _, false) = item, userTurns[id] == nil { userTurns[id] = turn }
                }
            }
            // El turno acabó pero el proceso sigue vivo: la caja sigue activa salvo que lo interrumpan.
            totalTokens = outcome.tokens
            if let cost = outcome.cost { totalCost = cost }
            lastTurnTokens = outcome.turnTokens
            lastTurnCost = outcome.turnCost
            if outcome.exitCode == 130 || outcome.state == "interrupted" {
                status = .interrupted
            } else if outcome.state == "blocked" {
                status = .blocked
            } else {
                status = .idle
            }
            // El resumen suele llegar ya como texto del agente: no se repite.
            if !outcome.summary.isEmpty && outcome.exitCode != 0 && !showsAgentText(outcome.summary) {
                items.append(.notice(id: nextID(), text: BlockedSummary.readable(outcome.summary)))
            }
            if let wrapUp = outcome.wrapUp { items.append(.wrapUp(id: nextID(), wrapUp, answered: false)) }
        case let .finished(outcome):
            runningPermission = nil
            compacting = false
            keepingAwake = false
            closeOpenCalls()
            totalTokens = outcome.tokens
            if let cost = outcome.cost { totalCost = cost }
            finish(outcome: outcome)
        case let .processes(list):
            processes = list
        case let .subagents(list):
            subagents = list
        case let .plan(newPlan):
            plan = newPlan
            planUserID = lastUserID
        case let .subagentEvent(agent, event):
            var steps = subagentSteps[agent] ?? []
            steps.append(event)
            if steps.count > Self.subagentStepLimit { steps.removeFirst(steps.count - Self.subagentStepLimit) }
            subagentSteps[agent] = steps
        case let .quota(rows): quotas = rows
        case let .keepingAwake(on): keepingAwake = on
        case .sessions, .models, .providers, .webSearch, .activated, .reported, .update, .sessionChanged, .history,
             .ollamaChats:
            break
        }
    }

    private func showsAgentText(_ text: String) -> Bool {
        let wanted = text.trimmingCharacters(in: .whitespacesAndNewlines)
        return items.contains { item in
            if case let .agent(_, shown) = item { return shown.trimmingCharacters(in: .whitespacesAndNewlines) == wanted }
            return false
        }
    }

    private mutating func apply(_ event: AgentEvent) {
        if let compaction = event.compaction {
            switch compaction.state {
            case .running: compacting = true
            case .done:
                compacting = false
                items.append(.compaction(id: nextID(), compaction))
            case .failed:
                compacting = false
                items.append(.notice(id: nextID(), text: event.text))
            }
            return
        }
        switch event.role {
        case "user":
            closeWrapUps()
            // Un turno nuevo: lo que el anterior dejó sin resultado («Enviar ahora» durante una
            // orden) ya no está corriendo.
            closeOpenCalls()
            let id: Int
            if let index = items.firstIndex(where: { if case .user(_, event.text, true) = $0 { return true } else { return false } }),
               case let .user(existing, text, _) = items[index] {
                items[index] = .user(id: existing, text: text, queued: false)
                id = existing
            } else {
                id = nextID()
                items.append(.user(id: id, text: event.text, queued: false))
            }
            if event.time > 0 { userTimes[id] = event.time }
            if let turn = event.turn { userTurns[id] = turn }
            if status == .idle || status == .blocked { status = .working }
        case "agent", "assistant":
            // La misma respuesta dos veces seguidas (sesiones guardadas por motores anteriores,
            // cuando el modelo la repetía tras recordarle una operación sin resolver): una basta.
            if case let .agent(_, previous)? = items.last, previous == event.text { return }
            let id = nextID()
            items.append(.agent(id: id, text: event.text))
            if event.time > 0 { agentTimes[id] = event.time }
            if status == .idle || status == .blocked || status == .starting { status = .working }
        default:
            // El resultado de una herramienta (no su «Inicio») cierra la orden en marcha.
            if event.role == "tool", !event.text.hasPrefix("Inicio ") { runningPermission = nil }
            let tool = ToolEvent(id: nextID(), role: event.role, text: event.text, time: event.time, tool: event.tool)
            let info = tool.toolInfo
            if let index = groupAwaiting(info?.phase == .start ? nil : info?.name ?? "") {
                // El resultado de una orden que pidió permiso vuelve a su grupo, encima de la tarjeta,
                // y un aviso que llega mientras espera (un hook, un proceso que terminó) no lo parte.
                if case let .events(id, existing) = items[index] { items[index] = .events(id: id, existing + [tool]) }
            } else if let info, info.phase == .end, let (index, position) = interruptedCall(named: info.name) {
                // El resultado tardío de una llamada que la app dio por cortada («El proceso se
                // interrumpió…» al empezar el turno siguiente) sustituye a ese cierre.
                if case let .events(id, existing) = items[index] {
                    var events = existing
                    events[position] = tool
                    items[index] = .events(id: id, events)
                }
            } else if case let .events(id, existing)? = items.last {
                items[items.count - 1] = .events(id: id, existing + [tool])
            } else {
                items.append(.events(id: nextID(), [tool]))
            }
            // Un aviso del sistema entre turnos (el juego en segundo plano terminó) no es trabajo:
            // la sesión sigue lista y sin botón de detener.
            if event.role == "system" && (status == .idle || status == .blocked) { return }
            if status == .idle || status == .blocked || status == .starting { status = .working }
        }
    }

    /// Grupo cuya última llamada sigue esperando su resultado: el de `name` (un fin) o cualquiera
    /// (`""`, un aviso del sistema). nil para un inicio. Se mira por encima de permisos, avisos y
    /// grupos que sólo tienen avisos; un mensaje del agente o del usuario cierra el grupo.
    private func groupAwaiting(_ name: String?) -> Int? {
        guard let name else { return nil }
        for index in items.indices.reversed() {
            switch items[index] {
            case .permission, .notice: continue
            case let .events(_, events):
                if let open = ToolCalls.running(in: events), name.isEmpty || open.name == name { return index }
                if ToolCalls.calls(from: events).isEmpty { continue }
                return nil
            default: return nil
            }
        }
        return nil
    }

    /// El cierre puesto por la app (`ToolInfo.interruption`) más reciente de una llamada `name`.
    private func interruptedCall(named name: String) -> (Int, Int)? {
        for index in items.indices.reversed() {
            guard case let .events(_, events) = items[index] else { continue }
            if let position = events.lastIndex(where: { $0.tool?.interrupted == true && $0.tool?.name == name }) {
                return (index, position)
            }
        }
        return nil
    }

    /// Da por cortadas las llamadas que siguen sin resultado: el turno terminó o empezó otro.
    private mutating func closeOpenCalls() {
        for index in items.indices {
            guard case let .events(id, events) = items[index] else { continue }
            let open = ToolCalls.open(in: events)
            guard !open.isEmpty else { continue }
            let closings = open.map { call in
                ToolEvent(id: nextID(), role: "tool", text: call.name, tool: .interruption(of: call.name))
            }
            items[index] = .events(id: id, events + closings)
        }
    }

    private mutating func resolve(id: String, state: PermissionState) {
        for index in items.indices {
            if case let .permission(itemID, request, .pending) = items[index], request.id == id {
                items[index] = .permission(id: itemID, request: request, state: state)
            }
        }
    }

    private mutating func finish(outcome: Outcome) {
        finished = true
        denyPending(reason: "Sesión terminada")
        if outcome.exitCode == 130 || outcome.state == "interrupted" {
            status = .interrupted
        } else if outcome.exitCode == 0 || outcome.state == "completed" {
            status = .completed
        } else if outcome.state == "blocked" {
            // Bloqueada (operación sin resolver, verificación pendiente) no es un fallo: se sigue.
            status = .blocked
        } else {
            status = .failed(outcome.summary)
        }
    }

    /// El proceso salió. Si no hubo `fin`, o salió con 130, la sesión queda interrumpida.
    public mutating func processExited(code: Int32) {
        runningPermission = nil
        closeOpenCalls()
        compacting = false
        // macOS suelta la aserción con el proceso.
        keepingAwake = false
        // Con el proceso de alfalfa se cierran sus órdenes en segundo plano.
        processes = processes.map { var row = $0; if row.alive { row.alive = false; row.stopped = true }; return row }
        denyPending(reason: "Sesión terminada")
        if finished && code != 130 { return }
        if code == 130 || !finished {
            status = .interrupted
        }
    }

    private mutating func denyPending(reason: String) {
        for index in items.indices {
            if case let .permission(itemID, request, .pending) = items[index] {
                items[index] = .permission(id: itemID, request: request, state: .denied(reason))
            }
        }
    }

    /// Tokens en uso ahora: el último total medido o, si es mayor, lo que estima el motor
    /// con lo añadido después (la respuesta final, resultados de herramientas).
    public var contextUsed: Int { max(contextTokens, contextDetails?.estimate ?? 0) }

    /// Lo añadido desde la última medida («Desde la última actualización» en Claude Code).
    public var contextSinceMeasure: Int { max(0, contextUsed - contextTokens) }

    /// Umbral de la compactación automática; nil si el motor no lo dijo.
    public var compactThreshold: Int? { contextDetails?.threshold }

    /// Tokens que quedan hasta la compactación automática (0 si ya se pasó).
    public var tokensUntilCompaction: Int? { compactThreshold.map { max(0, $0 - contextUsed) } }

    /// Lo que la compactación automática nunca deja llenar (la reserva para el resumen).
    public var compactReserve: Int { compactThreshold.map { max(0, contextLimit - $0) } ?? 0 }

    public var contextFraction: Double {
        contextLimit > 0 ? min(1, Double(contextUsed) / Double(contextLimit)) : 0
    }

    /// Parte del camino hasta la compactación automática (0…1); nil sin umbral.
    public var compactionProgress: Double? {
        guard let threshold = compactThreshold, threshold > 0 else { return nil }
        return min(1, Double(contextUsed) / Double(threshold))
    }
}
