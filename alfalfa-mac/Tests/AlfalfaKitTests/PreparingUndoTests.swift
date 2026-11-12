@testable import AlfalfaKit
import XCTest

/// «Preparando deshacer…»: el motor guarda la instantánea del turno y la fila viva lo dice en vez de «Pensando».
final class PreparingUndoTests: XCTestCase {

    // MARK: - Métodos públicos

    func testDecodesTheTextAndItsEnd() {
        XCTAssertEqual(AlfalfaMessage.decode(line: #"{"version":2,"tipo":"preparando","texto":"Preparando deshacer… (12 archivos, 3 KB)"}"#),
                       .preparingUndo("Preparando deshacer… (12 archivos, 3 KB)"))
        XCTAssertEqual(AlfalfaMessage.decode(line: #"{"version":2,"tipo":"preparando","texto":null}"#),
                       .preparingUndo(nil))
    }

    func testTheTimelineKeepsItUntilTheEngineOrTheTurnEndsIt() {
        var timeline = Timeline()
        XCTAssertNil(timeline.preparingUndo)
        timeline.apply(.preparingUndo("Preparando deshacer…"))
        XCTAssertEqual(timeline.preparingUndo, "Preparando deshacer…")
        timeline.apply(.preparingUndo(nil))
        XCTAssertNil(timeline.preparingUndo)
        timeline.apply(.preparingUndo("Preparando deshacer…"))
        timeline.apply(.turnFinished(Outcome(state: "completed", summary: "", exitCode: 0, tokens: 1)))
        XCTAssertNil(timeline.preparingUndo, "al acabar el turno, aunque se pierda el aviso")
        timeline.apply(.preparingUndo("Preparando deshacer…"))
        timeline.processExited(code: 9)
        XCTAssertNil(timeline.preparingUndo, "sin proceso no se prepara nada")
    }

    func testTheLiveRowSaysItInsteadOfThinking() {
        let thinking = LiveActivity.thinking(since: 10)
        XCTAssertEqual(thinking.phrase, LiveActivity.thinkingPhrase)
        let preparing = LiveActivity.thinking(since: 10, preparing: "Preparando deshacer…")
        XCTAssertEqual(preparing.phrase, "Preparando deshacer…")
        XCTAssertEqual(preparing.phase, .working)
        XCTAssertEqual(LiveActivity.of(group: [], preparing: "Preparando deshacer…").phrase, "Preparando deshacer…")
        XCTAssertEqual(LiveActivity.of(group: [], waiting: true, preparing: "Preparando deshacer…").phrase,
                       LiveActivity.waitingPhrase, "un permiso pendiente manda")
    }
}
