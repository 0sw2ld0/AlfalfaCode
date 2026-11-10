import Combine
import CryptoKit
import Foundation
import WebKit

public enum BrowserProfileMode: String, CaseIterable, Sendable {
    case isolated = "aislado"
    case project = "proyecto"

    public var title: String {
        switch self {
        case .isolated: return "Aislado"
        case .project: return "Guardado por proyecto"
        }
    }
}

/// Preferencias de la persona, fuera del repositorio. Sólo crea almacenes de WebKit propios y con UUID.
@MainActor
public final class BrowserProfiles {

    // MARK: - Tipos

    private struct Profile: Codable {
        var identifier: UUID
        var saved: Bool
    }

    public enum Key {
        public static let profiles = "navegador.perfilesPorProyecto"
    }

    // MARK: - Constantes

    public static let shared = BrowserProfiles()

    // MARK: - Propiedades

    @Published public private(set) var clearingProjects: Set<String> = []

    // MARK: - Propiedades privadas

    private let defaults: UserDefaults
    private var stores: [UUID: WKWebsiteDataStore] = [:]

    private var profiles: [String: Profile] {
        get {
            guard let data = defaults.data(forKey: Key.profiles) else { return [:] }
            return (try? JSONDecoder().decode([String: Profile].self, from: data)) ?? [:]
        }
        set {
            if let data = try? JSONEncoder().encode(newValue) { defaults.set(data, forKey: Key.profiles) }
        }
    }

    // MARK: - Inicializadores

    public init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
    }

    // MARK: - Métodos públicos

    /// El proyecto es la carpeta de trabajo elegida, con sus enlaces resueltos; no el repositorio común de Git.
    public static func projectKey(for project: URL) -> String? {
        guard project.isFileURL else { return nil }
        let path = project.resolvingSymlinksInPath().standardizedFileURL.path
        return SHA256.hash(data: Data(path.utf8)).map { String(format: "%02x", $0) }.joined()
    }

    /// Consultar los ajustes no crea un perfil de WebKit ni abre una página.
    public func mode(forProject project: URL) -> BrowserProfileMode {
        guard let key = Self.projectKey(for: project), let profile = profile(for: key), profile.saved else {
            return .isolated
        }
        return .project
    }

    public func hasProfile(forProject project: URL) -> Bool {
        guard let key = Self.projectKey(for: project) else { return false }
        return profile(for: key) != nil
    }

    public func isClearing(_ project: URL) -> Bool {
        Self.projectKey(for: project).map { clearingProjects.contains($0) } ?? false
    }

    /// Cada apertura aislada recibe un almacén nuevo. Sólo el modo elegido por la persona comparte el guardado.
    public func dataStore(forProject project: URL) -> WKWebsiteDataStore {
        guard let key = Self.projectKey(for: project), !clearingProjects.contains(key),
              let profile = profile(for: key), profile.saved else { return .nonPersistent() }
        return store(for: profile.identifier)
    }

    public func setMode(_ mode: BrowserProfileMode, forProject project: URL) throws {
        guard let key = Self.projectKey(for: project) else { throw BrowserProfileError.invalidProject }
        guard !clearingProjects.contains(key) else { throw BrowserProfileError.clearing }
        var values = profiles
        if mode == .isolated && profile(for: key) == nil { return }
        var value = profile(for: key) ?? Profile(identifier: UUID(), saved: false)
        value.saved = mode == .project
        objectWillChange.send()
        values[key] = value
        profiles = values
        if mode == .isolated { stores.removeValue(forKey: value.identifier) }
    }

    /// La app cierra antes las vistas de este proyecto. Durante el borrado no se abren almacenes persistentes.
    /// No enumera perfiles, no usa el almacén por defecto y nunca borra datos de otro UUID.
    public func clearData(forProject project: URL) async throws {
        guard let key = Self.projectKey(for: project) else { throw BrowserProfileError.invalidProject }
        guard !clearingProjects.contains(key) else { throw BrowserProfileError.clearing }
        guard let profile = profile(for: key) else { return }
        clearingProjects.insert(key)
        defer { clearingProjects.remove(key) }
        let oldStore = store(for: profile.identifier)
        await withCheckedContinuation { continuation in
            oldStore.removeData(ofTypes: WKWebsiteDataStore.allWebsiteDataTypes(), modifiedSince: .distantPast) {
                continuation.resume()
            }
        }
        stores.removeValue(forKey: profile.identifier)
        // Un WKWebView antiguo no puede volver a llenar el perfil que se usará después del borrado.
        var values = profiles
        values[key] = Profile(identifier: UUID(), saved: profile.saved)
        profiles = values
    }

    // MARK: - Métodos privados

    private func profile(for key: String) -> Profile? {
        let values = profiles
        guard let value = values[key], value.identifier.uuidString != "00000000-0000-0000-0000-000000000000",
              !values.contains(where: { $0.key != key && $0.value.identifier == value.identifier }) else { return nil }
        return value
    }

    private func store(for identifier: UUID) -> WKWebsiteDataStore {
        if let existing = stores[identifier] { return existing }
        let created = WKWebsiteDataStore(forIdentifier: identifier)
        stores[identifier] = created
        return created
    }
}

// MARK: - ObservableObject

extension BrowserProfiles: ObservableObject {}

public enum BrowserProfileError: Error {
    case invalidProject
    case clearing
}

// MARK: - LocalizedError

extension BrowserProfileError: LocalizedError {
    public var errorDescription: String? {
        switch self {
        case .invalidProject: return "Elige una carpeta de proyecto para configurar su navegador."
        case .clearing: return "Espera a que termine el borrado de los datos del navegador de este proyecto."
        }
    }
}
