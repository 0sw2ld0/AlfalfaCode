import Foundation

/// Qué hace el agente ahora, para la fila viva de la conversación («Leyendo WorkLog.swift · 38s»).
///
/// Todo sale de la conversación: la frase de la llamada en curso (`en_curso` del motor) y las
/// horas de los eventos. Nada depende de un reloj de la vista, así que redibujar no reinicia
/// el contador.
public struct LiveActivity: Equatable, Sendable {
    public enum Phase: Equatable, Sendable {
        /// El modelo razona: no hay herramienta en curso.
        case thinking
        /// Una herramienta está corriendo.
        case working
        /// Una llamada espera el permiso de la persona.
        case waiting
        /// Terminado: la fila queda quieta con el resumen.
        case done
    }

    public static let thinkingPhrase = "Pensando"
    public static let waitingPhrase = "Esperando tu permiso"

    public var phase: Phase
    /// «Pensando», «Leyendo WorkLog.swift», «Esperando tu permiso para pasar las pruebas».
    public var phrase: String
    /// Desde cuándo (segundos desde 1970); nil si los eventos no traen hora.
    public var since: Double?

    public init(phase: Phase, phrase: String, since: Double?) {
        self.phase = phase
        self.phrase = phrase
        self.since = since
    }

    /// El agente razona desde `since` (el último mensaje o el último resultado). Con `preparing`
    /// (el motor guarda la instantánea para deshacer), la fila dice eso en vez de «Pensando».
    public static func thinking(since: Double?, preparing: String? = nil) -> LiveActivity {
        if let preparing { return LiveActivity(phase: .working, phrase: preparing, since: since) }
        return LiveActivity(phase: .thinking, phrase: thinkingPhrase, since: since)
    }

    /// Lo que hace un grupo de llamadas que sigue en marcha: la llamada sin resultado o, si no
    /// hay, «Pensando» (o `preparing`) desde el último evento. Con `waiting`, la llamada espera un permiso.
    public static func of(group events: [ToolEvent], waiting: Bool = false, preparing: String? = nil) -> LiveActivity {
        let calls = ToolCalls.calls(from: events)
        let lastTime = events.last { $0.time > 0 }?.time
        guard let current = calls.last(where: { !$0.finished }) else {
            return waiting ? LiveActivity(phase: .waiting, phrase: waitingPhrase, since: lastTime)
                : .thinking(since: lastTime, preparing: preparing)
        }
        let since = current.startTime ?? lastTime
        if waiting {
            return LiveActivity(phase: .waiting, phrase: waitingPhrase(for: current), since: since)
        }
        return LiveActivity(phase: .working, phrase: current.ongoing ?? current.description, since: since)
    }

    /// «Esperando tu permiso para pasar las pruebas» cuando la descripción es un infinitivo en
    /// español (el motor lo pasó a gerundio); si no, sólo «Esperando tu permiso».
    static func waitingPhrase(for call: ToolCall) -> String {
        guard let ongoing = call.ongoing, ongoing != call.description, let first = call.description.first
        else { return waitingPhrase }
        return waitingPhrase + " para " + first.lowercased() + call.description.dropFirst()
    }

    /// Segundos desde `since` hasta `now`; nil sin hora de inicio.
    public func elapsed(at now: Date) -> Double? {
        since.map { max(0, now.timeIntervalSince1970 - $0) }
    }

    /// Lo que lee VoiceOver: «Pensando, 38 segundos».
    public func spoken(at now: Date) -> String {
        guard let seconds = elapsed(at: now) else { return phrase }
        return phrase + ", " + ElapsedTime.spoken(seconds)
    }
}

/// El tiempo de la fila viva, corto como en la app de Claude: «38s», «1min 7s», «1h 2min».
public enum ElapsedTime {
    public static func text(_ seconds: Double) -> String {
        let total = max(0, Int(seconds.rounded(.down)))
        if total < 60 { return "\(total)s" }
        if total < 3600 { return total % 60 == 0 ? "\(total / 60)min" : "\(total / 60)min \(total % 60)s" }
        let minutes = total % 3600 / 60
        return minutes == 0 ? "\(total / 3600)h" : "\(total / 3600)h \(minutes)min"
    }

    /// Para VoiceOver: «38 segundos», «1 minuto 7 segundos», «2 horas 5 minutos».
    public static func spoken(_ seconds: Double) -> String {
        let total = max(0, Int(seconds.rounded(.down)))
        func n(_ value: Int, _ one: String, _ many: String) -> String { "\(value) \(value == 1 ? one : many)" }
        if total < 60 { return n(total, "segundo", "segundos") }
        if total < 3600 {
            let minutes = n(total / 60, "minuto", "minutos")
            return total % 60 == 0 ? minutes : minutes + " " + n(total % 60, "segundo", "segundos")
        }
        let hours = n(total / 3600, "hora", "horas")
        let minutes = total % 3600 / 60
        return minutes == 0 ? hours : hours + " " + n(minutes, "minuto", "minutos")
    }
}

extension ToolCall {
    /// Lo que tardó (del inicio al resultado), o nil si falta alguna hora.
    public var duration: Double? {
        guard let startTime, let endTime, endTime >= startTime else { return nil }
        return endTime - startTime
    }

    /// La frase en gerundio y la hora del evento que abre la llamada.
    func started(_ event: ToolEvent, _ info: ToolInfo) -> ToolCall {
        var call = self
        call.ongoing = info.ongoing
        call.startTime = event.time > 0 ? event.time : nil
        return call
    }
}

extension Timeline {
    /// Desde cuándo piensa el agente cuando no hay grupo de llamadas en marcha: la hora del
    /// último mensaje (de la persona o del agente) o del último evento de un grupo.
    public var thinkingSince: Double? {
        for item in items.reversed() {
            switch item {
            case let .user(id, _, false): return userTimes[id]
            case let .agent(id, _): return agentTimes[id]
            case let .events(_, events): return events.last { $0.time > 0 }?.time
            default: continue
            }
        }
        return nil
    }
}
