@testable import AlfalfaKit
import XCTest

/// Frase sugerida, como la de la app de Codex: el cierre propone el siguiente mensaje del usuario
/// y el cuadro vacío lo enseña en gris hasta que el usuario escribe, envía o empieza otro turno.
final class SuggestionTests: XCTestCase {

    private func finished(_ wrapUp: WrapUp?) -> AlfalfaMessage {
        .turnFinished(Outcome(state: "completed", summary: "", exitCode: 0, tokens: 1, wrapUp: wrapUp))
    }

    func testDecodesTheSuggestionInOneLine() {
        let line = #"{"version":2,"tipo":"turno_terminado","estado":"completed","codigo_salida":0,"cierre":{"resumen":"Listo.","sugerencia":"Añade pruebas\n para la resta."}}"#
        XCTAssertEqual(AlfalfaMessage.decode(line: line),
                       .turnFinished(Outcome(state: "completed", summary: "", exitCode: 0, tokens: 0,
                                             wrapUp: WrapUp(recap: "Listo.", suggestion: "Añade pruebas para la resta."))))
        XCTAssertNil(WrapUp(cierre: ["resumen": "Listo.", "sugerencia": "   "])?.suggestion)
        // Sin wrap_up no hay sugerencia: un cierre de sólo sesiones propuestas no la trae.
        XCTAssertNil(WrapUp(cierre: ["sugerencia": "Sigue."]))
    }

    func testShowsTheLastSuggestionUntilTheUserWrites() {
        var timeline = Timeline()
        timeline.apply(finished(WrapUp(recap: "Listo.", suggestion: "Añade la resta.")))
        XCTAssertEqual(timeline.suggestion, "Añade la resta.")
        timeline.dismissSuggestion()
        XCTAssertNil(timeline.suggestion)
        // El siguiente cierre trae la suya, aunque se descartara la anterior.
        timeline.apply(finished(WrapUp(recap: "Hecho.", suggestion: "Ahora la división.")))
        XCTAssertEqual(timeline.suggestion, "Ahora la división.")
    }

    func testSendingOrANewTurnRemovesIt() {
        var timeline = Timeline()
        timeline.apply(finished(WrapUp(recap: "Listo.", suggestion: "Añade la resta.")))
        _ = timeline.addUser("Otra cosa", queued: true)
        XCTAssertNil(timeline.suggestion)
        // Un cierre sin sugerencia no deja ver la del turno anterior.
        timeline.apply(finished(WrapUp(recap: "Hecho.")))
        XCTAssertNil(timeline.suggestion)
        timeline.apply(finished(nil))
        XCTAssertNil(timeline.suggestion)
    }

    func testClosingTheBoxRemovesIt() {
        var timeline = Timeline()
        timeline.apply(finished(WrapUp(recap: "Listo.", suggestion: "Añade la resta.")))
        guard case let .wrapUp(id, _, _)? = timeline.items.last else { return XCTFail("falta el cierre") }
        timeline.dismissWrapUp(id: id)
        XCTAssertNil(timeline.suggestion)
    }

    func testNotWhileATurnIsRunning() {
        var timeline = Timeline()
        timeline.apply(finished(WrapUp(recap: "Listo.", suggestion: "Añade la resta.")))
        // Un turno que arranca sin mensaje del usuario (otra sesión, un aviso): la sugerencia espera.
        timeline.apply(.event(AgentEvent(role: "agent", text: "Sigo con lo pendiente.", time: 3)))
        XCTAssertTrue(timeline.status.isRunning)
        XCTAssertNil(timeline.suggestion)
    }

    func testARestoredSessionKeepsIt() {
        let line = #"{"version":2,"tipo":"historial","sesion":"abc","eventos":[{"rol":"user","texto":"Hola","hora":1},{"rol":"agent","texto":"Listo","hora":2}],"cierre":{"resumen":"Hecho.","sugerencia":"Añade la resta."}}"#
        guard case let .history("abc", events, wrapUp, _, _, _)? = AlfalfaMessage.decode(line: line) else {
            return XCTFail("no decodifica")
        }
        XCTAssertEqual(Timeline(history: events, wrapUp: wrapUp).suggestion, "Añade la resta.")
    }
}
