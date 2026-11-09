@testable import AlfalfaKit
import XCTest

/// Los ajustes de la app de Mac y lo que pone el pie de la barra lateral.
final class PreferencesTests: XCTestCase {

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

    func testTheShownNameIsTheSavedOneOrTheMacAccount() {
        XCTAssertEqual(Preferences.shownName(saved: "Oswaldo", system: "Oswaldo Leon"), "Oswaldo")
        XCTAssertEqual(Preferences.shownName(saved: "  Os  ", system: "Oswaldo Leon"), "Os")
        XCTAssertEqual(Preferences.shownName(saved: nil, system: "Oswaldo Leon"), "Oswaldo Leon")
        XCTAssertEqual(Preferences.shownName(saved: "   ", system: "Oswaldo Leon"), "Oswaldo Leon")
        XCTAssertEqual(Preferences.shownName(saved: "", system: ""), Preferences.fallbackName)
    }

    func testTheInitialIsTheFirstLetterInCapitals() {
        XCTAssertEqual(Preferences.initial(of: "oswaldo"), "O")
        XCTAssertEqual(Preferences.initial(of: "  ñandú"), "Ñ")
        XCTAssertEqual(Preferences.initial(of: ""), "T")
    }

    func testAnEmptyValueRemovesTheSetting() {
        let preferences = Preferences(defaults: defaults)
        preferences.defaultModel = "openai/gpt-6-luna"
        XCTAssertEqual(preferences.defaultModel, "openai/gpt-6-luna")
        preferences.defaultModel = "  "
        XCTAssertNil(preferences.defaultModel)
        XCTAssertNil(defaults.object(forKey: Preferences.Key.defaultModel))
    }

    func testAnUnknownPermissionModeIsIgnored() {
        let preferences = Preferences(defaults: defaults)
        preferences.defaultMode = .plan
        XCTAssertEqual(preferences.defaultMode, .plan)
        defaults.set("modo-de-otra-version", forKey: Preferences.Key.defaultMode)
        XCTAssertNil(preferences.defaultMode)
    }

    func testTheDefaultFolderWinsWhileItExists() {
        let preferences = Preferences(defaults: defaults)
        let recents = ["/tmp/ultima", "/tmp/anterior"]
        XCTAssertEqual(preferences.startFolder(recents: recents) { _ in true }, "/tmp/ultima")
        preferences.defaultFolder = "/tmp/fija"
        XCTAssertEqual(preferences.startFolder(recents: recents) { _ in true }, "/tmp/fija")
        XCTAssertEqual(preferences.startFolder(recents: recents) { $0 != "/tmp/fija" }, "/tmp/ultima")
        XCTAssertNil(Preferences(defaults: defaults).startFolder(recents: []) { _ in false })
    }

    func testTheFooterProviderComesFromTheModelList() {
        let models = [ModelInfo(name: "gpt-6-luna", provider: "alfalfa")]
        XCTAssertEqual(ModelProvider.id(of: "gpt-6-luna", in: models), "alfalfa")
        XCTAssertEqual(ModelProvider.id(of: "openai/gpt-6", in: models), "openai")
        XCTAssertEqual(ModelProvider.id(of: "anthropic/claude-haiku-5-5", in: []), "anthropic")
        XCTAssertEqual(ModelProvider.id(of: "alfalfa/glm-5.3-flash", in: []), "alfalfa")
        XCTAssertEqual(ModelProvider.id(of: "deepseek-v4.1-flash:cloud", in: []), "ollama")
        XCTAssertEqual(ModelProvider.id(of: "hf.co/usuario/modelo", in: []), "ollama")
    }

    func testTheProviderNameUsesTheListedOneFirst() {
        let listed = [ProviderStatus(id: "ollama", name: "Ollama Cloud", configured: true)]
        XCTAssertEqual(ModelProvider.name(of: "ollama", listed: listed), "Ollama Cloud")
        XCTAssertEqual(ModelProvider.name(of: "openai", listed: listed), "OpenAI")
        XCTAssertEqual(ModelProvider.name(of: "otro"), "otro")
    }
}
