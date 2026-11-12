import Foundation

/// Selección explícita del segundo motor. Los perfiles de Chromium nunca proceden de Safari ni Chrome personales.
public struct BrowserEnginePreferences {

    // MARK: - Constantes

    public static let engineKey = "ajustes.navegador.motor"
    public static let profileKey = "ajustes.navegador.perfilCuyBrowser"
    public static let pathKey = "ajustes.navegador.cuyBrowserDesdePath"

    // MARK: - Propiedades

    public let defaults: UserDefaults

    public var engine: String {
        guard let chosen = defaults.string(forKey: Self.engineKey), ["cuybrowser", "off"].contains(chosen) else {
            return "webkit"
        }
        return chosen
    }

    public var profile: String {
        defaults.string(forKey: Self.profileKey) ?? "alfalfa"
    }

    public var fromPath: Bool { defaults.bool(forKey: Self.pathKey) }

    // MARK: - Inicialización

    public init(defaults: UserDefaults = .standard) { self.defaults = defaults }
}

extension Launcher {
    public static func browserArguments(enabled: Bool, engine: String?, profile: String,
                                        fromPath: Bool? = nil) -> [String] {
        if engine == "off" { return [] }
        let source = fromPath.map { [$0 ? "--cuybrowser-desde-path" : "--cuybrowser-solo-incluido"] } ?? []
        if engine == "cuybrowser" {
            return ["--motor-navegador", "cuybrowser", "--perfil-cuybrowser", profile] + source
        }
        return enabled ? ["--navegador"] + source : []
    }
}
