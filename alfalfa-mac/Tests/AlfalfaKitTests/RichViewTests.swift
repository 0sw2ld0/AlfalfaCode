@testable import AlfalfaKit
import XCTest

/// «Vista enriquecida»: el interruptor del panel con el que el agente dibuja pantallas en el chat o sólo escribe.
final class RichViewTests: XCTestCase {

    // MARK: - Métodos públicos

    func testTheSessionAnnouncesItOnlyWithAJSONBoolean() {
        let base = #"{"version":2,"tipo":"sesion","id":"s1","cwd":"/x""#
        guard case let .session(on)? = AlfalfaMessage.decode(line: base + #","vista_enriquecida":false}"#) else {
            return XCTFail("sesión")
        }
        XCTAssertEqual(on.richView, false)
        guard case let .session(old)? = AlfalfaMessage.decode(line: base + "}"),
              case let .session(number)? = AlfalfaMessage.decode(line: base + #","vista_enriquecida":1}"#) else {
            return XCTFail("sesión")
        }
        XCTAssertNil(old.richView)        // un motor anterior: el panel no enseña el interruptor
        XCTAssertNil(number.richView)
    }

    func testTheSwitchCommand() throws {
        XCTAssertEqual(AlfalfaCommand.richView(false).line, #"{"activo":false,"tipo":"vista_enriquecida"}"# + "\n")
        let order = try JSONSerialization.jsonObject(with: Data(XCTUnwrap(PacaiClient().send(.richView(true))
            .lines.first).utf8)) as? [String: Any]
        XCTAssertEqual(order?["type"] as? String, "alfalfa.set_rich_view")
        XCTAssertEqual(order?["payload"] as? [String: Bool], ["active": true])
    }

    func testEachSessionKeepsItsOwnAndTheRestFollowTheSetting() throws {
        let suite = "alfalfa.pruebas.\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: suite))
        defer { defaults.removePersistentDomain(forName: suite) }
        let preferences = Preferences(defaults: defaults)
        XCTAssertTrue(preferences.richView)
        XCTAssertTrue(preferences.richView(for: "s1"))
        preferences.setRichView(false, for: "s1")
        preferences.richView = false
        XCTAssertFalse(Preferences(defaults: defaults).richView(for: "s1"))
        XCTAssertFalse(preferences.richView(for: "s2"))      // sigue la de Ajustes
        preferences.richView = true
        XCTAssertTrue(preferences.richView(for: "s2"))
        XCTAssertFalse(preferences.richView(for: "s1"))      // la suya no cambia
        XCTAssertTrue(preferences.richView(for: nil))
    }
}
