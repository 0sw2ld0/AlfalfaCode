@testable import AlfalfaKit
import XCTest

final class TaskQueueTests: XCTestCase {
    private func queue(_ state: String = "lista", jobState: String = "pendiente") throws -> TaskQueue {
        try XCTUnwrap(TaskQueue(value: ["id": "c1", "estado": state, "modo": "paralelo",
            "tokens_sesion": 200, "coste_sesion": 0.02, "encargos": [["id": "e1", "titulo": "Arregla suma",
                "estado": jobState, "sesion": "hija", "carpeta": "/tmp/demo", "rama": "alfalfa/cola-c1-e1"]]]))
    }

    func testCommandsAreIndependentOfTheMessageQueueAndDoNotRunOnPrepare() {
        XCTAssertEqual(AlfalfaCommand.taskQueue(.start).line, #"{"accion":"iniciar","tipo":"encargos"}"# + "\n")
        XCTAssertEqual(AlfalfaCommand.taskQueue(.prepare, tasks: ["A", "B"], mode: "paralelo").line,
                       #"{"accion":"preparar","encargos":["A","B"],"modo":"paralelo","paralelos":2,"tipo":"encargos"}"# + "\n")
        let args = Launcher.stdioArguments(task: nil, resume: nil, model: "simulado", effort: "low",
                                          readOnly: false, queue: true)
        XCTAssertTrue(args.contains("--cola-encargos"))
        XCTAssertFalse(args.contains("--reanudar"))
    }

    func testProgressKeepsSessionsAndWorktreesVisibleAndAccountsForTheWholeQueue() throws {
        var timeline = Timeline()
        timeline.apply(.taskQueue(try queue("ejecutando", jobState: "ejecutando")))
        XCTAssertEqual(timeline.status, .working)
        XCTAssertEqual(timeline.totalTokens, 200)
        XCTAssertEqual(timeline.totalCost, 0.02)
        XCTAssertEqual(timeline.taskQueue?.jobs.first?.session, "hija")
        XCTAssertEqual(timeline.taskQueue?.jobs.first?.branch, "alfalfa/cola-c1-e1")
        timeline.apply(.taskQueue(try queue("completada", jobState: "completed")))
        XCTAssertEqual(timeline.status, .idle)
        XCTAssertTrue(timeline.taskQueue?.finished == true)
        XCTAssertEqual(timeline.taskQueue?.jobs.first?.stateText, "Terminado")
    }

    func testClosingTheProcessPausesWithoutAutomaticallyRetryingAnInterruptedJob() throws {
        var timeline = Timeline()
        timeline.apply(.taskQueue(try queue("ejecutando", jobState: "ejecutando")))
        timeline.processExited(code: 130)
        XCTAssertEqual(timeline.taskQueue?.state, "pausada")
        XCTAssertEqual(timeline.taskQueue?.jobs.first?.state, "interrumpido")
        XCTAssertTrue(timeline.taskQueue?.canStart == true)
    }

    func testSavedStateDuringWakeDoesNotAcknowledgeANewStartOrPreparation() throws {
        let saved = try queue("pausada", jobState: "interrumpido")
        XCTAssertFalse(saved.acknowledges(.start, previousID: "c1"))
        XCTAssertTrue(try queue("ejecutando").acknowledges(.start, previousID: "c1"))
        XCTAssertFalse(saved.acknowledges(.prepare, previousID: "c1"))
        XCTAssertTrue(try queue().acknowledges(.prepare, previousID: "otra-cola"))
        XCTAssertFalse(try queue("ejecutando").acknowledges(.start, previousID: "otra-cola"))
    }

    func testProtocolRejectsMalformedQueues() throws {
        XCTAssertNil(AlfalfaMessage.decode(line: #"{"version":2,"tipo":"encargos","cola":{}}"#))
        let data = try JSONSerialization.data(withJSONObject: ["version": 2, "tipo": "encargos", "cola": [
            "id": "c1", "estado": "lista", "modo": "serie", "encargos": [["id": "e1", "titulo": "A", "estado": "pendiente"]]]])
        guard case let .taskQueue(queue) = AlfalfaMessage.decode(line: String(decoding: data, as: UTF8.self)) else {
            return XCTFail("No se decodificó la cola")
        }
        XCTAssertTrue(queue.canStart)
        XCTAssertEqual(queue.jobs.first?.stateText, "Pendiente")
    }
}
