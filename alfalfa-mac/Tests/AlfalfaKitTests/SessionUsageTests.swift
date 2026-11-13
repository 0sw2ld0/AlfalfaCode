@testable import AlfalfaKit
import XCTest

/// `uso_sesion`: el panel enseñaba «Tokens gastados: 0» durante todo un turno largo.
final class SessionUsageTests: XCTestCase {
    func testUsageArrivesMidTurn() throws {
        var timeline = Timeline()
        timeline.apply(try XCTUnwrap(AlfalfaMessage.decode(line: #"{"version":2,"tipo":"uso_sesion","tokens":7425904}"#)))
        XCTAssertEqual(timeline.totalTokens, 7_425_904)
        XCTAssertNil(timeline.totalCost, "Ollama no tiene precio: sin coste")
        timeline.apply(try XCTUnwrap(AlfalfaMessage.decode(
            line: #"{"version":2,"tipo":"uso_sesion","tokens":8000000,"coste":1.25}"#)))
        XCTAssertEqual(timeline.totalTokens, 8_000_000)
        XCTAssertEqual(timeline.totalCost, 1.25)
    }

    func testInvalidUsageIsIgnored() {
        XCTAssertNil(AlfalfaMessage.decode(line: #"{"version":2,"tipo":"uso_sesion","tokens":"muchos"}"#))
    }
}
