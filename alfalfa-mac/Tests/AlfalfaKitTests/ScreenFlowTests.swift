@testable import AlfalfaKit
import XCTest

/// Las pantallas del perfil `ui` de punta a punta en la app: lo que pide el `hello`, cómo llegan con su mensaje,
/// dónde se ven en la conversación y cómo sale lo que pulsa el usuario.
final class ScreenFlowTests: XCTestCase {

    // MARK: - Cliente PACAI

    func testTheHelloAsksForTheUIProfileWithTheBaseCatalog() throws {
        let hello = try parsed(XCTUnwrap(PacaiClient().start().lines.first))
        let payload = try XCTUnwrap(hello["payload"] as? [String: Any])
        XCTAssertEqual(payload["profiles"] as? [String], ["work", "ui"])
        let catalog = try XCTUnwrap((payload["render"] as? [String: Any])?["ui"] as? [String: Any])
        XCTAssertEqual(catalog["version"] as? String, "1")
        XCTAssertEqual(catalog["catalog"] as? String, "base")
    }

    func testTheScreensOfAMessageTravelWithItsText() throws {
        let client = PacaiClient()
        _ = client.start()
        _ = client.receive(agent("run_started", ["input_id": "c_1"], id: "m_run00000001"))
        _ = client.receive(agent("message_start", ["run_id": "m_run00000001", "role": "assistant"],
                                 id: "m_msg00000001"))
        _ = client.receive(agent("message_delta", ["message_id": "m_msg00000001",
                                                   "blocks": [["type": "text", "text": "Tus tickets:"]]]))
        _ = client.receive(agent("message_delta", ["message_id": "m_msg00000001", "blocks": [Self.block(rev: 1)]]))
        let end = client.receive(agent("message_end", ["message_id": "m_msg00000001",
                                                       "blocks": [Self.block(rev: 2, replace: true)]]))
        guard case let .event(event)? = end.events.first else { return XCTFail("sin evento: \(end.events)") }
        XCTAssertEqual(event.text, "Tus tickets:")
        XCTAssertEqual(event.screens.map(\.rev), [1, 2])
        XCTAssertEqual(event.screens.map(\.replace), [false, true])
    }

    func testAnActionGoesAsAnInputWithItsTextAndItsBlockIntoTheRunningTurn() throws {
        let client = PacaiClient()
        _ = client.start()
        _ = client.receive(agent("run_started", ["input_id": "c_1"], id: "m_run00000001"))
        let request = ScreenActionRequest(view: "tickets", rev: 2, node: "crear", event: "press", name: "crear",
                                          values: ["title": .string("Uno")], item: nil, label: "Crear")
        let line = try parsed(XCTUnwrap(client.send(.screenAction(request, id: "cola-0001")).lines.first))
        XCTAssertEqual(line["type"] as? String, "input")
        XCTAssertEqual(line["id"] as? String, "cola-0001")
        let payload = try XCTUnwrap(line["payload"] as? [String: Any])
        XCTAssertEqual(payload["mode"] as? String, "steer")
        let blocks = try XCTUnwrap(payload["blocks"] as? [[String: Any]])
        XCTAssertEqual(blocks.first?["text"] as? String, "Pulsé «Crear»")
        XCTAssertEqual(blocks.last?["schema"] as? String, "pacai.ui.action/1")
        XCTAssertEqual((blocks.last?["data"] as? [String: Any])?["values"] as? [String: String], ["title": "Uno"])
        // Con `--stdio` v2 sale como un mensaje con su texto.
        XCTAssertTrue(AlfalfaCommand.screenAction(request).line.contains("\"texto\":\"Pulsé «Crear»\""))
    }

    // MARK: - Conversación

    func testAScreenAppearsUnderItsMessageAndUpdatesInPlace() throws {
        var timeline = Timeline()
        timeline.apply(.event(Self.agentEvent("Mira:", updates: [Self.update(rev: 1), Self.update(rev: 2)])))
        timeline.apply(.event(Self.agentEvent("Cambié el título.", updates: [Self.update(rev: 3)])))
        let screens = timeline.items.compactMap { item -> (Screen, Bool)? in
            if case let .screen(_, screen, live) = item { return (screen, live) } else { return nil }
        }
        XCTAssertEqual(screens.count, 1, "sin placement latest se actualiza donde está")
        XCTAssertEqual(screens.first?.0.rev, 3)
        XCTAssertEqual(screens.first?.1, true)
        XCTAssertTrue(timeline.markdown(title: "t").contains("> *Pantalla:* "))
    }

    func testWithPlacementLatestItMovesDownAndLeavesWhatItWas() throws {
        var timeline = Timeline()
        timeline.apply(.event(Self.agentEvent("Mira:", updates: [Self.update(rev: 1)])))
        timeline.apply(.event(Self.agentEvent("", updates: [Self.update(rev: 2, placement: .latest),
                                                            Self.update(rev: 3)])))
        let kinds = timeline.items.map { item -> String in
            switch item {
            case .agent: return "agent"
            case let .screen(_, screen, live): return "screen\(screen.rev)" + (live ? "" : "-old")
            default: return "other"
            }
        }
        // El mensaje sin texto no deja una respuesta vacía; la foto y el patch del mismo mensaje van juntos.
        XCTAssertEqual(kinds, ["agent", "screen1-old", "screen3"])
    }

    // MARK: - Datos

    private static func block(rev: Int, replace: Bool = false) -> [String: Any] {
        ["type": "structured", "schema": "pacai.ui/1",
         "data": ["view": "tickets", "op": replace ? "replace" : "patch", "rev": rev, "alt": "Tickets",
                  "nodes": [["id": "root", "component": "Text", "props": ["text": "Hola"]]]]]
    }

    private static func update(rev: Int, placement: ScreenPlacement? = nil) -> ScreenUpdate {
        ScreenUpdate(view: "tickets", rev: rev, nodes: [ScreenNode(id: "root", component: "Text",
                                                                   props: ["text": .string("v\(rev)")])],
                     alt: "Tickets", placement: placement)
    }

    private static func agentEvent(_ text: String, updates: [ScreenUpdate]) -> AgentEvent {
        AgentEvent(role: "agent", text: text, time: 1, screens: updates)
    }

    private func parsed(_ line: String) throws -> [String: Any] {
        try XCTUnwrap(try JSONSerialization.jsonObject(with: Data(line.utf8)) as? [String: Any])
    }

    private func agent(_ type: String, _ payload: [String: Any], id: String = "m_0000000000000001") -> String {
        let message: [String: Any] = ["pacai": "0.3", "id": id, "type": type, "sender": "agent", "seq": 1,
                                      "session": "s_" + String(repeating: "0", count: 32),
                                      "time": "2026-11-24T10:00:00.000Z", "payload": payload]
        return String(decoding: (try? JSONSerialization.data(withJSONObject: message)) ?? Data(), as: UTF8.self)
    }
}
