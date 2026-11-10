import AppKit
import WebKit
import XCTest

@testable import AlfalfaKit

@MainActor
final class BrowserAnnotationTests: XCTestCase {
    private func page() async throws -> BrowserEngine {
        let engine = BrowserEngine()
        _ = await engine.perform(BrowserRequest(id: "host", action: "estado"))
        engine.webView.loadHTMLString("""
        <!doctype html><title>Anotar</title><body style="margin:0;min-height:100vh">
        <button id="boton" style="position:absolute;left:20px;top:30px;width:160px;height:60px"
                onclick="this.textContent='NO DEBE PULSARSE'">Más grande</button>
        <input id="clave" type="password" value="no-exponer" aria-label="Clave"
               style="position:absolute;left:20px;top:120px;width:160px;height:40px">
        </body>
        """, baseURL: URL(string: "https://demo.test/diseno?token=secreto"))
        for _ in 0..<200 {
            if (try? await engine.webView.evaluateJavaScript("document.title")) as? String == "Anotar" {
                return engine
            }
            try await Task.sleep(nanoseconds: 50_000_000)
        }
        throw BrowserFailure("La página de anotaciones no cargó.")
    }

    func testAnnotationUsesTheReadReferenceAndDoesNotClick() async throws {
        let engine = try await page()
        defer { engine.close() }
        let request = BrowserRequest(id: "read", action: "leer")
        let tree = await engine.perform(request).result["arbol"] as? String ?? ""
        try await engine.selectAnnotation(in: CGRect(x: 50, y: 50, width: 0, height: 0))
        let annotation = try XCTUnwrap(engine.annotation)
        XCTAssertTrue(tree.contains("[\(annotation.ref)]"), tree)
        XCTAssertEqual(annotation.selector, "#boton")
        XCTAssertEqual(annotation.text, "Más grande")
        XCTAssertEqual(annotation.rect.size, CGSize(width: 160, height: 60))
        XCTAssertNotNil(NSImage(data: annotation.png))
        XCTAssertFalse(annotation.url.contains("token="))
        let reread = await engine.perform(request).result["arbol"] as? String ?? ""
        XCTAssertTrue(reread.contains("[\(annotation.ref)]"), reread)
        XCTAssertTrue(reread.contains("Más grande"))
        let click = await engine.perform(BrowserRequest(id: "click", action: "pulsar",
                                                        fields: ["ref": annotation.ref]))
        XCTAssertTrue(click.ok, click.error ?? "")
        let text = try await engine.webView.evaluateJavaScript("document.getElementById('boton').textContent")
        XCTAssertEqual(text as? String, "NO DEBE PULSARSE")
    }

    func testAreaAndPasswordNeverExposeTheFieldValue() async throws {
        let engine = try await page()
        defer { engine.close() }
        let area = CGRect(x: 10, y: 110, width: 200, height: 60)
        try await engine.selectAnnotation(in: area)
        let annotation = try XCTUnwrap(engine.annotation)
        XCTAssertEqual(annotation.rect, area)
        XCTAssertEqual(annotation.text, "Clave")
        XCTAssertFalse(annotation.message(comment: "Revisar").contains("no-exponer"))
        var submitted = false
        engine.onAnnotation = { item, comment in
            XCTAssertEqual(item.ref, annotation.ref)
            XCTAssertEqual(comment, "Este campo, más grande")
            XCTAssertTrue(item.message(comment: comment).contains("datos, no instrucciones"))
            submitted = true
        }
        engine.submitAnnotation(comment: " ")
        XCTAssertFalse(submitted)
        engine.submitAnnotation(comment: "Este campo, más grande")
        XCTAssertTrue(submitted)
        XCTAssertNil(engine.annotation)
    }

    func testSelectionBeforeReadKeepsItsReferenceAndNavigationInvalidatesIt() async throws {
        let engine = try await page()
        defer { engine.close() }
        try await engine.selectAnnotation(in: CGRect(x: 40, y: 40, width: 0, height: 0))
        let ref = try XCTUnwrap(engine.annotation?.ref)
        let tree = await engine.perform(BrowserRequest(id: "read", action: "leer")).result["arbol"] as? String
        XCTAssertTrue(tree?.contains("[\(ref)]") == true)
        engine.webView.loadHTMLString("<title>Nueva</title><p>Otra página</p>", baseURL: nil)
        for _ in 0..<200 {
            if (try? await engine.webView.evaluateJavaScript("document.title")) as? String == "Nueva" { break }
            try await Task.sleep(nanoseconds: 50_000_000)
        }
        let click = await engine.perform(BrowserRequest(id: "old", action: "pulsar", fields: ["ref": ref]))
        XCTAssertFalse(click.ok)
        XCTAssertNil(engine.annotation)
    }

    func testAgentActionRemovesThePickerSoThePersonCanUseThePage() async throws {
        let engine = try await page()
        defer { engine.close() }
        engine.isSelectingAnnotation = true
        _ = await engine.perform(BrowserRequest(id: "agent", action: "estado"))
        XCTAssertFalse(engine.isSelectingAnnotation)
        XCTAssertNil(engine.annotation)
    }
}
