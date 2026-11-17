@testable import AlfalfaKit
import Darwin
import XCTest

/// Mirar en vivo una sesión de otro proceso: el `hello` de quien sólo mira, la foto (`snapshot`) sin repetir lo que ya
/// trae el historial, la copia en vivo, la decisión del dueño reenviada y la conexión con el socket de la sesión.
final class PacaiObservationTests: XCTestCase {

    // MARK: - Ayudantes

    static let agentSession = "s_observador_0123456789abcdef"

    static func envelope(_ type: String, _ payload: [String: Any], id: String, seq: Int, replyTo: String? = nil,
                         session: String = agentSession) -> [String: Any] {
        var object: [String: Any] = ["pacai": "0.3", "id": id, "type": type, "session": session, "sender": "agent",
                                     "seq": seq, "time": "2026-11-17T10:00:00.000Z", "payload": payload]
        if let replyTo { object["reply_to"] = replyTo }
        return object
    }

    static func line(_ object: [String: Any]) -> String {
        String(decoding: try! JSONSerialization.data(withJSONObject: object), as: UTF8.self)
    }

    /// Lo que manda el agente al conectarse: su `hello`, `session_started` y la foto de una sesión con una ejecución,
    /// una orden abierta que espera permiso, el uso de la sesión y su contexto.
    static func opening(hello: String) -> [String] {
        let run = envelope("run_started", ["input_id": "c_1"], id: "m_run", seq: 7, session: "s_dueno")
        let tool = envelope("tool_start", ["run_id": "m_run", "name": "run_command", "side": "agent",
                                           "arguments": ["argv": ["touch", "x"], "purpose": "p"],
                                           "alfalfa": ["text": "Inicio run_command · touch x", "turn": "t1",
                                                       "tool": ["nombre": "run_command", "fase": "inicio"]]],
                            id: "m_tool", seq: 9)
        let usage = envelope("usage", ["cost": ["amount": 0.25, "currency": "USD"],
                                       "alfalfa": ["total_tokens": 1500]], id: "m_uso", seq: 8)
        let context = envelope("alfalfa.context", ["tokens": 1000, "limit": 200_000], id: "m_ctx", seq: 10)
        let approval = envelope("approval_request", [
            "run_id": "m_run", "tool_call_id": "m_tool", "name": "run_command",
            "arguments": ["argv": ["touch", "x"], "purpose": "p"], "digest": "sha256:00", "reason": "dangerous",
            "scopes": ["once"], "alfalfa": ["argv": ["touch", "x"], "purpose": "p"]], id: "m_pide", seq: 11)
        let snapshot = envelope("snapshot", [
            "runs": [["run_id": "m_run", "status": "active"]], "pending_approvals": [approval],
            "last_messages": [run, usage, tool, context],
            "alfalfa": ["session_id": "0123456789abcdef", "history_until": 1_763_400_000.5]
        ], id: "m_foto", seq: 3, replyTo: hello)
        return [
            line(envelope("hello", ["versions": ["0.3"], "profiles": ["work"], "extensions": ["alfalfa"]],
                          id: "m_hola", seq: 1)),
            line(envelope("session_started", ["version": "0.3", "profiles": ["work"], "extensions": ["alfalfa"],
                                              "observe": true], id: "m_empieza", seq: 2, replyTo: hello)),
            line(snapshot)
        ]
    }

    // MARK: - Lógica

    func testTheHelloOnlyWatches() throws {
        let observation = PacaiObservation(ids: { "o_00000001" })
        let hello = try XCTUnwrap(PacaiObservation.object(observation.start()))
        XCTAssertEqual(hello["type"] as? String, "hello")
        let payload = try XCTUnwrap(hello["payload"] as? [String: Any])
        XCTAssertEqual(payload["observe"] as? Bool, true)
        XCTAssertNil(payload["client_tools"])
        XCTAssertEqual(payload["extensions"] as? [String], ["alfalfa"])
        XCTAssertEqual(hello["session"] as? String, observation.session)
    }

    func testTheSnapshotGivesTheStateAndTheHistoryCutWithoutRepeatingSteps() throws {
        let observation = PacaiObservation(ids: { "o_00000001" })
        _ = observation.start()
        let lines = Self.opening(hello: "o_00000001")
        XCTAssertNil(observation.receive(lines[0]))
        XCTAssertNil(observation.receive(lines[1]))
        guard case let .ready(sessionID, until, state, running)? = observation.receive(lines[2]) else {
            return XCTFail("sin foto")
        }
        XCTAssertTrue(observation.isReady)
        XCTAssertTrue(running)
        XCTAssertEqual(sessionID, "0123456789abcdef")
        XCTAssertEqual(until, 1_763_400_000.5)
        // La orden abierta ya está en el historial: no se repite. Sí el uso, el contexto y el permiso pendiente.
        XCTAssertFalse(state.contains { if case .event = $0 { return true } else { return false } }, "\(state)")
        XCTAssertTrue(state.contains(.sessionUsage(tokens: 1500, cost: 0.25)), "\(state)")
        XCTAssertTrue(state.contains { if case .context(1000, 200_000, _, _, _) = $0 { return true } else { return false } })
        guard case let .permission(request)? = state.last else { return XCTFail("sin permiso: \(state)") }
        XCTAssertEqual(request.id, "m_pide")

        // Lo que llega después: el dueño aprueba (reenviado), la orden termina y el agente escribe.
        let relayed = Self.envelope("approval_result", ["decision": "approved", "scope": "once", "relayed": true],
                                    id: "m_r", seq: 4, replyTo: "m_pide")
        XCTAssertEqual(observation.receive(Self.line(relayed)),
                       .messages([.permissionResolved(id: "m_pide", approved: true,
                                                      reason: PacaiClient.relayedAnswer)]))
        let end = Self.envelope("tool_end", ["tool_call_id": "m_tool", "status": "success",
                                             "output": [["type": "text", "text": "ok"]],
                                             "alfalfa": ["turn": "t1", "tool": ["nombre": "run_command",
                                                                                "fase": "fin"]]],
                                id: "m_fin", seq: 5)
        guard case let .messages(ended)? = observation.receive(Self.line(end)),
              case let .event(event)? = ended.first else { return XCTFail("la orden abierta no se cerró") }
        XCTAssertEqual(event.role, "tool")
        XCTAssertEqual(event.tool?.phase, .end)
        let start = Self.envelope("message_start", ["run_id": "m_run", "role": "assistant"], id: "m_msg", seq: 6)
        let delta = Self.envelope("message_delta", ["message_id": "m_msg", "blocks": [["type": "text",
                                                                                       "text": "Listo"]]],
                                  id: "m_d", seq: 7)
        let close = Self.envelope("message_end", ["message_id": "m_msg"], id: "m_e", seq: 8)
        XCTAssertNil(observation.receive(Self.line(start)))
        XCTAssertNil(observation.receive(Self.line(delta)))
        guard case let .messages(said)? = observation.receive(Self.line(close)),
              case let .event(text)? = said.first else { return XCTFail("sin texto") }
        XCTAssertEqual(text.text, "Listo")
    }

    func testADeniedRelayedAnswerKeepsItsReason() {
        let request = "m_pide"
        let denied = PacaiClient.relayed(request, ["decision": "denied", "reason": "No toques eso", "relayed": true])
        XCTAssertEqual(denied, .permissionResolved(id: request, approved: false, reason: "No toques eso"))
        let session = PacaiClient.relayed(request, ["decision": "approved", "scope": "session", "relayed": true])
        XCTAssertEqual(session, .permissionResolved(id: request, approved: true, reason: PacaiClient.relayedAnswer,
                                                    session: true))
    }

    func testWhatAnswersTheOwnerIsNotForTheObserver() {
        XCTAssertEqual(PacaiObservation.watchable([.unqueued(id: "q", removed: true, reason: nil), .error("x")]),
                       [.error("x")])
    }

    func testAnErrorBeforeTheSnapshotIsARejection() {
        let observation = PacaiObservation(ids: { "o_00000001" })
        _ = observation.start()
        let error = Self.envelope("error", ["code": "alfalfa.too_many_observers", "message": "Ya hay 8 observadores."],
                                  id: "m_err", seq: 1)
        XCTAssertEqual(observation.receive(Self.line(error)), .rejected("Ya hay 8 observadores."))
        XCTAssertFalse(observation.isReady)
    }

    // MARK: - El socket

    /// Un agente de mentira en un socket Unix: acepta una conexión, lee el `hello` y contesta con `answer`.
    final class FakeAgent: @unchecked Sendable {
        let url: URL
        private let listener: Int32
        private(set) var hello: [String: Any]?
        private var client: Int32 = -1

        init(answer: @escaping (String) -> [String], thenClose: Bool) throws {
            let folder = URL(fileURLWithPath: "/tmp").appendingPathComponent("alfobs-" + UUID().uuidString.prefix(8))
            try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
            url = folder.appendingPathComponent("vivo.sock")
            listener = Darwin.socket(AF_UNIX, SOCK_STREAM, 0)
            var address = sockaddr_un()
            address.sun_family = sa_family_t(AF_UNIX)
            let path = Array(url.path.utf8CString)
            withUnsafeMutableBytes(of: &address.sun_path) { raw in path.withUnsafeBytes { raw.copyMemory(from: $0) } }
            let bound = withUnsafePointer(to: &address) {
                $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                    Darwin.bind(listener, $0, socklen_t(MemoryLayout<sockaddr_un>.size))
                }
            }
            guard bound == 0, listen(listener, 1) == 0 else { throw PacaiObserver.Failure.unreachable(errno) }
            Thread.detachNewThread { [self] in
                client = accept(listener, nil, nil)
                var data = Data()
                var byte: UInt8 = 0
                while read(client, &byte, 1) == 1, byte != 0x0A { data.append(byte) }
                let line = String(decoding: data, as: UTF8.self)
                hello = PacaiObservation.object(line)
                for reply in answer((hello?["id"] as? String) ?? "") {
                    let bytes = Array((reply + "\n").utf8)
                    _ = bytes.withUnsafeBytes { write(client, $0.baseAddress, $0.count) }
                }
                if thenClose { Darwin.close(client) }
            }
        }

        func stop() {
            Darwin.close(listener)
            try? FileManager.default.removeItem(at: url.deletingLastPathComponent())
        }
    }

    func testConnectWaitsForTheSnapshotAndHoldsWhatComesNextUntilAttached() async throws {
        let message = Self.envelope("message_start", ["run_id": "m_run", "role": "assistant"], id: "m_msg", seq: 4)
        let delta = Self.envelope("message_delta", ["message_id": "m_msg", "blocks": [["type": "text",
                                                                                       "text": "En vivo"]]],
                                  id: "m_d", seq: 5)
        let end = Self.envelope("message_end", ["message_id": "m_msg"], id: "m_e", seq: 6)
        let agent = try FakeAgent(answer: { hello in
            Self.opening(hello: hello) + [Self.line(message), Self.line(delta), Self.line(end)]
        }, thenClose: true)
        defer { agent.stop() }
        let result = await PacaiObserver.connect(to: agent.url)
        guard case let .success(ready) = result else { return XCTFail("\(result)") }
        XCTAssertEqual(ready.historyUntil, 1_763_400_000.5)
        XCTAssertEqual(ready.sessionID, "0123456789abcdef")
        XCTAssertTrue(ready.running)
        XCTAssertEqual((agent.hello?["payload"] as? [String: Any])?["observe"] as? Bool, true)
        let received = expectation(description: "lo que llegó tras la foto")
        let closed = expectation(description: "el agente cerró")
        await MainActor.run {
            ready.observer.attach({ messages in
                if messages.contains(where: { if case let .event(e) = $0 { return e.text == "En vivo" }
                                              return false }) { received.fulfill() }
            }, onClose: { closed.fulfill() })
        }
        await fulfillment(of: [received, closed], timeout: 5)
    }

    func testWithoutSocketOrWithARejectionThereIsNoObservation() async throws {
        let missing = URL(fileURLWithPath: "/tmp/alf-no-existe-\(UUID().uuidString.prefix(8))/vivo.sock")
        guard case .failure(.unreachable) = await PacaiObserver.connect(to: missing) else {
            return XCTFail("sin socket no hay observación")
        }
        let long = URL(fileURLWithPath: "/tmp/" + String(repeating: "x", count: 120) + "/vivo.sock")
        guard case .failure(.pathTooLong) = await PacaiObserver.connect(to: long) else {
            return XCTFail("una ruta larga no cabe en sockaddr_un")
        }
        let agent = try FakeAgent(answer: { _ in
            [Self.line(Self.envelope("error", ["code": "invalid_message", "message": "No."], id: "m_e", seq: 1))]
        }, thenClose: false)
        defer { agent.stop() }
        guard case .failure(.rejected("No.")) = await PacaiObserver.connect(to: agent.url) else {
            return XCTFail("el rechazo del agente se cuenta")
        }
        let silent = try FakeAgent(answer: { _ in [] }, thenClose: false)
        defer { silent.stop() }
        guard case .failure(.timedOut) = await PacaiObserver.connect(to: silent.url, timeout: 0.5) else {
            return XCTFail("sin foto a tiempo no hay observación")
        }
    }

    func testAWatchedSessionWithARunningTurnIsWorking() {
        var timeline = Timeline(history: [], wrapUp: nil, running: true)
        XCTAssertEqual(timeline.status, .idle)
        timeline.watchingRun()
        XCTAssertEqual(timeline.status, .working)
    }

    func testTheSocketLivesInTheSessionFolder() {
        let root = URL(fileURLWithPath: "/Users/x/.alfalfa/sesiones")
        XCTAssertEqual(PacaiObserver.socketURL(sessionID: "abc", root: root).path,
                       "/Users/x/.alfalfa/sesiones/abc/vivo.sock")
    }
}
