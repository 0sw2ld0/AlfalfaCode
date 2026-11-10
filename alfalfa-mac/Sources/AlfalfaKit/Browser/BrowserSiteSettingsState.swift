import Foundation

/// Reglas guardadas por el motor y su resultado tras las restricciones del proyecto.
public struct BrowserSiteSettingsState: Equatable, Sendable {
    public let project: String
    public let allowed: [String]
    public let denied: [String]
    public let strict: Bool
    public let effectiveAllowed: [String]
    public let effectiveDenied: [String]
    public let projectLimit: [String]?
    public let effectiveStrict: Bool

    init?(_ object: [String: Any]) {
        guard let project = object["proyecto"] as? String,
              let saved = object["configuracion"] as? [String: Any],
              let policy = object["politica"] as? [String: Any],
              let allowed = saved["permitidos"] as? [String], let denied = saved["bloqueados"] as? [String],
              let strict = saved["estricto"] as? Bool,
              let effectiveAllowed = policy["permitidos"] as? [String],
              let effectiveDenied = policy["bloqueados"] as? [String],
              let effectiveStrict = policy["estricto"] as? Bool else { return nil }
        self.project = project
        self.allowed = allowed
        self.denied = denied
        self.strict = strict
        self.effectiveAllowed = effectiveAllowed
        self.effectiveDenied = effectiveDenied
        projectLimit = policy["limite_proyecto"] as? [String]
        self.effectiveStrict = effectiveStrict
    }
}
