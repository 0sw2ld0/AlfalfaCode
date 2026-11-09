@testable import AlfalfaKit
import XCTest

/// Modelos que cobran más por contexto largo: Ahorro o Contexto completo, el aviso y el selector.
final class PriceTierTests: XCTestCase {
    private func decode(_ line: String) -> AlfalfaMessage? { AlfalfaMessage.decode(line: line) }

    func testSelectorShowsWindowAndCheapThreshold() {
        let line = #"{"version":2,"tipo":"modelos","modelos":[{"name":"anthropic/claude-haiku-5-5","#
            + #""nombre":"Claude Haiku 5.5","proveedor":"anthropic","ventana":1000000,"#
            + #""barato_hasta":100000,"precio":{"entrada":0.1,"salida":0.5}},{"name":"x","#
            + #""ventana":200000,"barato_hasta":272000}]}"#
        guard case let .models(rows)? = decode(line) else { return XCTFail("sin modelos") }
        XCTAssertEqual(rows[0].cheapUntil, 100_000)
        XCTAssertEqual(CatalogFormat.detail(rows[0]), "1M · barato hasta 100K · 0,1/0,5 $ por M")
        // Un umbral que no cabe en la ventana no se enseña.
        XCTAssertEqual(CatalogFormat.detail(rows[1]), "200K")
    }

    func testContextEventCarriesTierAndMode() {
        let line = #"{"version":2,"tipo":"contexto","tokens":50000,"limite":1000000,"umbral":90000,"#
            + #""tarifa":{"desde":100000,"entrada":5.0,"salida":5.0,"modo":"ahorro"}}"#
        var timeline = Timeline()
        timeline.apply(decode(line)!)
        XCTAssertEqual(timeline.priceTier?.from, 100_000)
        XCTAssertEqual(timeline.priceTier?.multiplier, "5×")
        XCTAssertEqual(timeline.contextMode, .saving)
        XCTAssertEqual(timeline.tokensUntilCompaction, 40_000)
    }

    func testMultiplierInWords() {
        XCTAssertEqual(PriceTier(from: 272_000, input: 2, output: 1.5).multiplier, "2× la entrada y 1,5× la salida")
    }

    func testWarningInSavingModeWaitsForAnAnswer() {
        let line = #"{"version":2,"tipo":"evento","rol":"system","hora":1,"texto":"A partir de 100K tokens,"#
            + #" Claude Haiku 5.5 cobra 5× toda la petición. Compactaré en unos 10K.","#
            + #""tarifa":{"desde":100000,"entrada":5.0,"salida":5.0,"modo":"ahorro","estimado":80000,"#
            + #""faltan":10000,"modelo":"Claude Haiku 5.5"}}"#
        var timeline = Timeline()
        timeline.apply(decode(line)!)
        guard case let .priceWarning(_, warning, answered)? = timeline.items.last else { return XCTFail("sin aviso") }
        XCTAssertFalse(answered)
        XCTAssertEqual(warning.headline, "A partir de 100K tokens, Claude Haiku 5.5 cobra 5×.")
        XCTAssertEqual(warning.detail, "Compactaré en unos 10K.")
        // Elegir contexto completo deja el aviso contestado.
        timeline.apply(decode(#"{"version":2,"tipo":"sesion","id":"s","cwd":"/tmp","contexto":"completo"}"#)!)
        XCTAssertEqual(timeline.contextMode, .full)
        guard case let .priceWarning(_, _, done)? = timeline.items.last else { return XCTFail("sin aviso") }
        XCTAssertTrue(done)
    }

    func testCompactionAnswersTheWarning() {
        var timeline = Timeline()
        timeline.apply(decode(#"{"version":2,"tipo":"evento","rol":"system","hora":1,"texto":"aviso","#
            + #""tarifa":{"desde":272000,"entrada":2,"salida":1.5,"modo":"ahorro","estimado":220000,"#
            + #""faltan":24000,"modelo":"GPT-6 Luna"}}"#)!)
        timeline.apply(decode(#"{"version":2,"tipo":"evento","rol":"system","hora":2,"texto":"hecho","#
            + #""compactacion":{"estado":"hecha","motivo":"auto","antes":240000,"despues":9000,"#
            + #""resumen":"r"}}"#)!)
        let answered = timeline.items.contains {
            if case .priceWarning(_, _, true) = $0 { return true } else { return false }
        }
        XCTAssertTrue(answered)
    }

    func testWarningInFullModeOnlyInforms() {
        let line = #"{"version":2,"tipo":"evento","rol":"system","hora":1,"texto":"t","#
            + #""tarifa":{"desde":100000,"entrada":5,"salida":5,"modo":"completo","estimado":101000,"#
            + #""modelo":"Claude Haiku 5.5"}}"#
        var timeline = Timeline()
        timeline.apply(decode(line)!)
        guard case let .priceWarning(_, warning, answered)? = timeline.items.last else { return XCTFail("sin aviso") }
        XCTAssertTrue(answered)
        XCTAssertNil(warning.detail)
    }

    func testContinuedSessionAndCommands() {
        let line = #"{"version":2,"tipo":"sesion_cambiada","id":"nueva","continuada_de":"vieja"}"#
        XCTAssertEqual(decode(line), .continued(id: "nueva", from: "vieja"))
        XCTAssertEqual(AlfalfaCommand.contextMode(.full).line, #"{"modo":"completo","tipo":"contexto"}"# + "\n")
        XCTAssertEqual(AlfalfaCommand.continueInNewSession.line, #"{"tipo":"continuar_en_nueva"}"# + "\n")
    }

    func testHistoryKeepsTheWarning() {
        let line = #"{"version":2,"tipo":"historial","sesion":"s","eventos":[{"rol":"system","texto":"t","#
            + #""hora":1,"tarifa":{"desde":100000,"entrada":5,"salida":5,"modo":"ahorro","#
            + #""estimado":80000,"faltan":10000,"modelo":"Claude Haiku 5.5"}}],"#
            + #""contexto":{"tokens":80000,"limite":1000000,"tarifa":{"desde":100000,"entrada":5,"#
            + #""salida":5,"modo":"completo"}}}"#
        guard case let .history(_, events, _, context, _, _)? = decode(line) else { return XCTFail("sin historial") }
        XCTAssertNotNil(events.first?.priceWarning)
        let timeline = Timeline(history: events, wrapUp: nil, context: context)
        XCTAssertEqual(timeline.contextMode, .full)
    }
}
