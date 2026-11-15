import Foundation

/// «Detener» de una sesión, aparte de la vista para poder probarlo.
///
/// Antes `stopping` se ponía a true y no volvía nunca a false: tras el primer Detener el botón quedaba
/// gris y `/detener` no hacía nada, aunque la sesión arrancara motores nuevos. Y el plazo de 8 s
/// terminaba el proceso que hubiera en ese momento, que podía ser ya el motor siguiente.
public final class EngineStop {
    public private(set) var stopping = false
    private weak var target: AlfalfaProcess?

    public init() {}

    /// true si hay que mandar «cancelar» ahora; false si ya hay un Detener en curso para ese motor.
    public func begin(_ process: AlfalfaProcess) -> Bool {
        if stopping, target === process { return false }
        stopping = true
        target = process
        return true
    }

    /// El turno terminó, el motor se cerró o arrancó otro: Detener vuelve a estar disponible.
    public func reset() {
        stopping = false
        target = nil
    }

    /// Pasado el plazo, ¿se termina `current`? Sólo si es el motor que se detuvo y sigue sin parar.
    public func shouldTerminate(_ current: AlfalfaProcess?) -> Bool {
        guard stopping, let current, let target else { return false }
        return current === target
    }
}
