import AlfalfaKit
import XCTest

/// La alfalfa que crece con el contexto de la sesión.
final class AlfalfaPlantTests: XCTestCase {

    func testStageFollowsTheContextPercent() {
        let cases: [(Double, AlfalfaPlant.Stage)] = [
            (0, .sprout), (0.1, .sprout), (0.149, .sprout),
            (0.15, .seedling), (0.3, .seedling),
            (0.35, .young), (0.59, .young),
            (0.6, .grown), (0.84, .grown),
            (0.85, .flowering), (1, .flowering)
        ]
        for (growth, stage) in cases {
            XCTAssertEqual(AlfalfaPlant.stage(for: growth), stage, "\(growth)")
            XCTAssertEqual(AlfalfaPlant(growth: growth).stage, stage, "\(growth)")
        }
        // Valores raros no rompen nada.
        XCTAssertEqual(AlfalfaPlant.stage(for: -1), .sprout)
        XCTAssertEqual(AlfalfaPlant.stage(for: 7), .flowering)
        XCTAssertEqual(AlfalfaPlant.stage(for: .nan), .sprout)
    }

    func testGrowthMeasuresTheWayToCompaction() {
        // Con umbral: en flor justo antes de compactar, aunque la ventana no esté llena.
        XCTAssertEqual(AlfalfaPlant.growth(used: 80_000, limit: 200_000, threshold: 160_000), 0.5)
        XCTAssertEqual(AlfalfaPlant.stage(for: AlfalfaPlant.growth(used: 150_000, limit: 200_000, threshold: 160_000)),
                       .flowering)
        // Sin umbral, la parte usada de la ventana («X % usado»).
        XCTAssertEqual(AlfalfaPlant.growth(used: 50_000, limit: 200_000, threshold: nil), 0.25)
        XCTAssertEqual(AlfalfaPlant.growth(used: 50_000, limit: 200_000, threshold: 0), 0.25)
        // Sin medir todavía: un brote. Pasado el umbral, como mucho 1.
        XCTAssertEqual(AlfalfaPlant.growth(used: 0, limit: 0, threshold: nil), 0)
        XCTAssertEqual(AlfalfaPlant.growth(used: 300_000, limit: 200_000, threshold: 160_000), 1)
    }

    func testTimelineGrowsAndRegrowsAfterCompaction() throws {
        var timeline = Timeline()
        XCTAssertEqual(timeline.plantGrowth, 0)
        func context(_ tokens: Int, estimate: Int) throws -> AlfalfaMessage {
            let line = #"{"version":2,"tipo":"contexto","tokens":\#(tokens),"limite":200000,"#
                + #""estimado":\#(estimate),"umbral":160000}"#
            return try XCTUnwrap(AlfalfaMessage.decode(line: line))
        }
        timeline.apply(try context(40_000, estimate: 41_000))
        XCTAssertEqual(AlfalfaPlant.stage(for: timeline.plantGrowth), .seedling)
        // `context_estimado` cuenta aunque el total medido vaya por detrás.
        timeline.apply(try context(140_000, estimate: 150_000))
        XCTAssertEqual(timeline.plantGrowth, 150_000 / 160_000)
        XCTAssertEqual(AlfalfaPlant.stage(for: timeline.plantGrowth), .flowering)
        // La compactación deja el contexto en un resumen: vuelve a ser un brote.
        timeline.apply(try context(12_000, estimate: 12_000))
        XCTAssertEqual(AlfalfaPlant.stage(for: timeline.plantGrowth), .sprout)
    }

    func testEachStageAddsPartsInsteadOfScalingTheSameFigure() {
        let plants = AlfalfaPlant.Stage.allCases.map { AlfalfaPlant(growth: $0.start + 0.07) }
        for (smaller, bigger) in zip(plants, plants.dropFirst()) {
            XCTAssertGreaterThan(bigger.height, smaller.height, "más alta en \(bigger.stage)")
            XCTAssertGreaterThan(bigger.leafCount, smaller.leafCount, "más hojas en \(bigger.stage)")
        }
        let sprout = AlfalfaPlant(growth: 0), flowering = AlfalfaPlant(growth: 1)
        // El brote es tallo y cotiledones; la planta en flor ya no los tiene y lleva brotes y flores.
        XCTAssertEqual(sprout.cotyledons, 1)
        XCTAssertTrue(sprout.leaves.isEmpty && sprout.shoots.isEmpty && sprout.florets.isEmpty)
        XCTAssertEqual(flowering.cotyledons, 0)
        XCTAssertEqual(flowering.shoots.count, 2)
        XCTAssertEqual(flowering.florets.count, 5)
        XCTAssertTrue(flowering.florets.allSatisfy { $0 == 1 })
        // Más del doble de alta pero con muchas más hojas: no es la misma figura aumentada.
        XCTAssertLessThan(flowering.height / sprout.height, 3)
        XCTAssertGreaterThanOrEqual(flowering.leafCount, 8)
        // Las hojas alternan de lado y quedan escalonadas a lo largo del tallo.
        let sides = flowering.leaves.map(\.side)
        XCTAssertEqual(sides, [1, -1, 1, -1, 1])
        let positions = flowering.leaves.map(\.position)
        XCTAssertEqual(positions, positions.sorted(), "las más viejas, más abajo")
        XCTAssertEqual(Set(positions).count, positions.count)
    }

    func testNewLeavesUnfoldGradually() {
        // Entre dos valores cercanos la hoja nueva va de pequeña a abierta, sin saltos.
        let birth = AlfalfaPlant(growth: 0.43).leaves.last!
        let later = AlfalfaPlant(growth: 0.5).leaves.last!
        XCTAssertEqual(birth.node, later.node)
        XCTAssertLessThan(birth.maturity, 0.1)
        XCTAssertGreaterThan(later.maturity, birth.maturity)
        var previous = 0.0
        for step in stride(from: 0.0, through: 1, by: 0.01) {
            let plant = AlfalfaPlant(growth: step)
            XCTAssertGreaterThanOrEqual(plant.height, previous)
            previous = plant.height
        }
    }

    // MARK: - Movimiento de la planta

    func testPlantSwaysOnlyWhileAnimatedAndWithoutReduceMotion() {
        for phase in [LiveActivity.Phase.waiting, .done] {
            XCTAssertEqual(LeafMotion.plantSway(phase, time: 0.7, reduceMotion: false), 0)
            XCTAssertEqual(LeafMotion.flutter(phase, node: 2, time: 0.7, reduceMotion: false), 0)
            XCTAssertEqual(LeafMotion.plantOpacity(phase, time: 0.7, reduceMotion: true), 1)
        }
        for phase in [LiveActivity.Phase.thinking, .working] {
            for time in stride(from: 0.0, to: 4, by: 0.3) {
                XCTAssertEqual(LeafMotion.plantSway(phase, time: time, reduceMotion: true), 0)
                XCTAssertEqual(LeafMotion.flutter(phase, node: 1, time: time, reduceMotion: true), 0)
                XCTAssertGreaterThanOrEqual(LeafMotion.plantOpacity(phase, time: time, reduceMotion: true), 0.6)
                XCTAssertEqual(LeafMotion.plantOpacity(phase, time: time, reduceMotion: false), 1)
            }
        }
        // Pensando se mece menos que trabajando, y nunca de más.
        let thinking = stride(from: 0.0, to: 7, by: 0.05).map { abs(LeafMotion.plantSway(.thinking, time: $0, reduceMotion: false)) }
        let working = stride(from: 0.0, to: 7, by: 0.05).map { abs(LeafMotion.plantSway(.working, time: $0, reduceMotion: false)) }
        XCTAssertLessThan(thinking.max()!, working.max()!)
        XCTAssertLessThanOrEqual(working.max()!, 6)
        XCTAssertGreaterThan(thinking.max()!, 1)
    }

    func testLeavesFlutterOutOfStep() {
        let leaves = (0..<4).map { LeafMotion.flutter(.working, node: $0, time: 0.3, reduceMotion: false) }
        XCTAssertEqual(Set(leaves).count, 4, "cada hoja en su momento")
        XCTAssertTrue(leaves.allSatisfy { abs($0) <= 8 })
    }
}
