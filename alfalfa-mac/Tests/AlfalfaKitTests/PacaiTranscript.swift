@testable import AlfalfaKit
import Foundation
import XCTest

/// Una transcripción de `scripts/transcripciones_pacai_app.py`: el mismo escenario con el motor de verdad por
/// `--stdio` v2 y por `--pacai`, con las órdenes de la app y lo que el motor contestó en cada lado.
struct PacaiTranscript {

    // MARK: - Propiedades

    let name: String
    let v2: [[String: Any]]
    let pacai: [[String: Any]]

    // MARK: - Inicializadores

    init(_ name: String) throws {
        let url = try XCTUnwrap(Bundle.module.url(forResource: name, withExtension: "json",
                                                  subdirectory: "Fixtures/pacai"))
        let object = try XCTUnwrap(try JSONSerialization.jsonObject(with: Data(contentsOf: url)) as? [String: Any])
        self.name = name
        v2 = try XCTUnwrap((object["stdio"] as? [String: Any])?["pasos"] as? [[String: Any]])
        pacai = try XCTUnwrap((object["pacai"] as? [String: Any])?["pasos"] as? [[String: Any]])
    }

    // MARK: - Métodos

    /// Lo que la app ve con v2: cada línea del motor con el decodificador de siempre. Comprueba que cada orden de la
    /// app sale como la línea que se le mandó al motor.
    func eventsV2(file: StaticString = #filePath, line: UInt = #line) throws -> [AlfalfaMessage] {
        var events: [AlfalfaMessage] = []
        for step in v2 {
            if let received = step["recibido"] {
                if let message = AlfalfaMessage.decode(line: try Self.text(received)) { events.append(message) }
            } else if let spec = step["orden"] as? [String: Any] {
                let command = try Self.command(spec, after: events)
                let sent = try XCTUnwrap(step["enviado"] as? [[String: Any]])
                XCTAssertEqual(try Self.parsed(command.line) as NSDictionary, sent.first as NSDictionary?,
                               "\(name): v2 \(spec)", file: file, line: line)
            }
        }
        return events
    }

    /// Lo que la app ve con PACAI: el cliente de la app recibe cada mensaje del motor y manda cada orden. Comprueba
    /// que manda lo mismo que se le mandó al motor (sin `id`, `seq` ni `time`, que cambian en cada pasada).
    func eventsPacai(file: StaticString = #filePath, line: UInt = #line) throws -> [AlfalfaMessage] {
        var events: [AlfalfaMessage] = []
        var client: PacaiClient?
        for step in pacai {
            var output = PacaiClient.Output()
            if let opening = step["apertura"] as? [String: Any] {
                let hello = try XCTUnwrap((step["enviado"] as? [[String: Any]])?.first)
                let start = PacaiClient.Opening(task: opening["task"] as? String,
                                                attachments: opening["attachments"] as? [String] ?? [],
                                                continueWithoutMessage: opening["continue"] as? Bool ?? false)
                client = PacaiClient(opening: start, session: try XCTUnwrap(hello["session"] as? String))
                output = try XCTUnwrap(client).start()
            } else if let received = step["recibido"] {
                output = try XCTUnwrap(client).receive(try Self.text(received))
            } else if let spec = step["orden"] as? [String: Any] {
                output = try XCTUnwrap(client).send(try Self.command(spec, after: events))
            }
            let sent = (step["enviado"] as? [[String: Any]] ?? []).map(Self.stable)
            let mine = try output.lines.map { try Self.stable(Self.parsed($0)) }
            XCTAssertEqual(mine as NSArray, sent as NSArray, "\(name): PACAI \(step.keys.sorted())", file: file,
                           line: line)
            events += output.events
        }
        return events
    }

    /// Los eventos como texto comparable: sin horas y con cada id (de sesión, turno, permiso, herramienta…)
    /// cambiado por su orden de aparición, porque cada pasada tiene los suyos.
    static func normalized(_ events: [AlfalfaMessage]) -> [String] {
        var names: [String: String] = [:]
        return events.map { event in
            var text = String(describing: event)
            for pattern in [#"\b(time|since|until|date): (Optional\()?[0-9.e+-]+"#] {
                text = text.replacingOccurrences(of: pattern, with: "$1: 0", options: .regularExpression)
            }
            let ids = try? NSRegularExpression(pattern: #"\b(m_[0-9a-f]{16}|[0-9a-f]{32}|[0-9a-f]{10,16})\b"#)
            let range = NSRange(text.startIndex..., in: text)
            for match in (ids?.matches(in: text, range: range) ?? []).reversed() {
                guard let found = Range(match.range, in: text) else { continue }
                let id = String(text[found])
                let name = names[id] ?? "ID\(names.count + 1)"
                names[id] = name
                text.replaceSubrange(found, with: name)
            }
            return text
        }
    }

    // MARK: - Métodos privados

    private static func text(_ value: Any) throws -> String {
        let data = try JSONSerialization.data(withJSONObject: value, options: [.sortedKeys])
        return String(decoding: data, as: UTF8.self)
    }

    private static func parsed(_ line: String) throws -> [String: Any] {
        try XCTUnwrap(try JSONSerialization.jsonObject(with: Data(line.utf8)) as? [String: Any])
    }

    /// Un mensaje PACAI sin lo que cambia en cada pasada.
    private static func stable(_ message: [String: Any]) -> [String: Any] {
        var message = message
        for key in ["seq", "time", "session"] { message.removeValue(forKey: key) }
        if (message["id"] as? String)?.hasPrefix("c_") == true { message.removeValue(forKey: "id") }
        return message
    }

    /// La orden de la app que describe un paso. Un permiso o una respuesta del navegador contestan al último que
    /// vio la app.
    private static func command(_ spec: [String: Any], after events: [AlfalfaMessage]) throws -> AlfalfaCommand {
        switch spec["tipo"] as? String {
        case "message":
            return .message(spec["text"] as? String ?? "", images: spec["images"] as? [String] ?? [],
                            id: spec["id"] as? String)
        case "permission":
            let id = try XCTUnwrap(events.reversed().lazy.compactMap { event -> String? in
                if case let .permission(request) = event { return request.id }
                return nil
            }.first)
            return .permission(id: id, approved: spec["approved"] as? Bool ?? false,
                               session: spec["session"] as? Bool ?? false, site: spec["site"] as? String,
                               always: spec["always"] as? Bool ?? false)
        case "browserReply":
            let id = try XCTUnwrap(events.reversed().lazy.compactMap { event -> String? in
                if case let .browser(request) = event, request.action != "cancelar_relevo" { return request.id }
                return nil
            }.first)
            return .browserReply(reply(id, try XCTUnwrap(spec["body"] as? [String: Any])))
        case "cancel": return .cancel
        case "sendNow": return .sendNow(spec["text"] as? String ?? "", id: spec["id"] as? String)
        case "redirect": return .redirect
        case "unqueue": return .unqueue(try XCTUnwrap(spec["id"] as? String))
        case "compact": return .compact(instructions: spec["instructions"] as? String)
        case "contextMode": return .contextMode(try XCTUnwrap(ContextMode(rawValue: spec["mode"] as? String ?? "")))
        case "settings":
            return .settings(model: spec["model"] as? String, effort: spec["effort"] as? String,
                             mode: spec["mode"] as? String)
        case "budget":
            return .budget(session: (spec["session"] as? NSNumber)?.doubleValue,
                           month: (spec["month"] as? NSNumber)?.doubleValue)
        case "keepAwake": return .keepAwake(spec["on"] as? Bool ?? true)
        case "taskQueue":
            return .taskQueue(try XCTUnwrap(TaskQueueAction(rawValue: spec["action"] as? String ?? "")),
                              tasks: spec["tasks"] as? [String] ?? [], mode: spec["mode"] as? String ?? "serie",
                              parallelism: spec["parallelism"] as? Int ?? 2)
        default: throw XCTSkip("orden desconocida: \(spec)")
        }
    }

    private static func reply(_ id: String, _ body: [String: Any]) -> BrowserReply {
        let result = body["resultado"] as? [String: Any] ?? [:]
        if body["ok"] as? Bool == true { return .success(id, result) }
        if let permission = body["permiso"] as? [String: Any] {
            if permission["tipo"] as? String == "sitio" {
                return .sitePermission(id, url: permission["url"] as? String ?? "",
                                       token: permission["token"] as? String ?? "",
                                       initiated: permission["iniciada"] as? Bool ?? false, result: result)
            }
            return .permission(id, kind: permission["tipo"] as? String ?? "",
                               detail: permission["detalle"] as? String ?? "", result: result)
        }
        return .failure(id, body["error"] as? String ?? "", result: result)
    }
}
