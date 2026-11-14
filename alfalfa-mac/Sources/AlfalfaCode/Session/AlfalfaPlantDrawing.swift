import AlfalfaKit
import SwiftUI

/// Cuánto ha crecido la alfalfa de la sesión que se ve (0…1, `Timeline.plantGrowth`). Con nil,
/// el indicador dibuja la hoja suelta de siempre (los grupos ya terminados).
private struct AlfalfaGrowthKey: EnvironmentKey {
    static let defaultValue: Double? = nil
}

extension EnvironmentValues {
    var alfalfaGrowth: Double? {
        get { self[AlfalfaGrowthKey.self] }
        set { self[AlfalfaGrowthKey.self] = newValue }
    }
}

/// Dibuja una `AlfalfaPlant` en un Canvas: tallo que se curva con el vaivén, cotiledones, hojas
/// trifoliadas alternas que aletean desfasadas, brotes laterales y el racimo violeta.
///
/// Todo se mide en partes del lado del lienzo y el suelo queda abajo: la planta crecida llena
/// el cuadro y el brote ocupa el tercio de abajo.
struct AlfalfaPlantDrawing {

    // MARK: - Propiedades

    let plant: AlfalfaPlant
    let phase: LiveActivity.Phase
    let time: Double
    let reduceMotion: Bool
    let leaf: Color
    let flower: Color

    // MARK: - Métodos

    func draw(in graphics: inout GraphicsContext, size: CGSize) {
        let opacity = LeafMotion.plantOpacity(phase, time: time, reduceMotion: reduceMotion)
        guard opacity < 1 else { return drawParts(in: &graphics, size: size) }
        // Una sola capa: la planta se desvanece entera, sin costuras donde se solapan las partes.
        graphics.opacity *= opacity
        graphics.drawLayer { layer in
            layer.opacity = 1
            drawParts(in: &layer, size: size)
        }
    }

    // MARK: - Métodos privados

    private func drawParts(in graphics: inout GraphicsContext, size: CGSize) {
        let frame = min(size.width, size.height)
        let base = CGPoint(x: size.width / 2, y: size.height / 2 + frame * 0.46)
        // A 16–22 pt el brote ocuparía cuatro píxeles: se dibuja algo mayor mientras es pequeño,
        // y el aumento se va al crecer (la planta crecida ya llena el cuadro).
        let side = frame < 28 ? frame * (1 + 0.35 * (1 - plant.growth)) : frame
        let stem = Stem(plant: plant, sway: LeafMotion.plantSway(phase, time: time, reduceMotion: reduceMotion))
        func point(_ p: CGPoint) -> CGPoint { CGPoint(x: base.x + p.x * side, y: base.y - p.y * side) }
        let shading = GraphicsContext.Shading.color(leaf)
        let width = max(1, side * (0.04 + 0.02 * plant.growth))

        // El tallo, un poco más fino arriba: dos tramos con distinto grosor.
        var lower = Path(), upper = Path()
        lower.move(to: point(stem.at(0)))
        for step in 1...6 { lower.addLine(to: point(stem.at(Double(step) / 12))) }
        upper.move(to: point(stem.at(0.5)))
        for step in 7...12 { upper.addLine(to: point(stem.at(Double(step) / 12))) }
        graphics.stroke(lower, with: shading, style: StrokeStyle(lineWidth: width, lineCap: .round, lineJoin: .round))
        graphics.stroke(upper, with: shading, style: StrokeStyle(lineWidth: width * 0.8, lineCap: .round, lineJoin: .round))

        // Tallos de la corona: salen de la base, se abren hacia su lado y se curvan hacia arriba.
        // Se mecen con el principal, algo menos y un poco a destiempo.
        let lean = stem.angle(at: 1)
        for shoot in plant.shoots {
            let origin = stem.at(0.02)
            let angle = shoot.side * 30 + lean * 0.7
            let end = Self.move(origin, angle: angle - shoot.side * 14, by: shoot.length)
            let bend = Self.move(origin, angle: angle + shoot.side * 6, by: shoot.length * 0.5)
            var path = Path()
            path.move(to: point(origin))
            path.addQuadCurve(to: point(end), control: point(bend))
            graphics.stroke(path, with: shading, style: StrokeStyle(lineWidth: width * 0.75, lineCap: .round))
            let middle = Self.quad(origin, bend, end, at: 0.55)
            let flutter = LeafMotion.flutter(phase, node: shoot.node, time: time, reduceMotion: reduceMotion)
            trifoliate(in: &graphics, at: middle, angle: angle + shoot.side * 52 + flutter, petiole: 0.08,
                       length: 0.11, maturity: shoot.maturity, side: side, point: point)
            trifoliate(in: &graphics, at: end, angle: angle - shoot.side * 14 + flutter * 1.2, petiole: 0.02,
                       length: 0.11, maturity: shoot.maturity, side: side, point: point)
        }

        // Cotiledones: dos óvalos casi horizontales que se secan al salir las hojas de verdad.
        if plant.cotyledons > 0.01 {
            let at = min(1, plant.cotyledonHeight / plant.height)
            let origin = stem.at(at)
            let angle = stem.angle(at: at)
            var layer = graphics
            layer.opacity *= plant.cotyledons
            let scale = 0.55 + 0.45 * plant.cotyledons
            for turn in [-68.0, 68.0] {
                let flutter = LeafMotion.flutter(phase, node: 0, time: time, reduceMotion: reduceMotion) * 0.5
                let path = Self.ellipseLeaf(length: 0.15 * scale, width: 0.085 * scale)
                layer.fill(path.applying(transform(at: origin, angle: angle + turn + flutter, side: side, point: point)),
                           with: shading)
            }
        }

        // Hojas de los nudos, alternas.
        for item in plant.leaves {
            let origin = stem.at(item.position)
            let flutter = LeafMotion.flutter(phase, node: item.node, time: time, reduceMotion: reduceMotion)
            let angle = stem.angle(at: item.position) + item.side * (72 - 14 * item.maturity) + flutter
            trifoliate(in: &graphics, at: origin, angle: angle, petiole: 0.12, length: 0.12,
                       maturity: item.maturity, side: side, point: point)
        }

        // La hoja de la punta: es la que «respira» como el indicador de siempre. Al florecer se
        // aparta un poco para dejar ver el racimo.
        let tipAngle = stem.angle(at: 1)
        let flowering = plant.florets.first ?? 0
        let flutter = LeafMotion.flutter(phase, node: plant.leaves.count + 1, time: time, reduceMotion: reduceMotion)
        trifoliate(in: &graphics, at: stem.at(1), angle: tipAngle - 55 * flowering + flutter, petiole: 0.03,
                   length: 0.14, maturity: max(0.02, plant.tip), side: side, point: point, breathing: true)

        // El racimo: florecillas que se abren de abajo arriba, de verde a violeta.
        if !plant.florets.isEmpty {
            let origin = Self.move(stem.at(1), angle: tipAngle, by: -0.01)
            let slots: [(across: Double, along: Double)] = [(-0.05, 0.04), (0.05, 0.06), (-0.04, 0.105),
                                                            (0.045, 0.125), (0, 0.165)]
            for (index, open) in plant.florets.enumerated() {
                let slot = slots[index % slots.count]
                let center = Self.move(Self.move(origin, angle: tipAngle, by: slot.along),
                                       angle: tipAngle + 90, by: slot.across)
                let floret = Self.ellipseLeaf(length: 0.085 * (0.5 + 0.5 * open), width: 0.065 * (0.5 + 0.5 * open))
                let place = transform(at: Self.move(center, angle: tipAngle + 180, by: 0.03), angle: tipAngle, side: side,
                                      point: point)
                var bud = graphics
                bud.opacity *= 1 - open
                bud.fill(floret.applying(place), with: shading)
                var bloom = graphics
                bloom.opacity *= open
                bloom.fill(floret.applying(place), with: .color(flower))
            }
        }
    }

    /// Una hoja trifoliada: peciolo y tres foliolos. Recién asomada, los foliolos están juntos
    /// y pequeños (la alfalfa los pliega); al madurar se abren en abanico.
    private func trifoliate(in graphics: inout GraphicsContext, at origin: CGPoint, angle: Double, petiole: Double,
                            length: Double, maturity: Double, side: CGFloat, point: (CGPoint) -> CGPoint,
                            breathing: Bool = false) {
        let grown = 0.35 + 0.65 * maturity
        let end = Self.move(origin, angle: angle, by: petiole * grown)
        let shading = GraphicsContext.Shading.color(leaf)
        if petiole > 0 {
            var path = Path()
            path.move(to: point(origin))
            path.addLine(to: point(end))
            graphics.stroke(path, with: shading, style: StrokeStyle(lineWidth: max(0.7, side * 0.025), lineCap: .round))
        }
        let spread = 14 + 44 * maturity
        let leaflets: [(turn: Double, base: CGPoint)] = [
            (-spread, end), (0, Self.move(end, angle: angle, by: 0.02 * grown)), (spread, end)
        ]
        for (index, leaflet) in leaflets.enumerated() {
            let pose = breathing
                ? LeafMotion.pose(phase, leaflet: index, time: time, reduceMotion: reduceMotion)
                : .still
            var layer = graphics
            // Con «Reducir movimiento» la opacidad ya la lleva la planta entera.
            if !reduceMotion { layer.opacity *= pose.opacity }
            let scale = (reduceMotion ? 1 : pose.scale) * grown
            let path = Self.obovate(length: length * scale, width: length * 0.62 * scale)
            layer.fill(path.applying(transform(at: leaflet.base, angle: angle + leaflet.turn, side: side, point: point)),
                       with: shading)
        }
    }

    /// Lleva un dibujo hecho en partes del lienzo, apuntando hacia arriba desde (0, 0), a `origin`
    /// girado `angle` grados desde la vertical.
    private func transform(at origin: CGPoint, angle: Double, side: CGFloat,
                           point: (CGPoint) -> CGPoint) -> CGAffineTransform {
        let place = point(origin)
        return CGAffineTransform(translationX: place.x, y: place.y)
            .rotated(by: angle * .pi / 180)
            .scaledBy(x: side, y: side)
    }

    /// Avanza `distance` desde `origin` en la dirección `angle` (grados desde la vertical, positivo
    /// a la derecha), en el espacio de la planta (y hacia arriba).
    private static func move(_ origin: CGPoint, angle: Double, by distance: Double) -> CGPoint {
        let radians = angle * .pi / 180
        return CGPoint(x: origin.x + sin(radians) * distance, y: origin.y + cos(radians) * distance)
    }

    private static func quad(_ start: CGPoint, _ control: CGPoint, _ end: CGPoint, at t: Double) -> CGPoint {
        let u = 1 - t
        return CGPoint(x: u * u * start.x + 2 * u * t * control.x + t * t * end.x,
                       y: u * u * start.y + 2 * u * t * control.y + t * t * end.y)
    }

    /// Foliolo obovado (estrecho en la base, ancho cerca de la punta) que nace en (0, 0) y apunta
    /// hacia arriba en coordenadas de pantalla (y negativa).
    static func obovate(length: CGFloat, width: CGFloat) -> Path {
        let half = width / 2
        var path = Path()
        path.move(to: .zero)
        path.addCurve(to: CGPoint(x: half, y: -length * 0.66),
                      control1: CGPoint(x: half * 0.25, y: -length * 0.18),
                      control2: CGPoint(x: half, y: -length * 0.40))
        path.addCurve(to: CGPoint(x: 0, y: -length),
                      control1: CGPoint(x: half, y: -length * 0.90),
                      control2: CGPoint(x: half * 0.45, y: -length))
        path.addCurve(to: CGPoint(x: -half, y: -length * 0.66),
                      control1: CGPoint(x: -half * 0.45, y: -length),
                      control2: CGPoint(x: -half, y: -length * 0.90))
        path.addCurve(to: .zero,
                      control1: CGPoint(x: -half, y: -length * 0.40),
                      control2: CGPoint(x: -half * 0.25, y: -length * 0.18))
        path.closeSubpath()
        return path
    }

    /// Óvalo que nace en (0, 0) y apunta hacia arriba: cotiledones y florecillas.
    private static func ellipseLeaf(length: CGFloat, width: CGFloat) -> Path {
        Path(ellipseIn: CGRect(x: -width / 2, y: -length, width: width, height: length))
    }
}

/// El tallo: una curva cuadrática desde el suelo. La base queda casi vertical y la punta se
/// inclina con el vaivén, así que la planta se dobla en vez de girar entera como un palo.
private struct Stem {
    let start = CGPoint.zero
    let control: CGPoint
    let end: CGPoint

    init(plant: AlfalfaPlant, sway: Double) {
        // Una leve inclinación fija: las plantas no crecen a plomo.
        let lean = (2.5 + sway) * .pi / 180
        let height = plant.height
        end = CGPoint(x: sin(lean) * height, y: cos(lean) * height)
        control = CGPoint(x: sin(lean * 0.25) * height * 0.55, y: height * 0.55)
    }

    func at(_ t: Double) -> CGPoint {
        let u = 1 - t
        return CGPoint(x: u * u * start.x + 2 * u * t * control.x + t * t * end.x,
                       y: u * u * start.y + 2 * u * t * control.y + t * t * end.y)
    }

    /// Dirección del tallo en `t`, en grados desde la vertical.
    func angle(at t: Double) -> Double {
        let dx = 2 * (1 - t) * (control.x - start.x) + 2 * t * (end.x - control.x)
        let dy = 2 * (1 - t) * (control.y - start.y) + 2 * t * (end.y - control.y)
        return atan2(dx, dy) * 180 / .pi
    }
}
