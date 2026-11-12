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
        public static let keepAwake = "ajustes.mantenerDespierto"
        public static let sessionBudget = "ajustes.presupuestoSesion"
        public static let monthlyBudget = "ajustes.presupuestoMes"
        /// Topes propios de algunas sesiones, puestos desde su panel: id → dólares (0, sin tope).
        public static let sessionBudgets = "ajustes.presupuestoPorSesion"
        /// Ancho de la barra lateral izquierda, arrastrando su borde.
        public static let sidebarWidth = "barra.ancho"
        public static let diagnostics = "ajustes.diagnosticos"
        public static let swiftDiagnostics = "ajustes.diagnosticosSwift"
        public static let commitSignature = "ajustes.firmaCommits"
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

    /// Que el Mac no se duerma por inactividad mientras trabaja un agente. Encendido si nunca se tocó.
    public var keepAwake: Bool {
        get { defaults.object(forKey: Key.keepAwake) as? Bool ?? true }
        nonmutating set { defaults.set(newValue, forKey: Key.keepAwake) }
    }

    /// Tope en dólares de cada sesión (con sus subagentes); nil sin tope.
    public var sessionBudget: Double? {
        get { budget(Key.sessionBudget) }
        nonmutating set { storeBudget(newValue, Key.sessionBudget) }
    }

    /// Tope en dólares del mes en este Mac, todas las sesiones; nil sin tope.
    public var monthlyBudget: Double? {
        get { budget(Key.monthlyBudget) }
        nonmutating set { storeBudget(newValue, Key.monthlyBudget) }
    }

    /// Ancho de la barra lateral, siempre dentro de los límites; el de siempre si nunca se tocó.
    public var sidebarWidth: Double {
        get { SidebarWidth.clamp((defaults.object(forKey: Key.sidebarWidth) as? NSNumber)?.doubleValue ?? SidebarWidth.standard) }
        nonmutating set { defaults.set(SidebarWidth.clamp(newValue), forKey: Key.sidebarWidth) }
    }

    /// Ver los errores al editar: tras cada edición del agente, el servidor de lenguaje y el linter
    /// del proyecto miran los archivos tocados. Encendido si nunca se tocó.
    public var diagnostics: Bool {
        get { defaults.object(forKey: Key.diagnostics) as? Bool ?? true }
        nonmutating set { defaults.set(newValue, forKey: Key.diagnostics) }
    }

    /// Diagnósticos de Swift: sourcekit-lsp, el único que corre fuera del sandbox. Apagado si nunca se tocó,
    /// como el LSP de OpenCode; los .swift se comprueban con swift build o swift test.
    public var swiftDiagnostics: Bool {
        get { defaults.object(forKey: Key.swiftDiagnostics) as? Bool ?? false }
        nonmutating set { defaults.set(newValue, forKey: Key.swiftDiagnostics) }
    }

    /// Firmar los commits: el agente añade «Co-Authored-By: AlfalfaCode» a cada `git commit`.
    /// Encendido si nunca se tocó.
    public var commitSignature: Bool {
        get { defaults.object(forKey: Key.commitSignature) as? Bool ?? true }
        nonmutating set { defaults.set(newValue, forKey: Key.commitSignature) }
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

    /// El tope propio de una sesión, puesto desde su panel; nil si sigue el de Ajustes.
    public func sessionCap(for id: String?) -> SessionCap? {
        guard let id, let value = (defaults.dictionary(forKey: Key.sessionBudgets)?[id] as? NSNumber)?.doubleValue,
              value.isFinite, value >= 0 else { return nil }
        return value > 0 ? .amount(value) : .noCap
    }

    /// Pone (o, con nil, quita) el tope propio de una sesión. No toca el de Ajustes.
    public func setSessionCap(_ cap: SessionCap?, for id: String) {
        var all = defaults.dictionary(forKey: Key.sessionBudgets) ?? [:]
        switch cap {
        case .none: all[id] = nil
        case .noCap: all[id] = 0.0
        case let .amount(value) where value.isFinite && value > 0: all[id] = value
        case .amount: all[id] = 0.0
        }
        if all.isEmpty { defaults.removeObject(forKey: Key.sessionBudgets) } else { defaults.set(all, forKey: Key.sessionBudgets) }
    }

    /// El tope que vale para una sesión: el suyo si lo tiene; si no, el de Ajustes.
    public func sessionBudget(for id: String?) -> Double? {
        guard let cap = sessionCap(for: id) else { return sessionBudget }
        return cap.value
    }

    // MARK: - Métodos privados

    private static func clean(_ value: String?) -> String? {
        guard let value = value?.trimmingCharacters(in: .whitespacesAndNewlines), !value.isEmpty else { return nil }
        return value
    }

    private func text(_ key: String) -> String? {
        Self.clean(defaults.string(forKey: key))
    }

    private func budget(_ key: String) -> Double? {
        guard let value = (defaults.object(forKey: key) as? NSNumber)?.doubleValue, value.isFinite, value > 0 else {
            return nil
        }
        return value
    }

    private func storeBudget(_ value: Double?, _ key: String) {
        if let value, value.isFinite, value > 0 {
            defaults.set(value, forKey: key)
        } else {
            defaults.removeObject(forKey: key)
        }
    }

    private func store(_ value: String?, _ key: String) {
        if let value = Self.clean(value) {
            defaults.set(value, forKey: key)
        } else {
            defaults.removeObject(forKey: key)
        }
    }
}
