@testable import AlfalfaKit
import XCTest

final class CodeReviewTests: XCTestCase {
    private func message(_ state: String, findings: [[String: Any]] = []) throws -> AlfalfaMessage {
        let data = try JSONSerialization.data(withJSONObject: ["version": 2, "tipo": "revision_codigo", "revision": [
            "turno": "t1", "estado": state, "autor": "autor", "modelo": "revisor", "resumen": "Revisión lista",
            "hallazgos": findings, "tokens_sesion": 120, "coste_sesion": 0.02]])
        return try XCTUnwrap(AlfalfaMessage.decode(line: String(decoding: data, as: UTF8.self)))
    }

    func testClickUsesADedicatedCommandAndCanCancel() {
        XCTAssertEqual(AlfalfaCommand.reviewCode(turn: "t1", model: "revisor").line,
                       #"{"modelo":"revisor","tipo":"revisar_codigo","turno":"t1"}"# + "\n")
        XCTAssertEqual(AlfalfaCommand.cancelCodeReview.line, #"{"tipo":"cancelar_revision_codigo"}"# + "\n")
        XCTAssertEqual(CodeReview.identity(of: "ollama/autor:cloud"), CodeReview.identity(of: "autor"))
        XCTAssertEqual(CodeReview.identity(of: "puente/autor-latest"), CodeReview.identity(of: "autor"))
    }

    func testClosingCardKeepsItsTurnAndReceivesFindingsWithoutOpeningAnotherSession() throws {
        let close: [String: Any] = ["resumen": "Terminado", "revision_codigo": [
            "turno": "t1", "estado": "disponible", "autor": "autor"]]
        let wrap = try XCTUnwrap(WrapUp(cierre: close))
        var timeline = Timeline(history: [], wrapUp: wrap)
        let count = timeline.items.count
        timeline.apply(try message("revisando"))
        XCTAssertEqual(timeline.items.count, count)
        let finding: [String: Any] = ["ruta": "suma.py", "linea": 2, "prioridad": 1,
                                     "titulo": "La suma resta", "detalle": "Cambiar a + b"]
        timeline.apply(try message("terminada", findings: [finding]))
        guard case let .wrapUp(_, updated, answered)? = timeline.items.last else { return XCTFail("Falta el cierre") }
        let review = try XCTUnwrap(updated.review)
        XCTAssertEqual(review.findings.first?.path, "suma.py")
        XCTAssertEqual(review.findings.first?.line, 2)
        XCTAssertTrue(review.canCorrect)
        XCTAssertFalse(review.canRequest)
        XCTAssertTrue(review.correction.contains("t1"))
        XCTAssertFalse(answered)
        XCTAssertEqual(timeline.items.count, count)
        XCTAssertEqual(timeline.totalTokens, 120)
        XCTAssertEqual(timeline.totalCost, 0.02)
        XCTAssertEqual(review.candidates(in: [ModelInfo(name: "autor:cloud"), ModelInfo(name: "embed-prueba"),
                                             ModelInfo(name: "revisor")]).map(\.name), ["revisor"])
    }

    func testLateReviewCannotReplaceTheClosingCardOfAnotherTurn() throws {
        let wrap = try XCTUnwrap(WrapUp(cierre: ["resumen": "Otro turno", "revision_codigo": [
            "turno": "t2", "estado": "disponible", "autor": "autor"]]))
        var timeline = Timeline(history: [], wrapUp: wrap)
        timeline.apply(try message("terminada"))
        guard case let .wrapUp(_, updated, _)? = timeline.items.last else { return XCTFail("Falta el cierre") }
        XCTAssertEqual(updated.review?.turn, "t2")
        XCTAssertEqual(updated.review?.state, "disponible")
    }

    func testInterruptedOrExhaustedReviewCanBeRequestedAgainAndLegacyClosesStillWork() throws {
        for state in ["cancelada", "sin_presupuesto", "fallida"] {
            guard case let .codeReview(review) = try message(state) else { return XCTFail("Revisión inválida") }
            XCTAssertTrue(review.canRequest)
            XCTAssertFalse(review.canCorrect)
        }
        XCTAssertNil(WrapUp(cierre: ["resumen": "Cierre anterior"])?.review)
        XCTAssertNil(AlfalfaMessage.decode(line: #"{"version":2,"tipo":"revision_codigo","revision":{}}"#))
    }
}
