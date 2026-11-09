@testable import AlfalfaKit
import XCTest

/// Mensajes en cola: el eco del motor los entrega, y «Enviar ahora» o «Redirigir» los adelantan.
final class QueueTests: XCTestCase {
    func testCommands() {
        XCTAssertEqual(AlfalfaCommand.sendNow("Para").line, #"{"texto":"Para","tipo":"enviar_ahora"}"# + "\n")
        XCTAssertEqual(AlfalfaCommand.redirect.line, #"{"tipo":"redirigir"}"# + "\n")
    }

    func testEchoDeliversTheQueuedReplyAndACutTurnLeavesTheSessionReady() {
        var timeline = Timeline()
        timeline.apply(.event(AgentEvent(role: "user", text: "primero", time: 1)))
        timeline.apply(.turnFinished(Outcome(state: "completed", summary: "", exitCode: 0, tokens: 1)))
        timeline.addUser("No, otra cosa", queued: true)
        timeline.apply(.event(AgentEvent(role: "user", text: "No, otra cosa", time: 2)))
        XCTAssertEqual(timeline.status, .working)
        guard case let .user(_, text, queued)? = timeline.items.last else { return XCTFail("mensaje") }
        XCTAssertEqual(text, "No, otra cosa")
        XCTAssertFalse(queued, "el eco del motor lo marca entregado")
        XCTAssertEqual(timeline.items.filter { if case .user = $0 { return true } else { return false } }.count, 2)
        // «Enviar ahora» corta el turno: la sesión queda lista, no interrumpida.
        timeline.apply(.turnFinished(Outcome(state: "cortado", summary: "Turno interrumpido por el usuario.",
                                             exitCode: 1, tokens: 1)))
        XCTAssertEqual(timeline.status, .idle)
        XCTAssertTrue(timeline.status.isActive)
    }
}
