@testable import AlfalfaKit
import XCTest

/// Mantener el Mac despierto mientras trabaja un turno, como «Prevent sleep while running» de Codex.
final class KeepAwakeTests: XCTestCase {

    // MARK: - Métodos públicos

    func testDecodesTheEventOnlyWithAJSONBoolean() {
        XCTAssertEqual(AlfalfaMessage.decode(line: #"{"version":2,"tipo":"despierto","activo":true}"#),
                       .keepingAwake(true))
        XCTAssertEqual(AlfalfaMessage.decode(line: #"{"version":2,"tipo":"despierto","activo":false}"#),
                       .keepingAwake(false))
        XCTAssertNil(AlfalfaMessage.decode(line: #"{"version":2,"tipo":"despierto","activo":1}"#))
        XCTAssertNil(AlfalfaMessage.decode(line: #"{"version":2,"tipo":"despierto"}"#))
    }

    func testTheSwitchCommand() {
        XCTAssertEqual(AlfalfaCommand.keepAwake(false).line, #"{"activo":false,"tipo":"mantener_despierto"}"# + "\n")
        XCTAssertEqual(AlfalfaCommand.keepAwake(true).line, #"{"activo":true,"tipo":"mantener_despierto"}"# + "\n")
    }

    func testTheTimelineShowsItOnlyWhileTheHarnessHoldsIt() {
        var timeline = Timeline()
        XCTAssertFalse(timeline.keepingAwake)
        timeline.apply(.keepingAwake(true))
        XCTAssertTrue(timeline.keepingAwake)
        timeline.apply(.keepingAwake(false))
        XCTAssertFalse(timeline.keepingAwake, "el interruptor apagado a mitad de turno")
        timeline.apply(.keepingAwake(true))
        timeline.apply(.turnFinished(Outcome(state: "completed", summary: "", exitCode: 0, tokens: 1)))
        XCTAssertFalse(timeline.keepingAwake, "al acabar el turno, aunque se pierda el aviso")
        timeline.apply(.keepingAwake(true))
        timeline.processExited(code: 9)
        XCTAssertFalse(timeline.keepingAwake, "macOS la suelta con el proceso")
    }

    func testTheLauncherPassesTheSwitchOnlyWhenOff() {
        XCTAssertEqual(Launcher.stdioArguments(task: "x", resume: nil, model: nil, effort: nil, readOnly: false),
                       ["--stdio", "--", "x"])
        XCTAssertEqual(Launcher.stdioArguments(task: "x", resume: nil, model: nil, effort: nil, readOnly: false,
                                               keepAwake: false),
                       ["--stdio", "--sin-mantener-despierto", "--", "x"])
    }

    func testTheSettingIsOnUntilTurnedOff() throws {
        let suite = "alfalfa.pruebas.\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: suite))
        defer { defaults.removePersistentDomain(forName: suite) }
        let preferences = Preferences(defaults: defaults)
        XCTAssertTrue(preferences.keepAwake)
        preferences.keepAwake = false
        XCTAssertFalse(Preferences(defaults: defaults).keepAwake)
        preferences.keepAwake = true
        XCTAssertTrue(defaults.bool(forKey: Preferences.Key.keepAwake))
    }
}
