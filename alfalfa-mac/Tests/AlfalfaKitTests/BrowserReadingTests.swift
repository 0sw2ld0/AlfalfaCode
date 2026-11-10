@testable import AlfalfaKit
import WebKit
import XCTest

@MainActor
final class BrowserReadingTests: XCTestCase {
    func testIdentityTracksDocumentAndReferencesSurviveAnotherRead() async throws {
        let engine = BrowserEngine()
        defer { engine.close() }
        _ = await engine.perform(BrowserRequest(id: "estado", action: "estado"))
        try await load(engine, title: "Lectura uno")
        let corta = await read(engine, mode: "arbol", limit: 500)
        let identity = try XCTUnwrap(corta.result["documento"] as? String)
        let first = try XCTUnwrap(corta.result["arbol"] as? String)
        let range = try XCTUnwrap(first.range(of: #"\[e\d+\]"#, options: .regularExpression))
        let ref = String(first[range].dropFirst().dropLast())
        XCTAssertEqual(corta.result["recortado"] as? Bool, true)

        let clicked = await engine.perform(BrowserRequest(id: "click", action: "pulsar", fields: ["ref": ref]))
        XCTAssertTrue(clicked.ok, clicked.error ?? "")
        let completa = await read(engine, mode: "arbol", limit: 15000)
        XCTAssertEqual(completa.result["documento"] as? String, identity)
        XCTAssertTrue((completa.result["arbol"] as? String)?.contains("[\(ref)]") ?? false)
        XCTAssertTrue((completa.result["arbol"] as? String)?.contains("Pedido creado") ?? false)
        let texto = await read(engine, mode: "texto", limit: 500)
        XCTAssertEqual(texto.result["documento"] as? String, identity)

        try await load(engine, title: "Lectura dos")
        let recarga = await read(engine, mode: "arbol", limit: 15000)
        XCTAssertEqual(recarga.result["url"] as? String, completa.result["url"] as? String)
        XCTAssertNotEqual(try XCTUnwrap(recarga.result["documento"] as? String), identity)
    }

    private func read(_ engine: BrowserEngine, mode: String, limit: Int) async -> BrowserReply {
        await engine.perform(BrowserRequest(id: UUID().uuidString, action: "leer",
                                            fields: ["modo": mode, "max": limit]))
    }

    private func load(_ engine: BrowserEngine, title: String) async throws {
        let rows = (0..<40).map { "<p>Artículo ficticio \($0) de catálogo para la lectura.</p>" }.joined()
        let html = """
        <!doctype html><title>\(title)</title>
        <button onclick="document.getElementById('estado').textContent='Pedido creado'">Crear pedido</button>
        <p id="estado">Sin pedido</p>\(rows)
        """
        engine.webView.loadHTMLString(html, baseURL: URL(string: "https://lectura.alfalfa.test/"))
        let deadline = Date().addingTimeInterval(20)
        while Date() < deadline {
            let script = "document.readyState + ':' + document.title"
            if (try? await engine.webView.evaluateJavaScript(script) as? String) == "complete:\(title)" { return }
            try await Task.sleep(nanoseconds: 50_000_000)
        }
        throw NSError(domain: "BrowserReadingTests: la página ficticia no cargó", code: 1)
    }
}
