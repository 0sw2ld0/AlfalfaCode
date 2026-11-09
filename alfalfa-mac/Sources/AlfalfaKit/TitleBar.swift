import Foundation

/// Qué hace el doble clic en la barra de título, según Ajustes del Sistema →
/// Escritorio y Dock → «Hacer doble clic en la barra de título de una ventana para».
public enum TitleBarDoubleClick: Equatable, Sendable {
    case zoom
    case minimize
    case nothing

    /// `AppleActionOnDoubleClick` del dominio global; sin valor, como el sistema: ampliar.
    public static func action(globalDefaults: [String: Any]) -> TitleBarDoubleClick {
        switch globalDefaults["AppleActionOnDoubleClick"] as? String {
        case "Minimize": return .minimize
        case "None": return .nothing
        case "Maximize", "Fill": return .zoom
        default:
            return (globalDefaults["AppleMiniaturizeOnDoubleClick"] as? Bool) == true ? .minimize : .zoom
        }
    }

    public static var current: TitleBarDoubleClick {
        action(globalDefaults: UserDefaults.standard.persistentDomain(forName: UserDefaults.globalDomain) ?? [:])
    }
}
