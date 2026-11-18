@testable import AlfalfaKit
import XCTest

/// El cliente PACAI de la app en lo que las transcripciones no recorren: abrir la sesión, lo que se escribe antes
/// de que empiece el turno, las respuestas que ya no hacen falta y cómo sale cada orden.
final class PacaiClientTests: XCTestCase {

    // MARK: - Métodos

    func testTheHelloDeclaresTheBrowserAndTheTaskGoesWithoutEchoAndWithItsAttachments() throws {
        let client = PacaiClient(opening: .init(task: "Mira esto", attachments: ["/p/captura.png", "/p/informe.pdf"]))
        let lines = try client.start().lines.map(parsed)
        XCTAssertEqual(lines.map { $0["type"] as? String }, ["hello", "input"])
        let hello = try XCTUnwrap(lines.first?["payload"] as? [String: Any])
        XCTAssertEqual(hello["versions"] as? [String], ["0.3"])
        XCTAssertEqual(hello["profiles"] as? [String], ["work"])
        XCTAssertEqual(hello["extensions"] as? [String], ["alfalfa"])
        XCTAssertEqual((hello["client_tools"] as? [[String: Any]])?.first?["name"] as? String, "browser")
        let input = try XCTUnwrap(lines.last?["payload"] as? [String: Any])
        XCTAssertEqual(input["alfalfa"] as? [String: Bool], ["echo": false])
        let blocks = try XCTUnwrap(input["blocks"] as? [[String: Any]])
        XCTAssertEqual(blocks.map { $0["type"] as? String }, ["text", "file", "file"])
        XCTAssertEqual(blocks[1]["media_type"] as? String, "image/png")
        XCTAssertEqual(blocks[1]["url"] as? String, "file:///p/captura.png")
        XCTAssertEqual(blocks[2]["media_type"] as? String, "application/pdf")
        XCTAssertEqual(lines.map { $0["seq"] as? Int }, [1, 2])
        XCTAssertEqual(Set(lines.compactMap { $0["session"] as? String }), [client.session])
    }

    func testResumingWithoutAMessageAndOpeningToWaitSendNoText() throws {
        let resume = try PacaiClient(opening: .init(continueWithoutMessage: true)).start().lines.map(parsed)
        XCTAssertEqual((resume.last?["payload"] as? [String: Any])?["alfalfa"] as? [String: Bool], ["continue": true])
        XCTAssertEqual(PacaiClient().start().lines.count, 1)
    }

    func testWhatIsWrittenBeforeTheTurnStartsJoinsIt() throws {
        let client = PacaiClient(opening: .init(task: "Uno"))
        _ = client.start()
        XCTAssertTrue(client.send(.message("dos", id: "cola-dos-0002")).lines.isEmpty)
        XCTAssertTrue(client.send(.message("tres", id: "cola-tres-0003")).lines.isEmpty)
        XCTAssertEqual(client.send(.unqueue("cola-tres-0003")).events,
                       [.unqueued(id: "cola-tres-0003", removed: true, reason: nil)])
        let released = try client.receive(agent("run_started", ["input_id": "c_1"], id: "m_run00000001")).lines
            .map(parsed)
        XCTAssertEqual(released.count, 1)
        XCTAssertEqual(released.first?["id"] as? String, "cola-dos-0002")
        let payload = try XCTUnwrap(released.first?["payload"] as? [String: Any])
        XCTAssertEqual(payload["mode"] as? String, "steer")
        XCTAssertEqual(payload["run_id"] as? String, "m_run00000001")
        XCTAssertEqual(payload["alfalfa"] as? [String: Bool], ["redirect": false])
    }

    func testRemovingTheInputThatWasToOpenTheRunLetsTheHeldMessagesOut() throws {
        // «Sí, súbelos» mientras el motor compacta: sale para abrir la ejecución y se quita antes de que empiece.
        let client = PacaiClient()
        _ = client.start()
        XCTAssertEqual(client.send(.message("Sí, súbelos", id: "cola-uno-0001")).lines.count, 1)
        XCTAssertTrue(client.send(.message("Y la etiqueta", id: "cola-dos-0002")).lines.isEmpty)
        XCTAssertEqual(client.send(.unqueue("cola-uno-0001")).lines.count, 1)        // ya salió: lo quita el motor
        let output = client.receive(agent("alfalfa.dequeued", ["id": "cola-uno-0001", "removed": true]))
        XCTAssertEqual(output.events, [.unqueued(id: "cola-uno-0001", removed: true, reason: nil)])
        // Su ejecución ya no empezará: lo que esperaba sale como mensaje nuevo, que abre la suya.
        let released = try output.lines.map(parsed)
        XCTAssertEqual(released.map { $0["id"] as? String }, ["cola-dos-0002"])
        XCTAssertNil((released.first?["payload"] as? [String: Any])?["mode"])
        XCTAssertTrue(client.send(.message("tres", id: "cola-tres-0003")).lines.isEmpty)   // espera a la nueva
    }

    func testAnInputTheModelAlreadyReadOrAnotherOneKeepsTheWait() throws {
        let client = PacaiClient()
        _ = client.start()
        _ = client.send(.message("uno", id: "cola-uno-0001"))
        _ = client.send(.message("dos", id: "cola-dos-0002"))
        let read = client.receive(agent("alfalfa.dequeued", ["id": "cola-uno-0001", "removed": false,
                                                              "reason": "delivered"]))
        XCTAssertTrue(read.lines.isEmpty)
        XCTAssertTrue(client.receive(agent("alfalfa.dequeued", ["id": "otra-0009", "removed": true])).lines.isEmpty)
        XCTAssertTrue(client.send(.message("tres", id: "cola-tres-0003")).lines.isEmpty)
    }

    func testARejectedInputLetsTheHeldMessagesOut() throws {
        let client = PacaiClient(opening: .init(task: "Uno"))
        _ = client.start()
        _ = client.send(.message("dos", id: "cola-dos-0002"))
        let output = client.receive(agent("error", ["code": "alfalfa.engine_error", "message": "No existe la imagen"]))
        XCTAssertEqual(output.events, [.error("No existe la imagen")])
        XCTAssertEqual(try output.lines.map(parsed).first?["id"] as? String, "cola-dos-0002")
    }

    func testABrowserReplyOnlyGoesToAnOpenClientTool() throws {
        let client = PacaiClient()
        _ = client.start()
        let start = client.receive(agent("tool_start", ["run_id": "m_run00000001", "name": "browser", "side": "client",
                                                       "arguments": ["action": "read", "mode": "text"]],
                                         id: "m_tool0000001"))
        XCTAssertEqual(start.events, [.browser(BrowserRequest(id: "m_tool0000001", action: "leer",
                                                              fields: ["modo": "texto"]))])
        let reply = try client.send(.browserReply(.permission("m_tool0000001", kind: "formulario", detail: "Enviar")))
            .lines.map(parsed)
        let payload = try XCTUnwrap(reply.first?["payload"] as? [String: Any])
        XCTAssertEqual(reply.first?["reply_to"] as? String, "m_tool0000001")
        XCTAssertEqual(payload["status"] as? String, "error")
        let data = (payload["output"] as? [[String: Any]])?.first?["data"] as? [String: Any]
        XCTAssertEqual(data?["permission"] as? [String: String], ["type": "form", "detail": "Enviar"])
        // Ya contestada (o cancelada por el agente): una segunda respuesta, como el «cancelar relevo», sobra.
        XCTAssertTrue(client.send(.browserReply(.success("m_tool0000001", [:]))).lines.isEmpty)
    }

    func testAnswersEchoLikeV2AndCarryTheScopesV2Has() throws {
        let client = PacaiClient()
        let always = client.send(.permission(id: "m_peticion001", approved: true, always: true))
        XCTAssertEqual(always.events, [.permissionResolved(id: "m_peticion001", approved: true,
                                                           reason: "Respuesta del cliente", always: true)])
        let payload = try XCTUnwrap(try parsed(XCTUnwrap(always.lines.first))["payload"] as? [String: Any])
        XCTAssertEqual(payload["scope"] as? String, "once")
        XCTAssertEqual(payload["alfalfa"] as? [String: String], ["scope": "always"])
        let project = try parsed(XCTUnwrap(client.send(.permission(id: "m_peticion002", approved: true,
                                                                   site: "proyecto")).lines.first))
        XCTAssertEqual((project["payload"] as? [String: Any])?["alfalfa"] as? [String: String], ["scope": "project"])
        let denied = try parsed(XCTUnwrap(client.send(.permission(id: "m_peticion003", approved: false)).lines.first))
        XCTAssertEqual(denied["payload"] as? [String: String], ["decision": "denied"])
    }

    func testEveryOtherCommandHasItsMessage() throws {
        let client = PacaiClient()
        let commands: [(AlfalfaCommand, String)] = [
            (.cancel, "close"), (.redirect, "alfalfa.redirect"),
            (.continueInNewSession, "alfalfa.continue_in_new_session"), (.stopProcess("p1"), "alfalfa.stop_process"),
            (.diagnostics(false), "alfalfa.set_diagnostics"),
            (.swiftDiagnostics(true), "alfalfa.set_swift_diagnostics"),
            (.commitSignature(false), "alfalfa.set_signing"),
            (.learning(id: "ap1", apply: true), "alfalfa.resolve_learning"),
            (.reviewCode(turn: "t1", model: "m"), "alfalfa.review_code"),
            (.cancelCodeReview, "alfalfa.cancel_code_review"),
            (.taskQueue(.pause), "alfalfa.jobs_command"), (.taskQueue(.start), "input"),
            (.exportBrowserTest(id: String(repeating: "f", count: 32), path: "login.spec.ts"), "input")
        ]
        for (command, type) in commands {
            XCTAssertEqual(try parsed(XCTUnwrap(client.send(command).lines.first))["type"] as? String, type,
                           "\(command)")
        }
        let export = try parsed(XCTUnwrap(client.send(.exportBrowserTest(id: "f", path: "x")).lines.first))
        let block = ((export["payload"] as? [String: Any])?["blocks"] as? [[String: Any]])?.first
        XCTAssertEqual(block?["schema"] as? String, "alfalfa.export_browser_test")
        XCTAssertEqual(block?["data"] as? [String: String], ["id": "f", "path": "x"])
    }

    func testTheEscapeHatchIsAUserDefault() throws {
        let defaults = try XCTUnwrap(UserDefaults(suiteName: "pacai-\(UUID().uuidString)"))
        XCTAssertEqual(EngineProtocol.current(defaults), .pacai)
        defaults.set("stdio", forKey: EngineProtocol.defaultsKey)
        XCTAssertEqual(EngineProtocol.current(defaults), .stdio)
        defaults.set("otra cosa", forKey: EngineProtocol.defaultsKey)
        XCTAssertEqual(EngineProtocol.current(defaults), .pacai)
    }

    func testPacaiArgumentsKeepTheSettingsAndLeaveTheTaskForTheInput() {
        let arguments = Launcher.pacaiArguments(resume: "abc", model: "m", effort: "high", readOnly: true,
                                                mode: "manual", keepAwake: false, browser: true, queue: true)
        XCTAssertEqual(arguments.first, "--pacai")
        XCTAssertFalse(arguments.contains("--stdio") || arguments.contains("--esperar") || arguments.contains("--"))
        for flag in ["--reanudar", "--modelo", "--esfuerzo", "--solo-lectura", "--modo", "--sin-mantener-despierto",
                     "--cola-encargos", "--navegador"] {
            XCTAssertTrue(arguments.contains(flag), flag)
        }
    }

    // MARK: - Métodos privados

    private func parsed(_ line: String) throws -> [String: Any] {
        try XCTUnwrap(try JSONSerialization.jsonObject(with: Data(line.utf8)) as? [String: Any])
    }

    private func agent(_ type: String, _ payload: [String: Any], id: String = "m_0000000000000001") -> String {
        let message: [String: Any] = ["pacai": "0.3", "id": id, "type": type, "sender": "agent", "seq": 1,
                                      "session": "s_" + String(repeating: "0", count: 32),
                                      "time": "2026-11-16T10:00:00.000Z", "payload": payload]
        return String(decoding: (try? JSONSerialization.data(withJSONObject: message)) ?? Data(), as: UTF8.self)
    }
}
