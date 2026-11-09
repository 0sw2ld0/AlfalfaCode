@testable import AlfalfaKit
import XCTest

/// Lo que llega del catálogo de models.dev: «Gratis ahora», la clave que falta, la ventana,
/// el precio y el coste estimado por turno y por sesión.
final class CatalogTests: XCTestCase {

    // MARK: - Métodos públicos

    func testModelRowsCarryWindowPriceFreeAndMissingKey() {
        let line = #"{"version":2,"tipo":"modelos","modelos":["#
            + #"{"name":"openrouter/google/gemma:free","nombre":"Gemma","proveedor":"openrouter","#
            + #""nombre_proveedor":"OpenRouter","ventana":262144,"precio":{"entrada":0,"salida":0},"gratis":true,"#
            + #""aviso":"Gratis: puede usar tus datos para entrenar","falta_clave":{"variable":"OPENROUTER_API_KEY","#
            + #""donde":"https://openrouter.ai/settings/keys","texto":"Falta la clave de OpenRouter."}},"#
            + #"{"name":"openai/gpt-6-luna","nombre":"GPT-6 Luna","proveedor":"openai","ventana":272000,"#
            + #""precio":{"entrada":0.25,"salida":2}},"#
            + #"{"name":"glm-5.3-flash:cloud","proveedor":"ollama","precio":{"entrada":-1,"salida":2}}]}"#
        guard case let .models(rows)? = AlfalfaMessage.decode(line: line) else { return XCTFail("modelos") }
        XCTAssertEqual(rows.count, 3)
        let gemma = rows[0]
        XCTAssertTrue(gemma.free)
        XCTAssertEqual(gemma.providerName, "OpenRouter")
        XCTAssertEqual(gemma.context, 262144)
        XCTAssertEqual(gemma.price, ModelPrice(input: 0, output: 0))
        XCTAssertEqual(gemma.notice, CatalogFormat.freeNotice)
        XCTAssertEqual(gemma.missingKey?.variable, "OPENROUTER_API_KEY")
        XCTAssertEqual(gemma.missingKey?.url, "https://openrouter.ai/settings/keys")
        XCTAssertEqual(CatalogFormat.detail(gemma), "262K · gratis · sin clave")
        XCTAssertEqual(CatalogFormat.detail(rows[1]), "272K · 0,25/2 $ por M")
        XCTAssertFalse(rows[1].free)
        XCTAssertNil(rows[2].price, "un precio negativo no se acepta")
        XCTAssertNil(CatalogFormat.detail(rows[2]))
    }

    func testFormats() {
        XCTAssertEqual(CatalogFormat.window(1_000_000), "1M")
        XCTAssertEqual(CatalogFormat.window(1_050_000), "1,05M")
        XCTAssertEqual(CatalogFormat.window(200_000), "200K")
        XCTAssertEqual(CatalogFormat.price(ModelPrice(input: 3, output: 15)), "3/15 $ por M")
        XCTAssertEqual(CatalogFormat.price(ModelPrice(input: 0.075, output: 0.2)), "0,075/0,2 $ por M")
        XCTAssertEqual(CatalogFormat.cost(0), "0 $")
        XCTAssertEqual(CatalogFormat.cost(0.004), "< 0,01 $")
        XCTAssertEqual(CatalogFormat.cost(0.374), "0,37 $")
        XCTAssertEqual(CatalogFormat.cost(12.4), "12,40 $")
    }

    func testTurnAndSessionCostReachTheTimeline() {
        var timeline = Timeline()
        let line = #"{"version":2,"tipo":"turno_terminado","estado":"completed","resumen":"","codigo_salida":0,"#
            + #""tokens":1500,"coste":0.75,"tokens_turno":250,"coste_turno":0.25}"#
        guard let message = AlfalfaMessage.decode(line: line) else { return XCTFail("turno") }
        timeline.apply(message)
        XCTAssertEqual(timeline.totalTokens, 1500)
        XCTAssertEqual(timeline.totalCost, 0.75)
        XCTAssertEqual(timeline.lastTurnTokens, 250)
        XCTAssertEqual(timeline.lastTurnCost, 0.25)
        // Un turno sin precio (Ollama) no borra el coste que ya había.
        timeline.apply(.turnFinished(Outcome(state: "completed", summary: "", exitCode: 0, tokens: 1600)))
        XCTAssertEqual(timeline.totalCost, 0.75)
        XCTAssertNil(timeline.lastTurnCost)
    }

    func testHistoryAndSessionsCarryTheCost() {
        let history = #"{"version":2,"tipo":"historial","sesion":"s","eventos":[],"tokens":9,"coste":1.5}"#
        guard case let .history(_, _, _, _, tokens, cost)? = AlfalfaMessage.decode(line: history) else {
            return XCTFail("historial")
        }
        XCTAssertEqual(Timeline(history: [], wrapUp: nil, tokens: tokens, cost: cost).totalCost, 1.5)
        let sessions = #"{"version":2,"tipo":"sesiones","sesiones":[{"id":"a","estado":"completed","coste":0.02},"#
            + #"{"id":"b","estado":"completed","coste":true}]}"#
        guard case let .sessions(rows)? = AlfalfaMessage.decode(line: sessions) else { return XCTFail("sesiones") }
        XCTAssertEqual(rows.map(\.cost), [0.02, nil])
    }

    func testCompatibleProvidersInTheProvidersScreen() {
        let line = #"{"version":2,"tipo":"proveedores","por_defecto":"ollama","proveedores":["#
            + #"{"id":"opencode","nombre":"OpenCode Zen","configurado":false,"clave":false,"compatible":true,"#
            + #""variable":"OPENCODE_API_KEY","donde":"https://opencode.ai/auth","#
            + #""aviso":"Los modelos gratis de OpenCode Zen sólo se pueden usar desde OpenCode."}]}"#
        guard case let .providers(_, rows)? = AlfalfaMessage.decode(line: line) else { return XCTFail("proveedores") }
        XCTAssertEqual(rows.first?.compatible, true)
        XCTAssertEqual(rows.first?.variable, "OPENCODE_API_KEY")
        XCTAssertEqual(rows.first?.keyPage, "https://opencode.ai/auth")
        XCTAssertNotNil(rows.first?.notice)
    }
}
