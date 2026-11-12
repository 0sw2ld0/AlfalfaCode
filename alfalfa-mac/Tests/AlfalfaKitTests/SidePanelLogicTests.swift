@testable import AlfalfaKit
import XCTest

/// El tope de una sesión puesto desde su panel, el ancho de la barra lateral y lo gastado al reanudar.
final class SidePanelLogicTests: XCTestCase {

    // MARK: - Propiedades privadas

    private var suite = ""
    private var defaults = UserDefaults.standard

    // MARK: - Métodos públicos

    override func setUp() {
        super.setUp()
        suite = "alfalfa.pruebas.\(UUID().uuidString)"
        defaults = UserDefaults(suiteName: suite) ?? .standard
    }

    override func tearDown() {
        defaults.removePersistentDomain(forName: suite)
        super.tearDown()
    }

    func testASessionCapOnlyChangesThatSession() {
        let preferences = Preferences(defaults: defaults)
        preferences.sessionBudget = 20
        XCTAssertNil(preferences.sessionCap(for: "a"))
        XCTAssertEqual(preferences.sessionBudget(for: "a"), 20)
        XCTAssertEqual(preferences.sessionBudget(for: nil), 20)

        preferences.setSessionCap(.amount(5), for: "a")
        XCTAssertEqual(preferences.sessionCap(for: "a"), .amount(5))
        XCTAssertEqual(preferences.sessionBudget(for: "a"), 5)
        XCTAssertEqual(preferences.sessionBudget(for: "b"), 20, "las demás siguen el de Ajustes")
        XCTAssertEqual(preferences.sessionBudget, 20, "el de Ajustes no cambia")

        // Quitar el tope de una sesión aunque Ajustes ponga uno.
        preferences.setSessionCap(.noCap, for: "a")
        XCTAssertEqual(preferences.sessionCap(for: "a"), .noCap)
        XCTAssertNil(preferences.sessionBudget(for: "a"))

        // Un cambio en Ajustes no pisa el tope propio.
        preferences.sessionBudget = 30
        XCTAssertNil(preferences.sessionBudget(for: "a"))
        XCTAssertEqual(preferences.sessionBudget(for: "b"), 30)

        // Volver al de Ajustes; sin topes propios no queda nada guardado.
        preferences.setSessionCap(nil, for: "a")
        XCTAssertNil(preferences.sessionCap(for: "a"))
        XCTAssertEqual(preferences.sessionBudget(for: "a"), 30)
        XCTAssertNil(defaults.object(forKey: Preferences.Key.sessionBudgets))
    }

    func testASessionCapSurvivesANewPreferencesAndIgnoresBadValues() {
        Preferences(defaults: defaults).setSessionCap(.amount(12.5), for: "a")
        XCTAssertEqual(Preferences(defaults: defaults).sessionBudget(for: "a"), 12.5)
        Preferences(defaults: defaults).setSessionCap(.amount(-3), for: "b")
        XCTAssertEqual(Preferences(defaults: defaults).sessionCap(for: "b"), .noCap)
        defaults.set(["c": "mucho"], forKey: Preferences.Key.sessionBudgets)
        XCTAssertNil(Preferences(defaults: defaults).sessionCap(for: "c"))
    }

    func testWhatIsTypedInThePanelField() {
        XCTAssertEqual(SessionCap(entry: "5"), .amount(5))
        XCTAssertEqual(SessionCap(entry: "12,50 $"), .amount(12.5))
        XCTAssertEqual(SessionCap(entry: ""), .noCap)
        XCTAssertEqual(SessionCap(entry: "0"), .noCap)
        XCTAssertNil(SessionCap(entry: "cinco"))
        XCTAssertNil(SessionCap.noCap.value)
        XCTAssertEqual(SessionCap.amount(3).value, 3)
    }

    func testTheSidebarWidthStaysWithinItsLimitsAndIsRemembered() {
        XCTAssertEqual(SidebarWidth.clamp(100), SidebarWidth.minimum)
        XCTAssertEqual(SidebarWidth.clamp(900), SidebarWidth.maximum)
        XCTAssertEqual(SidebarWidth.clamp(301.4), 301)
        XCTAssertEqual(SidebarWidth.clamp(.nan), SidebarWidth.standard)
        XCTAssertEqual(SidebarWidth.dragged(from: 260, by: 60), 320)
        XCTAssertEqual(SidebarWidth.dragged(from: 260, by: -200), 200)
        XCTAssertEqual(SidebarWidth.dragged(from: 400, by: 80), 420)

        let preferences = Preferences(defaults: defaults)
        XCTAssertEqual(preferences.sidebarWidth, SidebarWidth.standard)
        preferences.sidebarWidth = 333
        XCTAssertEqual(Preferences(defaults: defaults).sidebarWidth, 333)
        preferences.sidebarWidth = 1000
        XCTAssertEqual(Preferences(defaults: defaults).sidebarWidth, SidebarWidth.maximum)
        defaults.set(50, forKey: Preferences.Key.sidebarWidth)
        XCTAssertEqual(Preferences(defaults: defaults).sidebarWidth, SidebarWidth.minimum)
    }

    func testAResumedSessionShowsWhatItAlreadySpent() throws {
        let line = #"{"version":2,"tipo":"sesion","id":"a","cwd":"/r","modelo":"m","esfuerzo":"high","tokens":691895,"coste":1.77}"#
        guard case let .session(info)? = AlfalfaMessage.decode(line: line) else { return XCTFail("sin sesión") }
        XCTAssertEqual(info.tokens, 691_895)
        XCTAssertEqual(try XCTUnwrap(info.cost), 1.77, accuracy: 1e-9)
        var timeline = Timeline()
        timeline.apply(.session(info))
        XCTAssertEqual(timeline.totalTokens, 691_895)
        XCTAssertEqual(try XCTUnwrap(timeline.totalCost), 1.77, accuracy: 1e-9)

        // Un motor anterior no lo manda: no se pisa lo que ya traía el historial.
        let old = #"{"version":2,"tipo":"sesion","id":"a","cwd":"/r","modelo":"m","esfuerzo":"high"}"#
        guard case let .session(older)? = AlfalfaMessage.decode(line: old) else { return XCTFail("sin sesión") }
        var restored = Timeline(history: [], wrapUp: nil, tokens: 500, cost: 0.2)
        restored.apply(.session(older))
        XCTAssertEqual(restored.totalTokens, 500)
        XCTAssertEqual(restored.totalCost, 0.2)
    }
}
