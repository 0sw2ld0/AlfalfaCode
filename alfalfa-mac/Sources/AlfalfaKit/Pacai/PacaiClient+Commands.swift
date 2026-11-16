import Foundation
import UniformTypeIdentifiers

/// Cada orden de la app (`AlfalfaCommand`) como mensaje PACAI: del núcleo (`input`, `approval_result`, `close`,
/// `tool_end`) o de la extensión (`alfalfa.*`, con los campos y los valores en inglés).
extension PacaiClient {

    // MARK: - Métodos públicos

    /// Las líneas de una orden y lo que la app ve ya (el eco de su respuesta a un permiso, como el
    /// `permiso_resuelto` de v2).
    public func send(_ command: AlfalfaCommand) -> Output {
        switch command {
        case let .message(text, images, id): return message(command, text: text, images: images, id: id)
        case let .permission(id, approved, session, site, always):
            return permission(id, approved: approved, session: session, site: site, always: always)
        case let .browserReply(reply):
            // Sólo a una herramienta abierta: una respuesta a algo ya cancelado (o a «cancelar relevo») sobra.
            guard clientTools.remove(reply.id) != nil else { return Output() }
            var body = reply.object
            body.removeValue(forKey: "tipo")
            body.removeValue(forKey: "id")
            var payload: [String: Any] = ["tool_call_id": reply.id, "status": reply.ok ? "success" : "error",
                                          "output": [["type": "structured", "schema": "alfalfa.browser_reply",
                                                      "data": PacaiBrowser.reply(body)]]]
            payload["summary"] = reply.error.map { String($0.prefix(300)) }
            return Output(lines: [envelope("tool_end", payload, replyTo: reply.id)])
        case .cancel:
            return Output(lines: [envelope("close", [:])])
        case let .sendNow(_, id):
            // Uno que aún no salió no tiene turno que cortar: entra en cuanto empiece el que espera.
            guard let id, heldIndex(id) == nil else { return Output() }
            return order("send_now", ["id": id])
        case let .unqueue(id):
            if let index = heldIndex(id) {
                // Aún no salió: se quita aquí, como haría el motor con uno que no ha leído.
                held.remove(at: index)
                return Output(events: [.unqueued(id: id, removed: true, reason: nil)])
            }
            return order("dequeue", ["id": id])
        case .redirect: return order("redirect", [:])
        case let .exportBrowserTest(id, path):
            return work("alfalfa.export_browser_test", ["id": id, "path": path])
        case let .taskQueue(action, tasks, mode, parallelism):
            return taskQueue(action, tasks: tasks, mode: mode, parallelism: parallelism)
        default:
            return setting(command)
        }
    }

    // MARK: - Métodos privados

    /// Los bloques de un mensaje: el texto y un bloque `file` por adjunto (`image/*` para las imágenes, como las
    /// separa `Attachments`, y otro tipo para los demás).
    static func blocks(_ text: String, _ attachments: [String]) -> [[String: Any]] {
        var blocks: [[String: Any]] = text.isEmpty ? [] : [["type": "text", "text": text]]
        for path in attachments {
            let url = URL(fileURLWithPath: path)
            blocks.append(["type": "file", "name": url.lastPathComponent, "media_type": mediaType(url),
                           "url": url.absoluteString])
        }
        return blocks
    }

    static func mediaType(_ url: URL) -> String {
        let ext = url.pathExtension.lowercased()
        let mime = UTType(filenameExtension: ext)?.preferredMIMEType
        if Attachments.isImage(url) { return mime.flatMap { $0.hasPrefix("image/") ? $0 : nil } ?? "image/" + ext }
        return mime.flatMap { $0.hasPrefix("image/") ? nil : $0 } ?? "application/octet-stream"
    }

    /// Lo que se escribió mientras se esperaba el `run_started`: ahora entra en esa ejecución (o abre una).
    func releaseHeld() -> Output {
        var output = Output()
        let pending = held
        held = []
        for command in pending { output.append(send(command)) }
        return output
    }

    private func heldIndex(_ id: String) -> Int? {
        held.firstIndex { if case let .message(_, _, heldID) = $0 { return heldID == id } else { return false } }
    }

    private func message(_ command: AlfalfaCommand, text: String, images: [String], id: String?) -> Output {
        if awaitingRun {
            held.append(command)
            return Output()
        }
        var payload: [String: Any] = ["blocks": Self.blocks(text, images)]
        if let rootRun {
            // Con una ejecución en marcha entra en ella en la siguiente inferencia, sin cortar la respuesta
            // (`mensaje` de v2); «Redirigir» es aparte.
            payload["mode"] = "steer"
            payload["run_id"] = rootRun
            payload["alfalfa"] = ["redirect": false]
        } else {
            awaitingRun = true
        }
        return Output(lines: [envelope("input", payload, id: id)])
    }

    private func permission(_ id: String, approved: Bool, session: Bool, site: String?, always: Bool) -> Output {
        var payload: [String: Any] = ["decision": approved ? "approved" : "denied"]
        if approved {
            payload["scope"] = (site == "sesion" || (site == nil && session && !always)) ? "session" : "once"
            if always { payload["alfalfa"] = ["scope": "always"] }
            if site == "proyecto" { payload["alfalfa"] = ["scope": "project"] }
        }
        let echo = AlfalfaMessage.permissionResolved(id: id, approved: approved, reason: Self.clientAnswer,
                                                     session: approved && session && site == nil && !always,
                                                     always: approved && always && site == nil)
        return Output(lines: [envelope("approval_result", payload, replyTo: id)], events: [echo])
    }

    private func order(_ name: String, _ payload: [String: Any]) -> Output {
        Output(lines: [envelope("alfalfa." + name, payload)])
    }

    /// Trabajo que puede pedir permisos fuera de un turno: un `input` con su orden en un bloque `structured`.
    private func work(_ schema: String, _ data: [String: Any]) -> Output {
        Output(lines: [envelope("input", ["blocks": [["type": "structured", "schema": schema, "data": data]]])])
    }

    private func taskQueue(_ action: TaskQueueAction, tasks: [String], mode: String, parallelism: Int) -> Output {
        let english = ["preparar": "prepare", "iniciar": "start", "pausar": "pause", "detener": "stop",
                       "cancelar": "cancel"][action.rawValue] ?? action.rawValue
        if action == .start { return work("alfalfa.jobs_command", ["action": english]) }
        var payload: [String: Any] = ["action": english]
        if action == .prepare {
            payload["jobs"] = tasks
            payload["mode"] = PacaiNames.values["mode"]?[mode] ?? mode
            payload["parallel"] = parallelism
        }
        return order("jobs_command", payload)
    }

    /// Ajustes y órdenes de la sesión, sin respuesta propia.
    private func setting(_ command: AlfalfaCommand) -> Output {
        switch command {
        case let .compact(instructions):
            var payload: [String: Any] = [:]
            payload["instructions"] = instructions
            return order("compact", payload)
        case let .contextMode(mode):
            return order("set_context_mode", ["mode": PacaiNames.values["context"]?[mode.rawValue] ?? mode.rawValue])
        case .continueInNewSession: return order("continue_in_new_session", [:])
        case let .stopProcess(id): return order("stop_process", ["id": id])
        case let .settings(model, effort, mode):
            var payload: [String: Any] = [:]
            payload["model"] = model
            payload["effort"] = effort
            payload["mode"] = mode.map { PacaiNames.values["mode"]?[$0] ?? $0 }
            return order("set_settings", payload)
        case let .keepAwake(on): return order("set_keep_awake", ["active": on])
        case let .diagnostics(on): return order("set_diagnostics", ["active": on])
        case let .swiftDiagnostics(on): return order("set_swift_diagnostics", ["active": on])
        case let .commitSignature(on): return order("set_signing", ["active": on])
        case let .budget(session, month):
            return order("set_budget", ["session": session.map { $0 as Any } ?? NSNull(),
                                        "month": month.map { $0 as Any } ?? NSNull()])
        case let .learning(id, apply): return order("resolve_learning", ["id": id, "apply": apply])
        case let .reviewCode(turn, model): return order("review_code", ["turn": turn, "model": model])
        case .cancelCodeReview: return order("cancel_code_review", [:])
        default: return Output()
        }
    }
}
