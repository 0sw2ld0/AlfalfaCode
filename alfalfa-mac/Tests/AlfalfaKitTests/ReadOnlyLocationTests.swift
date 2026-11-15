@testable import AlfalfaKit
import XCTest

/// browser_eval sin permiso corre en el mundo de sólo lectura, pero `location` no se puede tapar ahí:
/// navegar a una URL `javascript:` ejecutaba código en la página (y enviaba un formulario) sin tarjeta.
@MainActor
final class ReadOnlyLocationTests: XCTestCase {
    func testReadOnlyEvalCannotRunJavaScriptURLs() async throws {
        let engine = BrowserEngine()
        defer { engine.close() }
        _ = await engine.perform(BrowserRequest(id: "iniciar", action: "leer"))
        engine.webView.loadHTMLString("""
        <title>Formulario</title>
        <form onsubmit="event.preventDefault();document.title='Enviado'"><input name="a"><button>Ir</button></form>
        <iframe srcdoc="<p>marco</p>"></iframe>
        """, baseURL: URL(string: "https://prueba.alfalfa.test/"))
        for _ in 0..<100 where engine.webView.title != "Formulario" {
            try await Task.sleep(nanoseconds: 20_000_000)
        }
        XCTAssertEqual(engine.webView.title, "Formulario")
        for script in [
            "location['assign']('javascript:void(document.forms[0].requestSubmit())')",
            "location.href = 'javascript:void(document.title=\"Cambiado\")'",
            "window['location']['replace']('javascript:void(document.title=\"Cambiado\")')",
            "frames[0].location.assign('javascript:void(parent.document.title=\"Cambiado\")')",
        ] {
            let respuesta = await engine.perform(BrowserRequest(id: "js", action: "js", fields: ["codigo": script]))
            try await Task.sleep(nanoseconds: 400_000_000)
            XCTAssertEqual(engine.webView.title, "Formulario", script)
            XCTAssertFalse(respuesta.ok, script)
            XCTAssertTrue(respuesta.error?.contains("modifies: true") ?? false, respuesta.json)
        }
        // Leer sigue funcionando.
        let leido = await engine.perform(BrowserRequest(id: "leer", action: "js",
                                                        fields: ["codigo": "return location.hostname"]))
        XCTAssertEqual(leido.result["valor"] as? String, "\"prueba.alfalfa.test\"")
    }

    func testTheRuleMatchesTheEngineFilter() {
        for script in ["location['assign']('x')", "return location", "return '\\x6aavascript:1'",
                       "return '\\u006aavascript:1'", "return 'JavaScript:1'", "window['location']['reload']()"] {
            XCTAssertNotNil(ReadOnlyScript.refusal(script), script)
        }
        for script in ["return location.href", "return window.location.pathname", "return document.title",
                       "return [location.host, location.search]"] {
            XCTAssertNil(ReadOnlyScript.refusal(script), script)
        }
    }
}
