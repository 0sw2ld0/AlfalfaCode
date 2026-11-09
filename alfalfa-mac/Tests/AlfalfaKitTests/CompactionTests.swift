@testable import AlfalfaKit
import XCTest

/// Compactar como en Claude Code: el pedido, la marca en la conversación y el panel de contexto.
final class CompactionTests: XCTestCase {
    private func decode(_ line: String) -> AlfalfaMessage {
        guard let message = AlfalfaMessage.decode(line: line) else {
            XCTFail("no se entendió: " + line)
            return .error("")
        }
        return message
    }

    func testCompactCommandCarriesInstructions() {
        XCTAssertEqual(AlfalfaCommand.compact().line, #"{"tipo":"compactar"}"# + "\n")
        XCTAssertEqual(AlfalfaCommand.compact(instructions: "Conserva las rutas").line,
                       #"{"instrucciones":"Conserva las rutas","tipo":"compactar"}"# + "\n")
    }

    func testWaitingLaunchOnlyForSavedSessionsWithoutMessage() {
        XCTAssertEqual(Launcher.stdioArguments(task: nil, resume: "abc", model: nil, effort: nil, readOnly: false, wait: true),
                       ["--stdio", "--reanudar", "abc", "--esperar"])
        XCTAssertFalse(Launcher.stdioArguments(task: "Hola", resume: "abc", model: nil, effort: nil, readOnly: false,
                                               wait: true).contains("--esperar"))
        XCTAssertFalse(Launcher.stdioArguments(task: nil, resume: nil, model: nil, effort: nil, readOnly: false,
                                               wait: true).contains("--esperar"))
    }

    func testCompactionEventsDriveTheMarker() {
        var timeline = Timeline()
        timeline.apply(decode(#"{"version":2,"tipo":"sesion","id":"s","cwd":"/tmp"}"#))
        timeline.apply(decode(#"{"version":2,"tipo":"evento","rol":"system","texto":"Compactando el contexto (manual)…","hora":1,"compactacion":{"estado":"en_curso","motivo":"manual"}}"#))
        XCTAssertTrue(timeline.compacting)
        XCTAssertTrue(timeline.items.isEmpty, "el «Compactando…» no es una fila de la conversación")
        timeline.apply(decode(#"{"version":2,"tipo":"evento","rol":"system","texto":"Contexto compactado (manual): unos 120000 → 9000 tokens","hora":2,"compactacion":{"estado":"hecha","motivo":"manual","antes":120000,"despues":9000,"resumen":"Falta el paso 3.","instrucciones":"Conserva las rutas"}}"#))
        XCTAssertFalse(timeline.compacting)
        guard case let .compaction(_, compaction)? = timeline.items.last else { return XCTFail("sin marca") }
        XCTAssertEqual(compaction, Compaction(state: .done, reason: "manual", before: 120000, after: 9000,
                                              summary: "Falta el paso 3.", instructions: "Conserva las rutas"))
        XCTAssertTrue(timeline.markdown(title: "t").contains("Falta el paso 3."))
        timeline.apply(decode(#"{"version":2,"tipo":"evento","rol":"system","texto":"No se compactó: No hay historia nueva que compactar.","hora":3,"compactacion":{"estado":"fallida","motivo":"manual"}}"#))
        guard case let .notice(_, text)? = timeline.items.last else { return XCTFail("sin aviso") }
        XCTAssertTrue(text.contains("No hay historia nueva"))
        // Un evento normal sigue siendo una acción.
        timeline.apply(decode(#"{"version":2,"tipo":"evento","rol":"system","texto":"Otra cosa","hora":4}"#))
        guard case .events? = timeline.items.last else { return XCTFail("evento normal") }
    }

    func testContextDetailsFeedThePanel() {
        var timeline = Timeline()
        let line = #"{"version":2,"tipo":"contexto","tokens":500000,"limite":1000000,"estimado":566500,"umbral":900000,"#
            + #""desglose":{"sistema":10600,"herramientas":18200,"mcp":23100,"mensajes":389900,"memoria":2000,"instrucciones_mcp":5200,"complementos":9000},"#
            + #""memoria":[{"ruta":"/p/AGENTS.md","tokens":2000},{"ruta":"","tokens":3}]}"#
        let message = decode(line)
        guard case let .context(_, _, _, breakdown, details) = message else { return XCTFail("contexto") }
        XCTAssertEqual(breakdown, ContextBreakdown(system: 10600, tools: 18200, mcp: 23100, messages: 389900,
                                                   memory: 2000, mcpInstructions: 5200, plugins: 9000))
        XCTAssertEqual(details, ContextDetails(estimate: 566500, threshold: 900000,
                                               memoryFiles: [ContextEntry(name: "/p/AGENTS.md", tokens: 2000)]))
        timeline.apply(message)
        XCTAssertEqual(timeline.contextUsed, 566500)
        XCTAssertEqual(timeline.contextSinceMeasure, 66500)
        XCTAssertEqual(timeline.tokensUntilCompaction, 333500)
        XCTAssertEqual(timeline.compactReserve, 100000)
        XCTAssertEqual(timeline.contextFraction, 0.5665, accuracy: 0.0001)
        XCTAssertEqual(timeline.compactionProgress ?? 0, 566500.0 / 900000, accuracy: 0.0001)
        // Sin detalles (motores anteriores) sigue valiendo lo medido.
        timeline.apply(decode(#"{"version":2,"tipo":"contexto","tokens":1000,"limite":8000}"#))
        XCTAssertEqual(timeline.contextTokens, 1000)
        XCTAssertNil(AlfalfaMessage.decode(line: #"{"version":2,"tipo":"contexto","tokens":1000,"limite":8000}"#)
            .flatMap { if case let .context(_, _, _, _, details) = $0 { return details } else { return nil } })
    }

    func testHistoryKeepsTheMarkerAndWaitingRestartStaysReady() {
        let line = #"{"version":2,"tipo":"historial","sesion":"s","eventos":["#
            + #"{"rol":"user","texto":"Hola","hora":1,"turno":"t1"},"#
            + #"{"rol":"system","texto":"Contexto compactado","hora":2,"compactacion":{"estado":"hecha","motivo":"auto","antes":10,"despues":5,"resumen":"R"}}"#
            + #"],"contexto":{"tokens":5,"limite":100,"umbral":90}}"#
        guard case let .history(_, events, wrapUp, context, tokens, _) = decode(line) else {
            return XCTFail("historial")
        }
        var timeline = Timeline(history: events, wrapUp: wrapUp, context: context, tokens: tokens)
        XCTAssertFalse(timeline.compacting)
        XCTAssertEqual(timeline.compactThreshold, 90)
        guard case .compaction? = timeline.items.last else { return XCTFail("marca") }
        timeline.restart(waiting: true)
        XCTAssertEqual(timeline.status, .idle)
        timeline.apply(decode(#"{"version":2,"tipo":"sesion","id":"s","cwd":"/tmp"}"#))
        XCTAssertEqual(timeline.status, .idle, "abrir sin turno no es trabajar")
    }
}

/// Salida real de `alfalfa --stdio --reanudar ID --esperar` con un modelo de Ollama Cloud:
/// compactar con instrucciones entre turnos y seguir con un mensaje.
final class RealCompactionTests: XCTestCase {
    func testRealRunPaintsTheMarkerAndTheNextTurn() throws {
        var timeline = Timeline(history: [], wrapUp: nil)
        timeline.restart(waiting: true)
        var sawCompacting = false
        for line in try fixtureLines("compactacion_real") {
            guard let message = AlfalfaMessage.decode(line: line) else { return XCTFail("no se entendió: " + line) }
            timeline.apply(message)
            sawCompacting = sawCompacting || timeline.compacting
        }
        XCTAssertTrue(sawCompacting, "se vio «Compactando…» mientras duraba")
        XCTAssertFalse(timeline.compacting)
        let markers = timeline.items.compactMap { item -> Compaction? in
            if case let .compaction(_, compaction) = item { return compaction } else { return nil }
        }
        XCTAssertEqual(markers.count, 1)
        XCTAssertEqual(markers.first?.reason, "manual")
        XCTAssertEqual(markers.first?.instructions, "Conserva la lista de la compra exacta.")
        XCTAssertTrue(markers.first?.summary.contains("heno para el cuy") ?? false)
        XCTAssertEqual(timeline.lastAgentText, "Las tres cosas eran: alfalfa, zanahorias y heno para el cuy.")
        XCTAssertEqual(timeline.compactThreshold, 943718)
        XCTAssertGreaterThan(timeline.contextSinceMeasure, 0)
        XCTAssertEqual(timeline.status, .completed)
    }
}
