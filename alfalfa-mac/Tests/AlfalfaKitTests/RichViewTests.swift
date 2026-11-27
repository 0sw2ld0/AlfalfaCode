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

    func testANewSessionTakesTheSettingAndKeepsIt() throws {
        let suite = "alfalfa.pruebas.\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: suite))
        defer { defaults.removePersistentDomain(forName: suite) }
        let preferences = Preferences(defaults: defaults)
        XCTAssertFalse(preferences.richView)                 // apagada por defecto en las sesiones nuevas
        XCTAssertFalse(preferences.richView(for: nil))       // una sesión que arranca, sin id todavía
        preferences.adoptRichView(for: "nueva")
        XCTAssertFalse(preferences.richView(for: "nueva"))
        preferences.richView = true                          // tocar Ajustes no cambia una sesión que ya fijó la suya
        XCTAssertFalse(preferences.richView(for: "nueva"))
        preferences.adoptRichView(for: "otra")
        XCTAssertTrue(preferences.richView(for: "otra"))
        preferences.setRichView(false, for: "otra")          // su panel sí
        preferences.adoptRichView(for: "otra")               // y adoptar no pisa la suya
        XCTAssertFalse(Preferences(defaults: defaults).richView(for: "otra"))
    }

    func testASessionFromBeforeTheUpdateKeepsItsScreens() throws {
        let suite = "alfalfa.pruebas.\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: suite))
        defer { defaults.removePersistentDomain(forName: suite) }
        let preferences = Preferences(defaults: defaults)
        // Sin la suya y sin tocar Ajustes: encendida, como cuando se creó (antes de 0.99.57 era el valor por defecto).
        XCTAssertTrue(preferences.richView(for: "vieja"))
        // Si el usuario tocó Ajustes, la sigue, como antes.
        preferences.richView = false
        XCTAssertFalse(preferences.richView(for: "vieja"))
        preferences.richView = true
        XCTAssertTrue(preferences.richView(for: "vieja"))
        preferences.setRichView(false, for: "vieja")
        XCTAssertFalse(preferences.richView(for: "vieja"))   // la suya gana
    }
}
