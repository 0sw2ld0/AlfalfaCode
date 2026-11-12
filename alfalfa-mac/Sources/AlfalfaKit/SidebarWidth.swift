import Foundation

/// Ancho de la barra lateral izquierda: se cambia arrastrando su borde, como en Claude Code,
/// sin bajar de lo que necesitan los títulos ni comerse la conversación.
public enum SidebarWidth {
    public static let standard: Double = 260
    public static let minimum: Double = 200
    public static let maximum: Double = 420

    /// Dentro de los límites; lo que no es un número vuelve al ancho de siempre.
    public static func clamp(_ value: Double) -> Double {
        guard value.isFinite else { return standard }
        return min(maximum, max(minimum, value.rounded()))
    }

    /// El ancho mientras se arrastra: el de partida más lo arrastrado.
    public static func dragged(from start: Double, by translation: Double) -> Double {
        clamp(start + translation)
    }
}
