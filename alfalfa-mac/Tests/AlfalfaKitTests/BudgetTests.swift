@testable import AlfalfaKit
import XCTest

/// Presupuesto con tope duro (sesión y mes) y lo que ahorra la caché de prompts.
final class BudgetTests: XCTestCase {

    // MARK: - Métodos públicos

    func testDecodesTheBudgetEventWithItsWarningAndCache() throws {
        let line = #"{"version":2,"tipo":"presupuesto","sesion":{"tope":1.0,"gastado":0.84},"#
            + #""mes":{"tope":null,"gastado":3.2,"mes":"2026-11"},"aviso":"sesion","#
            + #""cache":{"leidos":160000,"escritos":20000,"sin_cache":0.58,"con_cache":0.16,"ahorro":0.42}}"#
        guard case let .budget(status, cache)? = AlfalfaMessage.decode(line: line) else { return XCTFail("sin presupuesto") }
        XCTAssertEqual(status.session, BudgetStatus.Limit(cap: 1.0, spent: 0.84))
        XCTAssertEqual(status.month, BudgetStatus.Limit(cap: nil, spent: 3.2))
        XCTAssertEqual(status.monthName, "2026-11")
        XCTAssertEqual(status.warning, .session)
        XCTAssertNil(status.exhausted)
        XCTAssertTrue(status.hasCap)
        XCTAssertEqual(status.session.text, "0,84 $ de 1,00 $")
        XCTAssertEqual(status.month.text, "3,20 $")
        let savings = try XCTUnwrap(cache)
        XCTAssertEqual(savings.read, 160_000)
        XCTAssertEqual(savings.written, 20_000)
        XCTAssertEqual(try XCTUnwrap(savings.saved), 0.42, accuracy: 1e-9)
        XCTAssertEqual(savings.text, "Caché: ahorró 0,42 $")
        XCTAssertEqual(savings.detail, "0,58 $ sin caché · 0,16 $ con ella · 160.000 tokens leídos · 20.000 escritos")
        XCTAssertNil(AlfalfaMessage.decode(line: #"{"version":2,"tipo":"presupuesto","sesion":{}}"#))
    }

    func testCacheWithoutPriceOrWithoutTokens() {
        XCTAssertNil(CacheSavings(object: ["leidos": 0, "escritos": 0]), "Ollama: nada que enseñar")
        XCTAssertNil(CacheSavings(object: nil))
        let unpriced = CacheSavings(object: ["leidos": 1500])
        XCTAssertNil(unpriced?.saved)
        XCTAssertEqual(unpriced?.text, "Caché: 1.500 tokens leídos")
        let costly = CacheSavings(read: 0, written: 10, withoutCache: 0.01, withCache: 0.03)
        XCTAssertEqual(costly.savedText, "aún cuesta 0,02 $ más")
    }

    func testTheOutcomeCarriesTheCacheToTheTimeline() throws {
        let line = #"{"version":2,"tipo":"turno_terminado","estado":"blocked","resumen":"El presupuesto de esta "#
            + #"sesión se agotó: se gastaron 1,26 $ de 1,00 $.","codigo_salida":1,"tokens":10,"coste":1.26,"#
            + #""cache":{"leidos":80000,"escritos":0,"sin_cache":0.4,"con_cache":0.1,"ahorro":0.3}}"#
        guard case let .turnFinished(outcome)? = AlfalfaMessage.decode(line: line) else { return XCTFail() }
        XCTAssertEqual(outcome.cache?.read, 80_000)
        var timeline = Timeline()
        timeline.apply(.turnFinished(outcome))
        XCTAssertEqual(timeline.cacheSavings?.savedText, "ahorró 0,30 $")
        XCTAssertEqual(timeline.status, .blocked)
        // El corte se lee al final del turno: dice cuánto se gastó y cómo subir el tope.
        XCTAssertTrue(timeline.items.contains { item in
            if case let .notice(_, text) = item { return text.contains("1,26 $ de 1,00 $") }
            return false
        })
    }

    func testTheEightyPercentWarningIsANotice() {
        var timeline = Timeline()
        let status = BudgetStatus(session: .init(cap: 5, spent: 4.1), month: .init(cap: nil, spent: 4.1),
                                  warning: .session)
        timeline.apply(.budget(status, cache: nil))
        XCTAssertEqual(timeline.budget, status)
        guard case let .notice(_, text)? = timeline.items.last else { return XCTFail("sin aviso") }
        XCTAssertEqual(text, "Llevas 4,10 $ de 5,00 $ del presupuesto de esta sesión.")
        // Sin aviso, sólo se actualiza lo gastado.
        let count = timeline.items.count
        timeline.apply(.budget(BudgetStatus(session: .init(cap: 5, spent: 4.2), month: .init(cap: nil, spent: 4.2)),
                               cache: CacheSavings(read: 10)))
        XCTAssertEqual(timeline.items.count, count)
        XCTAssertEqual(timeline.budget?.session.spent, 4.2)
        XCTAssertEqual(timeline.cacheSavings?.read, 10)
    }

    func testTheSettingsCommandAndTheLauncher() {
        XCTAssertEqual(AlfalfaCommand.budget(session: 5, month: nil).line,
                       #"{"mes":null,"sesion":5,"tipo":"presupuesto"}"# + "\n")
        XCTAssertEqual(AlfalfaCommand.budget(session: nil, month: 12.5).line,
                       #"{"mes":12.5,"sesion":null,"tipo":"presupuesto"}"# + "\n")
        XCTAssertEqual(Launcher.stdioArguments(task: "x", resume: nil, model: nil, effort: nil, readOnly: false,
                                               budget: 5, monthlyBudget: 12.5),
                       ["--stdio", "--presupuesto", "5", "--presupuesto-mes", "12.5", "--", "x"])
        // Sin redondear: lo mismo que manda la orden en vivo.
        XCTAssertEqual(BudgetFormat.argument(12.345), "12.345")
        XCTAssertEqual(Launcher.stdioArguments(task: "x", resume: nil, model: nil, effort: nil, readOnly: false,
                                               budget: 0),
                       ["--stdio", "--", "x"])
    }

    func testTheFieldAcceptsSpanishAmounts() {
        XCTAssertEqual(BudgetFormat.parse("12,50"), 12.5)
        XCTAssertEqual(BudgetFormat.parse(" 3 $ "), 3)
        XCTAssertNil(BudgetFormat.parse(""))
        XCTAssertNil(BudgetFormat.parse("0"))
        XCTAssertNil(BudgetFormat.parse("mucho"))
        XCTAssertNil(BudgetFormat.parse("-2"))
        // El campo: vacío o cualquier cero quita el tope; lo que no es un importe no cambia nada.
        for zero in ["", "  ", "0", "0,00", "0.0", "0 $"] { XCTAssertEqual(BudgetFormat.entry(zero), .noCap, zero) }
        XCTAssertEqual(BudgetFormat.entry("15"), .amount(15))
        XCTAssertEqual(BudgetFormat.entry("1,5"), .amount(1.5))
        XCTAssertEqual(BudgetFormat.entry("1,"), .amount(1), "a medias vale 1, por eso se aplica al confirmar")
        XCTAssertEqual(BudgetFormat.entry("mucho"), .invalid)
        XCTAssertEqual(BudgetFormat.entry("-2"), .invalid)
        XCTAssertEqual(BudgetFormat.field(12.5), "12,50")
        XCTAssertEqual(BudgetFormat.field(5), "5")
        XCTAssertEqual(BudgetFormat.field(nil), "")
    }

    func testTheSettingsAreSavedWithoutCapByDefault() throws {
        let suite = "alfalfa.pruebas.\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: suite))
        defer { defaults.removePersistentDomain(forName: suite) }
        let preferences = Preferences(defaults: defaults)
        XCTAssertNil(preferences.sessionBudget)
        XCTAssertNil(preferences.monthlyBudget)
        preferences.sessionBudget = 5
        preferences.monthlyBudget = 40
        XCTAssertEqual(Preferences(defaults: defaults).sessionBudget, 5)
        XCTAssertEqual(Preferences(defaults: defaults).monthlyBudget, 40)
        preferences.sessionBudget = 0
        XCTAssertNil(preferences.sessionBudget)
        XCTAssertNil(defaults.object(forKey: Preferences.Key.sessionBudget))
    }
}
