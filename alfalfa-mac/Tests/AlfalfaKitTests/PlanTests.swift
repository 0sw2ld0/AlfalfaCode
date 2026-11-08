@testable import AlfalfaKit
import XCTest

/// El plan del agente, como la lista de tareas de Claude Code y Codex.
final class PlanTests: XCTestCase {
    func testDecodesThePlan() throws {
        let line = #"{"version":2,"tipo":"plan","pasos":[{"paso":"Leer","estado":"completed"},{"paso":"Arreglar","estado":"in_progress"},{"paso":"Probar","estado":"pending"},{"paso":"","estado":"pending"},{"paso":"Raro","estado":"otro"}],"explicacion":"Empiezo"}"#
        guard case let .plan(plan)? = AlfalfaMessage.decode(line: line) else { return XCTFail("plan") }
        XCTAssertEqual(plan, Plan(steps: [PlanStep("Leer", .completed), PlanStep("Arreglar", .inProgress),
                                          PlanStep("Probar", .pending), PlanStep("Raro", .pending)],
                                  explanation: "Empiezo"))
    }

    func testAMessageAfterThePlanMarksItAsFromBefore() {
        var timeline = Timeline()
        timeline.apply(.event(AgentEvent(role: "user", text: "Arregla la suma", time: 1)))
        let plan = Plan(steps: [PlanStep("Leer", .completed), PlanStep("Arreglar", .inProgress)])
        timeline.apply(.plan(plan))
        XCTAssertEqual(timeline.visiblePlan, plan)
        XCTAssertFalse(timeline.planIsFromBeforeLastMessage)
        // Escribes mientras trabaja: el plan sigue, pero ya es el de antes de tu mensaje.
        timeline.addUser("Y también la resta", queued: true)
        XCTAssertTrue(timeline.planIsFromBeforeLastMessage)
        // Hasta que el agente manda otro.
        timeline.apply(.plan(Plan(steps: [PlanStep("Arreglar la resta", .inProgress)])))
        XCTAssertFalse(timeline.planIsFromBeforeLastMessage)
    }

    func testAFinishedPlanGoesAwayWhenTheTurnEnds() {
        var timeline = Timeline()
        timeline.apply(.event(AgentEvent(role: "user", text: "Hazlo", time: 1)))
        timeline.apply(.plan(Plan(steps: [PlanStep("Hacerlo", .completed)])))
        XCTAssertNotNil(timeline.visiblePlan, "mientras trabaja se ve aunque esté todo hecho")
        timeline.apply(.turnFinished(Outcome(state: "completed", summary: "Hecho.", exitCode: 0, tokens: 0)))
        XCTAssertNil(timeline.visiblePlan)
        // Con pasos pendientes se queda a la vista.
        timeline.apply(.plan(Plan(steps: [PlanStep("Hacerlo", .completed), PlanStep("Probarlo", .pending)])))
        timeline.apply(.turnFinished(Outcome(state: "completed", summary: "Hecho.", exitCode: 0, tokens: 0)))
        XCTAssertNotNil(timeline.visiblePlan)
    }
}
