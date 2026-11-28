import Foundation

/// La planta de alfalfa que crece con el contexto de la sesión.
///
/// Con la sesión recién empezada es un brote: un tallo corto con sus dos cotiledones. Al llenarse
/// el contexto sube el tallo, salen hojas trifoliadas alternas, luego brotes laterales y, cerca de
/// la compactación automática, el racimo de flores violeta. Tras compactar el contexto baja y la
/// planta vuelve a ser pequeña, como la alfalfa segada que rebrota.
///
/// No es la misma figura escalada: las hojas tienen siempre el mismo tamaño y lo que cambia es
/// cuántas hay, la altura del tallo y la madurez. Todo sale de un crecimiento continuo (0…1), así
/// que al animar de un valor a otro las hojas nuevas se despliegan poco a poco.
public struct AlfalfaPlant: Equatable, Sendable {

    // MARK: - Etapas

    public enum Stage: Int, CaseIterable, Comparable, Sendable {
        case sprout, seedling, young, grown, flowering

        public static func < (lhs: Stage, rhs: Stage) -> Bool { lhs.rawValue < rhs.rawValue }

        /// Desde qué crecimiento empieza la etapa.
        public var start: Double {
            switch self {
            case .sprout: return 0
            case .seedling: return 0.15
            case .young: return 0.35
            case .grown: return 0.6
            case .flowering: return 0.85
            }
        }

        /// Para VoiceOver y la ayuda: «Alfalfa en plántula».
        public var label: String {
            switch self {
            case .sprout: return "brote"
            case .seedling: return "plántula"
            case .young: return "planta joven"
            case .grown: return "planta crecida"
            case .flowering: return "en flor"
            }
        }
    }

    /// La etapa de un crecimiento (0…1).
    public static func stage(for growth: Double) -> Stage {
        let value = clamp(growth)
        return Stage.allCases.last { value >= $0.start } ?? .sprout
    }

    /// Cuánto ha crecido la planta: la parte del camino hasta la compactación automática o, sin
    /// umbral, la parte usada de la ventana. En flor justo antes de compactar.
    public static func growth(used: Int, limit: Int, threshold: Int?) -> Double {
        let full = threshold.flatMap { $0 > 0 ? $0 : nil } ?? limit
        guard full > 0 else { return 0 }
        return clamp(Double(used) / Double(full))
    }

    // MARK: - Partes

    /// Una hoja trifoliada en un nudo del tallo.
    public struct Leaf: Equatable, Sendable {
        /// Dónde nace, como parte del tallo (0 en el suelo, 1 en la punta).
        public var position: Double
        /// -1 a la izquierda, 1 a la derecha.
        public var side: Double
        /// 0 recién asomada (foliolos cerrados y pequeños), 1 abierta del todo.
        public var maturity: Double
        /// Número del nudo, para desfasar el aleteo de cada hoja.
        public var node: Int
    }

    /// Un tallo nuevo que sale de la corona (la base), como en la alfalfa ya establecida: se abre
    /// hacia un lado y lleva una hoja a medio camino y otra en la punta.
    public struct Shoot: Equatable, Sendable {
        /// -1 a la izquierda, 1 a la derecha.
        public var side: Double
        /// Largo del tallo, en partes del lienzo.
        public var length: Double
        public var maturity: Double
        /// Para desfasar su vaivén y el aleteo de sus hojas.
        public var node: Int
    }

    public let growth: Double
    public let stage: Stage
    /// Altura del tallo, en partes del lienzo (el lado del cuadrado es 1).
    public let height: Double
    /// Los dos cotiledones del brote: 1 abiertos, 0 ya secos.
    public let cotyledons: Double
    /// Altura fija de los cotiledones sobre el suelo, en partes del lienzo.
    public let cotyledonHeight: Double
    /// La hoja de la punta, la más nueva.
    public let tip: Double
    public let leaves: [Leaf]
    public let shoots: [Shoot]
    /// Cada florecilla del racimo de la punta (0 capullo que asoma, 1 abierta).
    public let florets: [Double]

    public init(growth: Double) {
        let value = Self.clamp(growth)
        self.growth = value
        stage = Self.stage(for: value)
        height = Self.height(for: value)
        cotyledonHeight = Self.height(for: 0) * 0.78
        cotyledons = 1 - Self.ramp(value, from: 0.22, to: 0.46)
        tip = Self.ramp(value, from: 0.03, to: 0.2)
        var leaves: [Leaf] = []
        for (node, birth) in Self.leafBirths.enumerated() where value > birth {
            // Nace junto a la punta y, al alargarse el tallo por encima, queda más abajo.
            let age = min(1, (value - birth) / 0.88)
            leaves.append(Leaf(position: 0.8 - 0.62 * age, side: node.isMultiple(of: 2) ? 1 : -1,
                               maturity: Self.ramp(value, from: birth, to: birth + 0.13), node: node))
        }
        self.leaves = leaves
        shoots = Self.shootBirths.enumerated().compactMap { index, shoot in
            guard value > shoot.birth else { return nil }
            let maturity = Self.ramp(value, from: shoot.birth, to: shoot.birth + 0.22)
            return Shoot(side: shoot.side, length: 0.5 * (0.25 + 0.75 * maturity), maturity: maturity,
                         node: 6 + index)
        }
        florets = (0..<Self.floretCount).map { index in
            let birth = Self.floweringStart + Double(index) * 0.022
            return Self.ramp(value, from: birth, to: birth + 0.05)
        }.filter { $0 > 0 }
    }

    /// Cuántas hojas hay contando la de la punta y las de los tallos nuevos (dos en cada uno).
    public var leafCount: Int {
        (tip > 0 ? 1 : 0) + leaves.count + 2 * shoots.count
    }

    // MARK: - Privado

    /// Crecimiento al que asoma la hoja de cada nudo, de abajo arriba.
    static let leafBirths = [0.12, 0.27, 0.42, 0.56, 0.7]
    /// Tallos de la corona: lado y crecimiento al que asoman.
    static let shootBirths: [(side: Double, birth: Double)] = [(-1, 0.6), (1, 0.74)]
    static let floweringStart = Stage.flowering.start
    static let floretCount = 5

    static func height(for growth: Double) -> Double {
        0.3 + 0.44 * pow(growth, 0.85)
    }

    static func ramp(_ value: Double, from start: Double, to end: Double) -> Double {
        let t = clamp((value - start) / (end - start))
        return t * t * (3 - 2 * t)
    }

    static func clamp(_ value: Double) -> Double {
        value.isFinite ? min(1, max(0, value)) : 0
    }
}
