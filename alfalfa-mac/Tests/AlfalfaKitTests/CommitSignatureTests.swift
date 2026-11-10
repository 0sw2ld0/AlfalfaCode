@testable import AlfalfaKit
import XCTest

/// Firmar los commits: el interruptor de Ajustes ▸ General y cómo llega al motor.
final class CommitSignatureTests: XCTestCase {

    // MARK: - Métodos públicos

    func testTheSwitchCommand() {
        XCTAssertEqual(AlfalfaCommand.commitSignature(false).line, #"{"activo":false,"tipo":"firma"}"# + "\n")
        XCTAssertEqual(AlfalfaCommand.commitSignature(true).line, #"{"activo":true,"tipo":"firma"}"# + "\n")
    }

    func testTheLauncherPassesTheSwitchOnlyWhenOff() {
        XCTAssertEqual(Launcher.stdioArguments(task: "x", resume: nil, model: nil, effort: nil, readOnly: false),
                       ["--stdio", "--", "x"])
        XCTAssertEqual(Launcher.stdioArguments(task: "x", resume: nil, model: nil, effort: nil, readOnly: false,
                                               commitSignature: false),
                       ["--stdio", "--sin-firma", "--", "x"])
        XCTAssertEqual(Launcher.stdioArguments(task: "x", resume: nil, model: nil, effort: nil, readOnly: false,
                                               diagnostics: false, commitSignature: false),
                       ["--stdio", "--sin-diagnosticos", "--sin-firma", "--", "x"])
    }

    func testTheSettingIsOnUntilTurnedOff() throws {
        let suite = "alfalfa.pruebas.\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: suite))
        defer { defaults.removePersistentDomain(forName: suite) }
        let preferences = Preferences(defaults: defaults)
        XCTAssertTrue(preferences.commitSignature)
        preferences.commitSignature = false
        XCTAssertFalse(Preferences(defaults: defaults).commitSignature)
    }
}
