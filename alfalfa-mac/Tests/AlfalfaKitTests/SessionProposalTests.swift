@testable import AlfalfaKit
import XCTest

/// Las sesiones que propone el agente (`propose_session`) viajan en el cierre del turno.
final class SessionProposalTests: XCTestCase {

    // MARK: - Métodos públicos

    func testTheWrapUpCarriesTheProposals() {
        let cierre: [String: Any] = [
            "resumen": "Listo.",
            "sesiones": [["titulo": "Quitar el aviso", "encargo": "En tests/test_web.py cambia app= por transport=."]]
        ]
        XCTAssertEqual(WrapUp(cierre: cierre),
                       WrapUp(recap: "Listo.", sessions: [SessionProposal(
                           title: "Quitar el aviso", task: "En tests/test_web.py cambia app= por transport=.")]))
    }

    func testProposalsWithoutWrapUpStillShow() {
        let cierre: [String: Any] = ["pregunta": "¿Sigo?", "sesiones": [["titulo": "Uno", "encargo": "Haz uno."]]]
        let wrapUp = WrapUp(cierre: cierre)
        XCTAssertEqual(wrapUp?.recap, "")
        // Sin resumen no hay pregunta: la caja sólo enseña las sesiones.
        XCTAssertNil(wrapUp?.ask)
        XCTAssertEqual(wrapUp?.sessions.map(\.title), ["Uno"])
        XCTAssertNil(WrapUp(cierre: ["pregunta": "¿Sigo?"]))
    }

    func testRepeatedEmptyAndExtraProposalsAreDropped() {
        let rows: [Any] = [
            ["titulo": "Uno", "encargo": "Haz uno."], ["titulo": "Uno", "encargo": "Otra vez."],
            ["titulo": " ", "encargo": "Sin título."], ["titulo": "Sin encargo"], "no es una fila",
            ["titulo": "Dos", "encargo": "Haz dos."], ["titulo": "Tres", "encargo": "Haz tres."],
            ["titulo": "Cuatro", "encargo": "Haz cuatro."]
        ]
        XCTAssertEqual(SessionProposal.list(rows).map(\.title), ["Uno", "Dos", "Tres"])
    }

    func testDoItHereAsksThisSessionByTitle() {
        XCTAssertEqual(SessionProposal.doItHere(SessionProposal(title: "Quitar el aviso", task: "…")),
                       "Hazlo aquí: «Quitar el aviso».")
    }

    func testTheTurnMessageDecodesTheProposals() {
        let line = #"{"version":2,"tipo":"turno_terminado","estado":"completed","codigo_salida":0,"#
            + #""cierre":{"sesiones":[{"titulo":"Uno","encargo":"Haz uno."}]}}"#
        guard case let .turnFinished(outcome)? = AlfalfaMessage.decode(line: line) else { return XCTFail("turno") }
        XCTAssertEqual(outcome.wrapUp?.sessions, [SessionProposal(title: "Uno", task: "Haz uno.")])
    }
}
