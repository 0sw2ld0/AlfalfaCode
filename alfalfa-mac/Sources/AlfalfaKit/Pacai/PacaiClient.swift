import Foundation

/// El cliente PACAI 0.3 de la app (`alfalfa --pacai`, perfil `work` y extensión `alfalfa`). Abre la sesión,
/// convierte cada `AlfalfaCommand` en sus mensajes y cada mensaje del agente en los mismos `AlfalfaMessage` que da
/// `--stdio` v2, para que la interfaz vea lo mismo con los dos protocolos. No lee ni escribe: `AlfalfaProcess` le
/// pasa cada línea desde su cola y escribe lo que devuelve.
public final class PacaiClient: @unchecked Sendable {

    // MARK: - Tipos

    /// Con qué abre la app la sesión: un encargo con sus adjuntos (sin eco: la app ya lo pintó), seguir sin
    /// mensaje una sesión que no terminó bien, o nada (la sesión espera un mensaje).
    public struct Opening: Equatable, Sendable {
        public var task: String?
        public var attachments: [String]
        public var continueWithoutMessage: Bool

        public init(task: String? = nil, attachments: [String] = [], continueWithoutMessage: Bool = false) {
            self.task = task
            self.attachments = attachments
            self.continueWithoutMessage = continueWithoutMessage
        }
    }

    /// Lo que hay que escribir al motor y lo que la app tiene que ver.
    public struct Output {
        public var lines: [String] = []
        public var events: [AlfalfaMessage] = []

        public init(lines: [String] = [], events: [AlfalfaMessage] = []) {
            self.lines = lines
            self.events = events
        }

        mutating func append(_ other: Output) {
            lines += other.lines
            events += other.events
        }
    }

    struct OpenTool {
        var run: String
        var name: String
        var synthetic: Bool
    }

    struct OpenMessage {
        var run: String
        var role: String
        var text: String
        var time: Double
        var turn: Any?
    }

    // MARK: - Constantes

    public static let version = "0.3"
    /// La herramienta del cliente que hace de navegador de la app.
    static let browser = "browser"
    /// El motivo del eco local de una respuesta a un permiso: el mismo que daba v2.
    static let clientAnswer = "Respuesta del cliente"
    /// El motivo de un permiso que contestó quien lanzó la sesión, visto por quien sólo mira.
    static let relayedAnswer = "Lo contestó quien lanzó el encargo"

    // MARK: - Propiedades

    public let session: String
    public let opening: Opening
    /// La ejecución raíz en curso (un turno o un trabajo como una cola); nil entre turnos.
    public private(set) var rootRun: String?

    // MARK: - Propiedades internas

    let clock: () -> Date
    let ids: () -> String
    private var seq = 0
    /// Se mandó un `input` que abre ejecución y aún no llegó su `run_started`: lo que se escriba mientras espera
    /// entra en esa ejecución (como en v2, donde el mensaje se sumaba al turno que empezaba).
    var awaitingRun = false
    /// El id con el que salió ese `input`, si lo tenía: quitarlo de la cola antes de que empiece su ejecución
    /// (`alfalfa.dequeued`) también deja de esperarla.
    var awaitingInput: String?
    var held: [AlfalfaCommand] = []
    /// Herramientas del cliente (`browser`) abiertas: sólo ésas reciben respuesta.
    var clientTools: Set<String> = []
    private var subruns: [String: String] = [:]
    private var tools: [String: OpenTool] = [:]
    private var messages: [String: OpenMessage] = [:]
    private var closings: [String: [String: Any]] = [:]
    /// Lo de v2 que trae un `message_start` en `payload.alfalfa` además del turno, por id del mensaje: sale en el
    /// `evento` de su `message_end`.
    private var messageFields: [String: [String: Any]] = [:]

    // MARK: - Inicializadores

    public init(opening: Opening = Opening(), session: String = PacaiClient.newSession(),
                clock: @escaping () -> Date = Date.init, ids: @escaping () -> String = PacaiClient.newID) {
        self.opening = opening
        self.session = session
        self.clock = clock
        self.ids = ids
    }

    // MARK: - Métodos públicos

    /// 128 bits al azar, como pide PACAI para el id de una sesión.
    public static func newSession() -> String { "s_" + randomHex(bytes: 16) }

    public static func newID() -> String { "c_" + randomHex(bytes: 8) }

    private static func randomHex(bytes: Int) -> String {
        (0..<bytes).map { _ in String(format: "%02x", UInt8.random(in: .min ... .max)) }.joined()
    }

    /// El `hello` y, encadenado tras él, el `input` con el que abre la sesión, si lo hay.
    public func start() -> Output {
        var output = Output(lines: [envelope("hello", [
            "versions": [Self.version], "profiles": ["work"], "extensions": ["alfalfa"],
            "client_tools": [["name": Self.browser, "description": "El navegador propio de la app de Mac",
                              "input_schema": ["type": "object"]]]
        ])])
        if opening.task != nil || !opening.attachments.isEmpty {
            awaitingRun = true
            output.lines.append(envelope("input", ["blocks": Self.blocks(opening.task ?? "", opening.attachments),
                                                   "alfalfa": ["echo": false]]))
        } else if opening.continueWithoutMessage {
            awaitingRun = true
            output.lines.append(envelope("input", ["blocks": [Any](), "alfalfa": ["continue": true]]))
        }
        return output
    }

    /// Un mensaje del agente: los `AlfalfaMessage` que le corresponden y, a veces, mensajes que ya pueden salir.
    public func receive(_ line: String) -> Output {
        guard let data = line.data(using: .utf8),
              let message = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
              message["pacai"] as? String == Self.version, let type = message["type"] as? String,
              let id = message["id"] as? String else { return Output() }
        let payload = message["payload"] as? [String: Any] ?? [:]
        let time = Self.seconds(message["time"])
        if type.hasPrefix("alfalfa.") {
            let name = String(type.dropFirst("alfalfa.".count))
            var output = Output(events: extensionEvent(name, payload, time))
            if name == "dequeued" { output.append(dequeued(payload)) }
            return output
        }
        switch type {
        case "run_started": return runStarted(id, payload)
        case "run_finished": return Output(events: runFinished(payload))
        case "message_start", "message_delta", "message_end":
            if type == "message_start" { keepFields(of: id, payload) }
            return Output(events: streamed(type, id, payload, time))
        case "tool_start": return Output(events: toolStarted(id, payload, time))
        case "tool_end": return Output(events: toolEnded(payload, time))
        case "approval_request":
            return Output(events: decoded(PacaiExtension.permission(payload, id: id)))
        case "approval_result":
            guard let request = message["reply_to"] as? String else { return Output() }
            if payload["relayed"] as? Bool == true { return Output(events: [Self.relayed(request, payload)]) }
            // Lo que cuenta el agente: la ejecución terminó o se interrumpió sin respuesta, o caducó.
            guard payload["decision"] as? String != "approved" else { return Output() }
            let reason = payload["reason"] as? String
                ?? (payload["decision"] as? String == "expired" ? "Sin respuesta a tiempo" : "")
            return Output(events: [.permissionResolved(id: request, approved: false, reason: reason)])
        case "usage": return Output(events: usage(payload))
        case "plan_updated":
            let extra = payload["alfalfa"] as? [String: Any]
            return Output(events: decoded(PacaiExtension.plan(payload, explanation: extra?["explanation"])))
        case "error": return error(payload)
        case "close": return Output(events: closed(payload))
        default: return Output()                    // hello, session_started, heartbeat, diff_updated…
        }
    }

    // MARK: - Métodos privados

    func envelope(_ type: String, _ payload: [String: Any], id: String? = nil, replyTo: String? = nil) -> String {
        seq += 1
        var object: [String: Any] = ["pacai": Self.version, "id": id ?? ids(), "type": type, "session": session,
                                     "sender": "client", "seq": seq, "time": Self.timestamp(clock()),
                                     "payload": payload]
        if let replyTo { object["reply_to"] = replyTo }
        let options: JSONSerialization.WritingOptions = [.sortedKeys, .withoutEscapingSlashes]
        let data = (try? JSONSerialization.data(withJSONObject: object, options: options)) ?? Data()
        return String(decoding: data, as: UTF8.self) + "\n"
    }

    /// La respuesta del dueño a un permiso, que el agente reenvía a quien sólo mira (`relayed`): la tarjeta se cierra
    /// con lo que decidió él.
    static func relayed(_ request: String, _ payload: [String: Any]) -> AlfalfaMessage {
        let approved = payload["decision"] as? String == "approved"
        let own = (payload["alfalfa"] as? [String: Any])?["scope"] as? String
        return .permissionResolved(id: request, approved: approved,
                                   reason: approved ? relayedAnswer : payload["reason"] as? String ?? relayedAnswer,
                                   session: approved && payload["scope"] as? String == "session",
                                   always: approved && own == "always")
    }

    func decoded(_ object: [String: Any]?) -> [AlfalfaMessage] {
        object.flatMap(AlfalfaMessage.decode(object:)).map { [$0] } ?? []
    }

    private func runStarted(_ id: String, _ payload: [String: Any]) -> Output {
        guard payload["parent_run_id"] == nil else {
            subruns[id] = (payload["alfalfa"] as? [String: Any])?["agent"] as? String ?? ""
            return Output()
        }
        rootRun = id
        awaitingRun = false
        return releaseHeld()
    }

    private func runFinished(_ payload: [String: Any]) -> [AlfalfaMessage] {
        guard let run = payload["run_id"] as? String else { return [] }
        subruns.removeValue(forKey: run)
        guard run == rootRun else { return [] }
        rootRun = nil
        // Sólo un turno trae su `alfalfa.closing`; un trabajo (una cola, guardar una prueba) termina sin más.
        return closings.removeValue(forKey: run).map { decoded(PacaiExtension.turnFinished($0)) } ?? []
    }

    private func streamed(_ type: String, _ id: String, _ payload: [String: Any], _ time: Double) -> [AlfalfaMessage] {
        switch type {
        case "message_start":
            guard let run = payload["run_id"] as? String else { return [] }
            messages[id] = OpenMessage(run: run, role: payload["role"] as? String ?? "assistant", text: "",
                                       time: time, turn: (payload["alfalfa"] as? [String: Any])?["turn"])
            return []
        case "message_delta":
            guard let key = payload["message_id"] as? String else { return [] }
            messages[key]?.text += Self.text(of: payload["blocks"])
            return []
        default:
            guard let key = payload["message_id"] as? String, var open = messages.removeValue(forKey: key) else {
                return []
            }
            open.text += Self.text(of: payload["blocks"])
            let role = open.role == "assistant" ? "agent" : open.role
            if let agent = subruns[open.run] {
                return decoded(["tipo": "subagente_evento", "agente": agent, "rol": role, "texto": open.text,
                                "hora": open.time])
            }
            var event: [String: Any] = ["rol": role, "texto": open.text, "hora": open.time]
            event["turno"] = open.turn
            event.merge(messageFields.removeValue(forKey: key) ?? [:]) { current, _ in current }
            return decoded(PacaiExtension.agentEvent(event))
        }
    }

    /// Lo importado sin autorizar (`imported`) viaja en el `message_start` del aviso: el texto es para cualquier
    /// cliente y los datos, para que la app ofrezca «Revisar y autorizar…».
    private func keepFields(of id: String, _ payload: [String: Any]) {
        guard let imported = (payload["alfalfa"] as? [String: Any])?["imported"] else { return }
        messageFields[id] = ["importadas": PacaiV2.fields(imported)]
    }

    private func toolStarted(_ id: String, _ payload: [String: Any], _ time: Double) -> [AlfalfaMessage] {
        let name = payload["name"] as? String ?? "tool"
        let extra = payload["alfalfa"] as? [String: Any] ?? [:]
        if payload["side"] as? String == "client" {
            guard name == Self.browser else { return [] }
            clientTools.insert(id)
            var request = PacaiBrowser.request(payload["arguments"] as? [String: Any] ?? [:])
            request["tipo"] = "navegador"
            request["id"] = id
            return decoded(request)
        }
        let run = payload["run_id"] as? String ?? ""
        let synthetic = extra["synthetic"] as? Bool ?? false
        tools[id] = OpenTool(run: run, name: name, synthetic: synthetic)
        guard !synthetic else { return [] }               // la abrió un permiso: v2 no tiene herramienta
        return toolEvent(run: run, text: extra["text"] as? String ?? "Inicio " + name, extra: extra, time: time)
    }

    private func toolEnded(_ payload: [String: Any], _ time: Double) -> [AlfalfaMessage] {
        guard let id = payload["tool_call_id"] as? String else { return [] }
        clientTools.remove(id)
        guard let tool = tools.removeValue(forKey: id), !tool.synthetic else { return [] }
        let extra = payload["alfalfa"] as? [String: Any]
        // Un `cancelled` sin más lo pone PACAI al cerrar la ejecución: v2 no lo contaba (la app cierra sola lo que
        // quedó abierto al terminar el turno).
        guard let extra else { return [] }
        let output = Self.text(of: payload["output"])
        let text = extra["text"] as? String ?? tool.name + (output.isEmpty ? "" : "\n" + output)
        return toolEvent(run: tool.run, text: text, extra: extra, time: time)
    }

    private func toolEvent(run: String, text: String, extra: [String: Any], time: Double) -> [AlfalfaMessage] {
        if let agent = subruns[run] {
            return decoded(["tipo": "subagente_evento", "agente": agent, "rol": "tool", "texto": text, "hora": time])
        }
        var event: [String: Any] = ["rol": "tool", "texto": text, "hora": time]
        event["turno"] = extra["turn"]
        if let tool = extra["tool"] { event["herramienta"] = PacaiV2.fields(tool) }
        return decoded(PacaiExtension.agentEvent(event))
    }

    private func usage(_ payload: [String: Any]) -> [AlfalfaMessage] {
        // El contexto llega entero en `alfalfa.context` y el del turno en `alfalfa.closing`: aquí, el de la sesión.
        guard let total = (payload["alfalfa"] as? [String: Any])?["total_tokens"] else { return [] }
        var object: [String: Any] = ["tipo": "uso_sesion", "tokens": total]
        object["coste"] = (payload["cost"] as? [String: Any])?["amount"]
        return decoded(object)
    }

    private func error(_ payload: [String: Any]) -> Output {
        var object = PacaiV2.object(payload["alfalfa"])
        object["tipo"] = "error"
        object["texto"] = payload["message"] as? String ?? ""
        PacaiExtension.fixValues(&object)
        var output = Output(events: decoded(object))
        if awaitingRun {
            // El `input` no abrió ejecución (el motor lo rechazó): lo que esperaba sale ya, como mensaje nuevo.
            awaitingRun = false
            output.append(releaseHeld())
        }
        return output
    }

    /// Se quitó de la cola el `input` que iba a abrir la ejecución antes de que empezara (llegó mientras el motor
    /// compactaba, por ejemplo): su `run_started` ya no llegará. Sin esto, lo que se escribía después se quedaba
    /// aquí «en cola» para siempre; ahora sale ya, como mensaje nuevo.
    private func dequeued(_ payload: [String: Any]) -> Output {
        guard awaitingRun, payload["removed"] as? Bool == true, let id = payload["id"] as? String,
              id == awaitingInput else { return Output() }
        awaitingRun = false
        awaitingInput = nil
        return releaseHeld()
    }

    private func closed(_ payload: [String: Any]) -> [AlfalfaMessage] {
        var object = PacaiV2.object(payload["alfalfa"])
        object["tipo"] = "fin"
        if object["estado"] == nil { object["estado"] = "failed" }
        if object["resumen"] == nil { object["resumen"] = payload["reason"] }
        return decoded(object)
    }

    private func extensionEvent(_ name: String, _ payload: [String: Any], _ time: Double) -> [AlfalfaMessage] {
        if name == "closing", let run = payload["run_id"] as? String {
            closings[run] = payload                       // sale con el `run_finished` de su ejecución
            return []
        }
        return decoded(PacaiExtension.event(name, payload: payload, time: time))
    }
}

// MARK: - Textos y horas

extension PacaiClient {
    static func text(of blocks: Any?) -> String {
        (blocks as? [[String: Any]] ?? []).compactMap { $0["type"] as? String == "text" ? $0["text"] as? String : nil }
            .joined()
    }

    /// La hora de un sobre (ISO 8601 en UTC, con milisegundos o sin ellos) en segundos desde 1970, como `hora` de v2.
    static func seconds(_ value: Any?) -> Double {
        guard let text = value as? String else { return 0 }
        return (milliseconds.date(from: text) ?? wholeSeconds.date(from: text))?.timeIntervalSince1970 ?? 0
    }

    static func timestamp(_ date: Date) -> String { milliseconds.string(from: date) }

    private static let milliseconds: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return formatter
    }()

    private static let wholeSeconds = ISO8601DateFormatter()
}
