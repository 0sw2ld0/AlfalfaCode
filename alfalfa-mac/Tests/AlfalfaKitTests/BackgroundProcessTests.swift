@testable import AlfalfaKit
import XCTest

/// Órdenes en segundo plano, como los background terminals de Codex.
final class BackgroundProcessTests: XCTestCase {
    func testProcessesEventAndStopCommand() throws {
        let line = #"{"version":2,"tipo":"procesos","procesos":[{"id":"p1","argv":["swift","run","Juego"],"proposito":"Abrir","desde":10,"vivo":true,"codigo":null,"sin_sandbox":true,"detenido":false,"ultimas":["listo"]},{"id":"p2","argv":["make"],"vivo":false,"codigo":2},{"argv":["sin id"]}]}"#
        guard case let .processes(list)? = AlfalfaMessage.decode(line: line) else { return XCTFail("procesos") }
        XCTAssertEqual(list, [
            BackgroundProcess(id: "p1", argv: ["swift", "run", "Juego"], purpose: "Abrir", since: 10, alive: true,
                              exitCode: nil, unsandboxed: true, lastLines: ["listo"]),
            BackgroundProcess(id: "p2", argv: ["make"], alive: false, exitCode: 2)
        ])
        var timeline = Timeline()
        timeline.apply(.processes(list))
        XCTAssertEqual(timeline.liveProcesses.map(\.id), ["p1"])
        timeline.processExited(code: 0)
        XCTAssertTrue(timeline.liveProcesses.isEmpty, "con alfalfa se cierran sus procesos")
        XCTAssertEqual(AlfalfaCommand.stopProcess("p1").line, #"{"id":"p1","tipo":"detener_proceso"}"# + "\n")
    }

    func testProcessEndingBetweenTurnsKeepsSessionReady() {
        var timeline = Timeline()
        timeline.apply(.event(AgentEvent(role: "user", text: "abre el juego", time: 1)))
        timeline.apply(.turnFinished(Outcome(state: "completed", summary: "", exitCode: 0, tokens: 1)))
        XCTAssertEqual(timeline.status, .idle)
        timeline.apply(.event(AgentEvent(role: "system", text: "El proceso p2 (swift run Juego) terminó con código 0.", time: 2)))
        XCTAssertEqual(timeline.status, .idle, "el aviso no abre un turno")
        XCTAssertFalse(timeline.status.isRunning)
        timeline.apply(.event(AgentEvent(role: "user", text: "otra", time: 3)))
        XCTAssertEqual(timeline.status, .working)
    }

    func testSlashCommands() {
        XCTAssertEqual(SlashCommand.named("ps"), .procesos)
        XCTAssertEqual(SlashCommand.named("clean"), .cerrarProcesos)
        XCTAssertEqual(SlashCommand.named("cerrar-procesos"), .cerrarProcesos)
        XCTAssertFalse(SlashCommand.cerrarProcesos.available(running: false, dormant: true))
        XCTAssertTrue(SlashCommand.procesos.available(running: true, dormant: false))
    }
}
