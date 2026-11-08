@testable import AlfalfaKit
import XCTest

/// Subagentes como los de Codex y Claude Code: su lista y sus pasos, aparte de la conversación.
final class SubagentTests: XCTestCase {
    func testSubagentsAndTheirStepsStayOutOfTheConversation() throws {
        let list = #"{"version":2,"tipo":"subagentes","subagentes":[{"id":"a1","nombre":"Buscar usos","tipo":"explorador","estado":"trabajando","modelo":"glm:cloud","desde":10,"hasta":null,"actividad":"tool · Inicio search","pasos":2,"informe":"","error":""},{"id":"a2","nombre":"Revisar","tipo":"general","estado":"terminado","desde":5,"hasta":9,"informe":"Sin errores."},{"nombre":"sin id"}]}"#
        guard case let .subagents(rows)? = AlfalfaMessage.decode(line: list) else { return XCTFail("subagentes") }
        XCTAssertEqual(rows, [
            Subagent(id: "a1", name: "Buscar usos", type: "explorador", state: "trabajando", model: "glm:cloud",
                     since: 10, activity: "tool · Inicio search", steps: 2),
            Subagent(id: "a2", name: "Revisar", type: "general", state: "terminado", since: 5, until: 9, report: "Sin errores."),
        ])
        let step = #"{"version":2,"tipo":"subagente_evento","agente":"a1","rol":"tool","texto":"Inicio search {}","hora":11}"#
        guard let event = AlfalfaMessage.decode(line: step) else { return XCTFail("subagente_evento") }

        var timeline = Timeline()
        timeline.apply(.event(AgentEvent(role: "user", text: "usa subagentes", time: 1)))
        let before = timeline.items
        timeline.apply(.subagents(rows))
        timeline.apply(event)
        XCTAssertEqual(timeline.workingSubagents.map(\.id), ["a1"])
        XCTAssertEqual(timeline.subagentSteps["a1"]?.map(\.text), ["Inicio search {}"])
        XCTAssertEqual(timeline.items, before, "los pasos de un subagente no entran en la conversación")
        for index in 0..<(Timeline.subagentStepLimit + 5) {
            timeline.apply(.subagentEvent(agent: "a2", AgentEvent(role: "tool", text: "\(index)", time: 0)))
        }
        XCTAssertEqual(timeline.subagentSteps["a2"]?.count, Timeline.subagentStepLimit)
        XCTAssertEqual(timeline.subagentSteps["a2"]?.last?.text, "\(Timeline.subagentStepLimit + 4)")
    }
}
