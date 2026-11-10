@testable import AlfalfaKit
import AppKit
import WebKit
import XCTest

/// Cruces entre lectura, anotaciones, identidad del documento y relevo con las herramientas nuevas.
@MainActor
final class BrowserIntegrationTests: XCTestCase {

    func testAnnotationReferenceAndDocumentIdentitySurviveAnotherRead() async throws {
        let engine = try await page()
        defer { engine.close() }
        let first = await perform(engine, "leer")
        XCTAssertTrue(first.ok, first.json)
        let before = await perform(engine, "estado")
        let document = try XCTUnwrap(before.result["documento"] as? String)
        XCTAssertFalse(document.isEmpty)
        try await engine.selectAnnotation(in: CGRect(x: 50, y: 50, width: 0, height: 0))
        let annotation = try XCTUnwrap(engine.annotation)
        XCTAssertEqual(annotation.selector, "#objetivo")

        // Un elemento nuevo delante cambia el orden de lectura: reiniciar las refs señalaría otro botón.
        _ = try await engine.webView.evaluateJavaScript("""
        const nuevo = document.createElement('button');
        nuevo.textContent = 'Nuevo'; document.body.prepend(nuevo);
        """)
        let reread = await perform(engine, "leer")
        XCTAssertTrue(reread.ok, reread.json)
        let tree = try XCTUnwrap(reread.result["arbol"] as? String)
        let target = tree.split(separator: "\n").first { $0.contains("button «Objetivo»") }
        XCTAssertTrue(target?.contains("[\(annotation.ref)]") == true, tree)
        let after = await perform(engine, "estado")
        XCTAssertEqual(after.result["documento"] as? String, document)
        let clicked = await perform(engine, "pulsar", ["ref": annotation.ref])
        XCTAssertTrue(clicked.ok, clicked.json)
        let effects = try await engine.webView.evaluateJavaScript("window.effects") as? Int
        XCTAssertEqual(effects, 1, "La referencia debe seguir apuntando al botón anotado.")

        engine.webView.loadHTMLString("<title>Documento nuevo</title><p>Otra página</p>",
                                      baseURL: URL(string: "https://prueba.alfalfa.test/integracion"))
        try await loaded(engine, title: "Documento nuevo")
        let replacement = await perform(engine, "estado")
        XCTAssertNotEqual(replacement.result["documento"] as? String, document)
        let stale = await perform(engine, "pulsar", ["ref": annotation.ref])
        XCTAssertFalse(stale.ok, "La ref anterior no autoriza un elemento del documento nuevo.")
    }

    func testHandoffCancelsSelectionAndBlocksEveryNewAgentAction() async throws {
        let engine = try await page()
        defer { engine.close() }
        let document = await perform(engine, "estado")
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("integracion-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let file = root.appendingPathComponent("demo.txt")
        try Data("Sólo datos de prueba".utf8).write(to: file)
        engine.isSelectingAnnotation = true
        let handoff = Task {
            await self.perform(engine, "relevo", ["motivo": "Haz tu parte en la página de prueba.", "tiempo": 10])
        }
        for _ in 0..<100 where engine.handoff == nil { try await Task.sleep(nanoseconds: 20_000_000) }
        XCTAssertNotNil(engine.handoff)
        XCTAssertFalse(engine.isSelectingAnnotation, "La selección no debe tapar la página durante el relevo.")
        let activity = engine.lastActivity

        for (action, fields) in blockedRequests(document: document.result, root: root, file: file) {
            let reply = await perform(engine, action, fields)
            XCTAssertFalse(reply.ok, action + ": " + reply.json)
            XCTAssertTrue(reply.error?.contains("esperando a la persona") == true, action + ": " + reply.json)
            XCTAssertEqual(engine.lastActivity, activity, "No debe empezar la acción " + action)
        }
        XCTAssertNil(engine.viewport, "El viewport bloqueado no debe haberse aplicado.")
        let effects = try await engine.webView.evaluateJavaScript("window.effects") as? Int
        XCTAssertEqual(effects, 0, "Las teclas, hover y subida bloqueados no deben disparar la página.")
        engine.continueHandoff()
        let continued = await handoff.value
        XCTAssertTrue(continued.ok, continued.json)
        let console = await perform(engine, "consola")
        XCTAssertTrue(console.ok, console.json)
    }

    private func page() async throws -> BrowserEngine {
        let engine = BrowserEngine()
        _ = await perform(engine, "estado")
        engine.webView.loadHTMLString("""
        <!doctype html><title>Integración</title><body style="margin:0;min-height:100vh">
        <button id="objetivo" style="position:absolute;left:20px;top:30px;width:160px;height:60px"
                onclick="window.effects++" onmouseover="window.effects++">Objetivo</button>
        <input aria-label="Campo" onkeydown="window.effects++" style="position:absolute;top:120px">
        <input type="file" aria-label="Archivo" onchange="window.effects++" style="position:absolute;top:180px">
        <script>window.effects = 0;</script></body>
        """, baseURL: URL(string: "https://prueba.alfalfa.test/integracion"))
        try await loaded(engine, title: "Integración")
        return engine
    }

    private func loaded(_ engine: BrowserEngine, title: String) async throws {
        for _ in 0..<200 {
            if engine.webView.title == title && !engine.webView.isLoading { return }
            try await Task.sleep(nanoseconds: 20_000_000)
        }
        throw BrowserFailure("La página de integración no cargó.")
    }

    private func perform(_ engine: BrowserEngine, _ action: String,
                         _ fields: [String: Any] = [:]) async -> BrowserReply {
        await engine.perform(BrowserRequest(id: UUID().uuidString, action: action, fields: fields))
    }

    private func blockedRequests(document: [String: Any], root: URL, file: URL) -> [(String, [String: Any])] {
        [("consola", ["limite": 10]), ("red", ["limite": 10]), ("viewport", ["ancho": 390, "alto": 844]),
         ("esperar", ["texto": "Objetivo", "tiempo_ms": 100]), ("hover", ["objetivo": "Objetivo"]),
         ("tecla", ["tecla": "Enter", "objetivo": "Campo"]),
         ("subir", ["objetivo": "Archivo", "proyecto": root.path, "rutas": [file.path],
                     "documento_aprobado": document["documento"] ?? "", "url_aprobada": document["url"] ?? ""])]
    }
}
