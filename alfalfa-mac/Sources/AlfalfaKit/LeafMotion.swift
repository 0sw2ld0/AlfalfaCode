import Foundation

/// Cómo se mueven los tres foliolos de la hoja de alfalfa del indicador de actividad.
///
/// La hoja es trifoliada: izquierdo (0), terminal (1) y derecho (2). Pensando, los foliolos
/// respiran despacio uno tras otro; trabajando, la ola es más rápida y marcada y la hoja se
/// mece. Esperando permiso y al terminar, quieta. Con «Reducir movimiento» no hay escala ni
/// giro: la hoja entera cambia de opacidad, suave.
public enum LeafMotion {

    public struct Pose: Hashable, Sendable {
        public static let still = Pose(opacity: 1, scale: 1)

        public var opacity: Double
        public var scale: Double
    }

    public static let thinkingPeriod = 1.8
    public static let workingPeriod = 0.95
    public static let swayPeriod = 1.9
    public static let reducedPeriod = 2.4

    /// Si la fase se anima (las quietas no necesitan reloj).
    public static func isAnimated(_ phase: LiveActivity.Phase) -> Bool {
        phase == .thinking || phase == .working
    }

    /// Opacidad y escala del foliolo `leaflet` (0…2) en el instante `time` (segundos).
    public static func pose(_ phase: LiveActivity.Phase, leaflet: Int, time: Double, reduceMotion: Bool) -> Pose {
        guard isAnimated(phase) else { return .still }
        if reduceMotion {
            let wave = 0.5 + 0.5 * cos(2 * .pi * time / reducedPeriod)
            return Pose(opacity: 0.6 + 0.4 * wave, scale: 1)
        }
        let period = phase == .working ? workingPeriod : thinkingPeriod
        var progress = (time / period - Double(leaflet) / 3).truncatingRemainder(dividingBy: 1)
        if progress < 0 { progress += 1 }
        let wave = 0.5 - 0.5 * cos(2 * .pi * progress)
        if phase == .working {
            let sharp = wave * wave
            return Pose(opacity: 0.45 + 0.55 * sharp, scale: 0.78 + 0.30 * sharp)
        }
        return Pose(opacity: 0.35 + 0.65 * wave, scale: 0.82 + 0.18 * wave)
    }

    // MARK: - La planta que crece con el contexto

    /// Vaivén de la planta entera, en grados (positivo a la derecha). Suman dos senos de periodos
    /// que no encajan, así que no se repite como un metrónomo. Pensando, suave y lento;
    /// trabajando, más marcado. Quieta en las demás fases y con «Reducir movimiento».
    public static func plantSway(_ phase: LiveActivity.Phase, time: Double, reduceMotion: Bool) -> Double {
        guard isAnimated(phase), !reduceMotion else { return 0 }
        if phase == .working {
            return 5.5 * (sin(2 * .pi * time / swayPeriod) + 0.3 * sin(2 * .pi * time / 0.83 + 0.7)) / 1.3
        }
        return 2.4 * (sin(2 * .pi * time / 3.4) + 0.35 * sin(2 * .pi * time / 1.45 + 1.1)) / 1.35
    }

    /// Aleteo de la hoja del nudo `node`, en grados. Cada nudo va un poco detrás del de abajo:
    /// la brisa sube por la planta.
    public static func flutter(_ phase: LiveActivity.Phase, node: Int, time: Double, reduceMotion: Bool) -> Double {
        guard isAnimated(phase), !reduceMotion else { return 0 }
        let lag = Double(node) * 0.17
        if phase == .working {
            return 8 * sin(2 * .pi * (time / 1.1 - lag))
        }
        return 4 * sin(2 * .pi * (time / 2.6 - lag))
    }

    /// Opacidad de la planta entera: 1 salvo con «Reducir movimiento», donde en vez de moverse
    /// se desvanece suave (como la hoja sola).
    public static func plantOpacity(_ phase: LiveActivity.Phase, time: Double, reduceMotion: Bool) -> Double {
        guard isAnimated(phase), reduceMotion else { return 1 }
        return pose(phase, leaflet: 0, time: time, reduceMotion: true).opacity
    }

    /// Giro de la hoja entera, en grados: sólo trabajando y sin «Reducir movimiento».
    public static func sway(_ phase: LiveActivity.Phase, time: Double, reduceMotion: Bool) -> Double {
        guard phase == .working, !reduceMotion else { return 0 }
        return 7 * sin(2 * .pi * time / swayPeriod)
    }
}
