@testable import AlfalfaKit
import XCTest

/// El texto mientras se escribe: cada `message_delta` de la respuesta de la ejecución raíz es un borrador que crece;
/// el `message_end` con `blocks` trae el contenido final (§4.3) y lo sustituye; vacío, la respuesta se retiró.
final class PacaiDraftTests: XCTestCase {

    private func line(_ type: String, _ payload: [String: Any], id: String) -> String {
        let object: [String: Any] = ["pacai": "0.3", "id": id, "type": type, "session": "s_0123456789abcdef0123",
                                     "sender": "agent", "seq": 1, "time": "2026-11-17T10:00:00.000Z",
                                     "payload": payload]
        return String(decoding: try! JSONSerialization.data(withJSONObject: object), as: UTF8.self)
    }

    private func delta(_ text: String, id: String) -> String {
        line("message_delta", ["message_id": "m_msg", "blocks": [["type": "text", "text": text]]], id: id)
    }

    private func started() -> PacaiClient {
        let client = PacaiClient(opening: .init(task: "Saluda"))
        _ = client.start()
        _ = client.receive(line("run_started", ["input_id": "c_1"], id: "m_run"))
        _ = client.receive(line("message_start", ["run_id": "m_run", "role": "assistant",
                                                  "alfalfa": ["turn": "t1", "live": true]], id: "m_msg"))
        return client
    }

    func testEachDeltaGrowsTheDraftAndTheFinalTextReplacesIt() {
        let client = started()
        XCTAssertEqual(client.receive(delta("Ho", id: "m_d1")).events, [.draft("Ho")])
        XCTAssertEqual(client.receive(delta("la", id: "m_d2")).events, [.draft("Hola")])
        let end = line("message_end", ["message_id": "m_msg", "blocks": [["type": "text", "text": "Hola, mundo."]]],
                       id: "m_e")
        guard case let .event(event)? = client.receive(end).events.first else { return XCTFail("sin respuesta") }
        XCTAssertEqual(event.text, "Hola, mundo.")          // el contenido final, no «Hola» + «Hola, mundo.»
        XCTAssertEqual(event.role, "agent")
        XCTAssertEqual(event.turn, "t1")
    }

    func testAnEmptyFinalWithdrawsTheDraft() {
        let client = started()
        _ = client.receive(delta("No vale", id: "m_d1"))
        XCTAssertEqual(client.receive(line("message_end", ["message_id": "m_msg", "blocks": [Any]()], id: "m_e")).events,
                       [.draft(nil)])
    }

    func testAnEndWithoutBlocksKeepsWhatArrived() {
        let client = started()
        _ = client.receive(delta("Entero", id: "m_d1"))
        guard case let .event(event)? = client.receive(line("message_end", ["message_id": "m_msg"], id: "m_e"))
            .events.first else { return XCTFail("sin respuesta") }
        XCTAssertEqual(event.text, "Entero")
    }

    func testAMessageThatArrivesWholeIsNoDraft() {
        let client = PacaiClient(opening: .init(task: "Saluda"))
        _ = client.start()
        _ = client.receive(line("run_started", ["input_id": "c_1"], id: "m_run"))
        _ = client.receive(line("message_start", ["run_id": "m_run", "role": "assistant"], id: "m_msg"))
        XCTAssertEqual(client.receive(delta("Entero", id: "m_d1")).events, [])
    }

    func testASubagentDoesNotWriteInTheMainConversation() {
        let client = started()
        _ = client.receive(line("run_started", ["parent_run_id": "m_run", "alfalfa": ["agent": "a1"]], id: "m_sub"))
        _ = client.receive(line("message_start", ["run_id": "m_sub", "role": "assistant"], id: "m_sm"))
        let sub = line("message_delta", ["message_id": "m_sm", "blocks": [["type": "text", "text": "Reviso"]]],
                       id: "m_sd")
        XCTAssertEqual(client.receive(sub).events, [])
    }

    func testTheTimelineShowsTheDraftUntilTheAnswerArrives() {
        var timeline = Timeline(history: [], wrapUp: nil)
        timeline.apply(.draft("Escribo"))
        XCTAssertEqual(timeline.draft, "Escribo")
        XCTAssertEqual(timeline.status, .working)
        timeline.apply(.event(AgentEvent(role: "tool", text: "Inicio read_file", time: 1)))
        XCTAssertEqual(timeline.draft, "Escribo")         // un paso no lo retira
        timeline.apply(.event(AgentEvent(role: "agent", text: "Escribo bien.", time: 2)))
        XCTAssertNil(timeline.draft)
        timeline.apply(.draft("Otra"))
        timeline.apply(.draft(nil))
        XCTAssertNil(timeline.draft)
        timeline.apply(.draft("Cortada"))
        timeline.processExited(code: 1)
        XCTAssertNil(timeline.draft)
    }

    func testWhoWatchesFromHalfwaySeesWhatWasWritten() {
        let observation = PacaiObservation(ids: { "o_00000001" })
        _ = observation.start()
        func envelope(_ type: String, _ payload: [String: Any], _ id: String) -> [String: Any] {
            ["pacai": "0.3", "id": id, "type": type, "session": "s_x0123456789abcdef", "sender": "agent", "seq": 1,
             "time": "2026-11-17T10:00:00.000Z", "payload": payload]
        }
        let snapshot = envelope("snapshot", [
            "runs": [["run_id": "m_run", "status": "active"]], "pending_approvals": [Any](),
            "last_messages": [envelope("run_started", ["input_id": "c_1"], "m_run"),
                              envelope("message_start", ["run_id": "m_run", "role": "assistant",
                                                         "alfalfa": ["live": true]], "m_msg"),
                              envelope("message_delta", ["message_id": "m_msg",
                                                         "blocks": [["type": "text", "text": "Va por aquí"]]], "m_d")]
        ], "m_foto")
        var object = snapshot
        object["reply_to"] = "o_00000001"
        let text = String(decoding: try! JSONSerialization.data(withJSONObject: object), as: UTF8.self)
        guard case let .ready(_, _, state, _)? = observation.receive(text) else { return XCTFail("sin foto") }
        XCTAssertEqual(state, [.draft("Va por aquí")])
    }
}
