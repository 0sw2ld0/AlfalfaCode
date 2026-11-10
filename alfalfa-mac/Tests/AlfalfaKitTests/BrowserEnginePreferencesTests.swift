@testable import AlfalfaKit
import XCTest

final class BrowserEnginePreferencesTests: XCTestCase {

    // MARK: - Métodos públicos

    func testDefaultsAndOwnProfileSelection() throws {
        let suite = "alfalfa.pruebas.cuybrowser.\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: suite))
        defer { defaults.removePersistentDomain(forName: suite) }
        let preferences = BrowserEnginePreferences(defaults: defaults)
        XCTAssertEqual(preferences.engine, "webkit")
        XCTAssertEqual(preferences.profile, "alfalfa")
        defaults.set("cuybrowser", forKey: BrowserEnginePreferences.engineKey)
        defaults.set("proyecto-42", forKey: BrowserEnginePreferences.profileKey)
        XCTAssertEqual(preferences.engine, "cuybrowser")
        XCTAssertEqual(preferences.profile, "proyecto-42")
        defaults.set("../../Chrome", forKey: BrowserEnginePreferences.profileKey)
        XCTAssertEqual(preferences.profile, "../../Chrome", "El motor debe rechazarlo, sin cambiar a otro perfil")
    }

    func testCuyBrowserRoutingDoesNotOfferWebKitTransport() {
        let args = Launcher.stdioArguments(task: "hola", resume: nil, model: nil, effort: nil, readOnly: false,
                                           browser: true, browserEngine: "cuybrowser", cuyBrowserProfile: "propio")
        XCTAssertEqual(args,
                       ["--stdio", "--motor-navegador", "cuybrowser", "--perfil-cuybrowser", "propio", "--", "hola"])
        let webkit = Launcher.stdioArguments(task: nil, resume: nil, model: nil, effort: nil, readOnly: false,
                                             browser: true, browserEngine: "webkit")
        XCTAssertEqual(webkit, ["--stdio", "--navegador"])
    }
}
