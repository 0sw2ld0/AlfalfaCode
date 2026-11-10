import Foundation

/// Selección explícita del segundo motor. Los perfiles de Chromium nunca proceden de Safari ni Chrome personales.
public struct BrowserEnginePreferences {

    // MARK: - Constantes

    public static let engineKey = "ajustes.navegador.motor"
    public static let profileKey = "ajustes.navegador.perfilCuyBrowser"

    // MARK: - Propiedades

    public let defaults: UserDefaults

    public var engine: String {
        defaults.string(forKey: Self.engineKey) == "cuybrowser" ? "cuybrowser" : "webkit"
    }

    public var profile: String {
        defaults.string(forKey: Self.profileKey) ?? "alfalfa"
    }

    // MARK: - Inicialización

    public init(defaults: UserDefaults = .standard) { self.defaults = defaults }
}

extension Launcher {
    public static func browserArguments(enabled: Bool, engine: String?, profile: String) -> [String] {
        if engine == "cuybrowser" {
            return ["--motor-navegador", "cuybrowser", "--perfil-cuybrowser", profile]
        }
        return enabled ? ["--navegador"] : []
    }
}
