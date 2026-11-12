import Foundation

/// Cuándo la conversación sigue al final, como en Claude Code y Codex: sólo si ya estabas abajo
/// (o acabas de escribir). Si subiste a leer, no te mueve: aparece «Ir al final».
public enum AutoScroll {
    /// Margen para seguir contando como «abajo» aunque falten unos puntos.
    public static let margin: Double = 60

    public static func isAtBottom(offset: Double, visible: Double, content: Double) -> Bool {
        content <= visible || offset + visible >= content - margin
    }

    public static func follows(atBottom: Bool, userJustWrote: Bool) -> Bool {
        atBottom || userJustWrote
    }
}
