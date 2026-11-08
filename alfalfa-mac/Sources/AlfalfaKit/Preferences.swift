import Foundation

/// Los ajustes de la app de Mac: el nombre del pie, la carpeta y el modelo de los encargos nuevos.
/// Viven en UserDefaults; las pruebas pasan su propio dominio.
public struct Preferences {

    // MARK: - Tipos

    /// Claves de UserDefaults, también para `@AppStorage`.
    public enum Key {
        public static let displayName = "ajustes.nombre"
        public static let defaultFolder = "ajustes.carpeta"
        public static let defaultModel = "ajustes.modelo"
        public static let defaultEffort = "ajustes.esfuerzo"
        public static let defaultMode = "ajustes.permisos"
        public static let section = "ajustes.seccion"
    }

    // MARK: - Constantes

    /// Lo que pone el pie si ni los ajustes ni la cuenta del Mac tienen nombre.
    public static let fallbackName = "Tú"

    // MARK: - Propiedades

    public let defaults: UserDefaults

    /// La carpeta con la que se abre «Nuevo encargo»; sin ella, la última que se usó.
    public var defaultFolder: String? {
        get { text(Key.defaultFolder) }
        nonmutating set { store(newValue, Key.defaultFolder) }
    }

    public var defaultModel: String? {
        get { text(Key.defaultModel) }
        nonmutating set { store(newValue, Key.defaultModel) }
    }

    public var defaultEffort: String? {
        get { text(Key.defaultEffort) }
        nonmutating set { store(newValue, Key.defaultEffort) }
    }

    /// Sólo un modo que exista: uno guardado por una versión con otros modos no cuenta.
    public var defaultMode: PermissionMode? {
        get { text(Key.defaultMode).flatMap(PermissionMode.init(rawValue:)) }
        nonmutating set { store(newValue?.rawValue, Key.defaultMode) }
    }

    // MARK: - Inicializadores

    public init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
    }

    // MARK: - Métodos públicos

    /// El nombre del pie: el de los ajustes y, si está vacío, el de la cuenta del Mac.
    public static func shownName(saved: String?, system: String) -> String {
        clean(saved) ?? clean(system) ?? fallbackName
    }

    /// La letra del círculo del pie.
    public static func initial(of name: String) -> String {
        String((clean(name) ?? fallbackName).prefix(1)).uppercased()
    }

    /// La carpeta por defecto si todavía existe; si no, la última que se usó.
    public func startFolder(recents: [String],
                            exists: (String) -> Bool = { FileManager.default.fileExists(atPath: $0) }) -> String? {
        if let folder = defaultFolder, exists(folder) { return folder }
        return recents.first
    }

    // MARK: - Métodos privados

    private static func clean(_ value: String?) -> String? {
        guard let value = value?.trimmingCharacters(in: .whitespacesAndNewlines), !value.isEmpty else { return nil }
        return value
    }

    private func text(_ key: String) -> String? {
        Self.clean(defaults.string(forKey: key))
    }

    private func store(_ value: String?, _ key: String) {
        if let value = Self.clean(value) {
            defaults.set(value, forKey: key)
        } else {
            defaults.removeObject(forKey: key)
        }
    }
}
