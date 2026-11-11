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
        XCTAssertFalse(preferences.fromPath)
        defaults.set("cuybrowser", forKey: BrowserEnginePreferences.engineKey)
        defaults.set("proyecto-42", forKey: BrowserEnginePreferences.profileKey)
        XCTAssertEqual(preferences.engine, "cuybrowser")
        XCTAssertEqual(preferences.profile, "proyecto-42")
        defaults.set("../../Chrome", forKey: BrowserEnginePreferences.profileKey)
        XCTAssertEqual(preferences.profile, "../../Chrome", "El motor debe rechazarlo, sin cambiar a otro perfil")
        defaults.set("off", forKey: BrowserEnginePreferences.engineKey)
        defaults.set(true, forKey: BrowserEnginePreferences.pathKey)
        XCTAssertEqual(preferences.engine, "off")
        XCTAssertTrue(preferences.fromPath)
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

    func testPathSelectionPrecedesTaskAndOffOmitsBrowser() {
        let args = Launcher.stdioArguments(task: "hola", resume: nil, model: nil, effort: nil, readOnly: false,
                                           browser: true, browserEngine: "cuybrowser", cuyBrowserFromPath: true)
        XCTAssertEqual(Array(args.suffix(3)), ["--cuybrowser-desde-path", "--", "hola"])
        XCTAssertTrue(Launcher.browserArguments(enabled: true, engine: "off", profile: "propio").isEmpty)
        XCTAssertEqual(Launcher.browserArguments(enabled: true, engine: "webkit", profile: "propio", fromPath: false),
                       ["--navegador", "--cuybrowser-solo-incluido"])
    }

    func testChromiumPreparationResponse() throws {
        let line = #"{"tipo":"cuybrowser_preparacion","estado":"sin_chromium","origen":"incluido"}"#
        guard case let .cuyBrowserPreparation(result)? = AlfalfaMessage.decode(line: line) else {
            return XCTFail("Estado de CuyBrowser ausente")
        }
        XCTAssertEqual(result.status, .needsChromium)
        XCTAssertEqual(result.source, "incluido")
        XCTAssertNil(result.browser)
    }
}
