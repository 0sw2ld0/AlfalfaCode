@testable import AlfalfaKit
import Combine
import XCTest

@MainActor
final class BrowserHandoffTests: XCTestCase {
    func testHandoffWaitsAndRejectsAgentActions() async throws {
        let engine = BrowserEngine()
        defer { engine.close() }
        let task = Task {
            await engine.perform(BrowserRequest(id: "relevo", action: "relevo",
                                                fields: ["motivo": "Inicia sesión."]))
        }
        try await waitForHandoff(engine)
        XCTAssertTrue(engine.isPanelOpen)
        XCTAssertEqual(engine.handoff?.reason, "Inicia sesión.")
        let blocked = await engine.perform(BrowserRequest(id: "otro", action: "pulsar", fields: ["ref": "e1"]))
        XCTAssertTrue(blocked.error?.contains("esperando a la persona") ?? false)
        engine.continueHandoff()
        let reply = await task.value
        XCTAssertTrue(reply.ok)
        XCTAssertEqual(reply.result["continuado"] as? Bool, true)
        XCTAssertNil(engine.handoff)
    }

    func testContinueChecksTheNewURLAndTitle() async throws {
        let engine = BrowserEngine()
        defer { engine.close() }
        let task = Task {
            await engine.perform(BrowserRequest(id: "relevo", action: "relevo",
                                                fields: ["motivo": "Entra a tu cuenta.", "url_contiene": "#cuenta"]))
        }
        try await waitForHandoff(engine)
        XCTAssertFalse(engine.canContinueHandoff)
        engine.continueHandoff()
        XCTAssertNotNil(engine.handoff)
        engine.webView.loadHTMLString("<title>Cuenta nueva</title><h1>Dentro</h1>",
                                      baseURL: URL(string: "https://prueba.alfalfa.test/#cuenta"))
        for _ in 0..<100 where engine.webView.title != "Cuenta nueva" {
            try await Task.sleep(nanoseconds: 20_000_000)
        }
        XCTAssertTrue(engine.canContinueHandoff)
        engine.continueHandoff()
        let reply = await task.value
        XCTAssertEqual(reply.result["titulo"] as? String, "Cuenta nueva")
        XCTAssertTrue((reply.result["url"] as? String)?.contains("#cuenta") ?? false)
    }

    func testCancelTimeoutAndCloseReleaseTheHandoff() async throws {
        for kind in ["cancelar", "caducar", "cerrar"] {
            let engine = BrowserEngine()
            let task = Task {
                await engine.perform(BrowserRequest(id: "relevo", action: "relevo",
                                                    fields: ["motivo": "Haz tu parte.",
                                                             "tiempo": kind == "caducar" ? 0.05 : 10]))
            }
            try await waitForHandoff(engine)
            if kind == "cancelar" {
                _ = await engine.perform(BrowserRequest(id: "antiguo", action: "cancelar_relevo"))
                XCTAssertNotNil(engine.handoff, "Una cancelación vieja no afecta al relevo nuevo")
                _ = await engine.perform(BrowserRequest(id: "relevo", action: "cancelar_relevo"))
            } else if kind == "cerrar" { engine.close() }
            let reply = await task.value
            XCTAssertFalse(reply.ok)
            XCTAssertNil(engine.handoff)
            engine.close()
        }
    }

    func testCancelOfAQueuedHandoffDoesNotAppearLater() async throws {
        let engine = BrowserEngine()
        defer { engine.close() }
        let script = "await new Promise(r => setTimeout(r, 300)); return 1"
        let previous = Task {
            await engine.perform(BrowserRequest(id: "anterior", action: "js", fields: ["codigo": script]))
        }
        try await Task.sleep(nanoseconds: 30_000_000)
        let handoff = Task {
            await engine.perform(BrowserRequest(id: "relevo", action: "relevo", fields: ["motivo": "Espera."]))
        }
        for _ in 0..<100 where engine.queuedHandoffs["relevo"] == nil {
            try await Task.sleep(nanoseconds: 5_000_000)
        }
        _ = await engine.perform(BrowserRequest(id: "relevo", action: "cancelar_relevo"))
        _ = await previous.value
        let reply = await handoff.value
        XCTAssertFalse(reply.ok)
        XCTAssertTrue(reply.error?.contains("antes de que empezara") ?? false)
        XCTAssertNil(engine.handoff)
        XCTAssertTrue(engine.queuedHandoffs.isEmpty)
    }

    func testCancelDuringPreparationDoesNotShowTheHandoff() async throws {
        let engine = BrowserEngine()
        defer { engine.close() }
        var appeared = false
        let appearance = engine.$handoff.sink { if $0 != nil { appeared = true } }
        // activity se publica antes de preparar reglas; la cancelación real llega sin otra tarea ni temporizador.
        let cancellation = engine.$activity.sink { [weak engine] activity in
            guard activity != nil else { return }
            XCTAssertNil(engine?.handoff)
            engine?.cancelHandoff(id: "relevo")
        }
        defer { appearance.cancel(); cancellation.cancel() }
        let reply = await engine.perform(BrowserRequest(id: "relevo", action: "relevo",
                                                        fields: ["motivo": "Espera.", "tiempo": 0.05]))
        XCTAssertFalse(reply.ok)
        XCTAssertTrue(reply.error?.contains("antes de que empezara") ?? false, reply.json)
        XCTAssertFalse(appeared)
        XCTAssertNil(engine.handoff)
        XCTAssertTrue(engine.queuedHandoffs.isEmpty)
    }

    func testCloseDuringPreparationDoesNotShowTheHandoff() async throws {
        let engine = BrowserEngine()
        var appeared = false
        let appearance = engine.$handoff.sink { if $0 != nil { appeared = true } }
        let closing = engine.$activity.sink { [weak engine] activity in
            guard activity != nil else { return }
            engine?.close()
        }
        defer { appearance.cancel(); closing.cancel() }
        let reply = await engine.perform(BrowserRequest(id: "relevo", action: "relevo",
                                                        fields: ["motivo": "Espera.", "tiempo": 0.05]))
        XCTAssertFalse(reply.ok)
        XCTAssertEqual(reply.error, "El navegador de la sesión se cerró.")
        XCTAssertTrue(engine.isClosed)
        XCTAssertFalse(appeared)
        XCTAssertNil(engine.handoff)
        XCTAssertTrue(engine.queuedHandoffs.isEmpty)
    }

    private func waitForHandoff(_ engine: BrowserEngine) async throws {
        for _ in 0..<100 where engine.handoff == nil { try await Task.sleep(nanoseconds: 20_000_000) }
        XCTAssertNotNil(engine.handoff)
    }
}
