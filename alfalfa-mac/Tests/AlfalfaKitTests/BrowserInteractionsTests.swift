@testable import AlfalfaKit
import AppKit
import XCTest

@MainActor
final class BrowserInteractionsTests: XCTestCase {
    private func page() async throws -> BrowserEngine {
        let engine = BrowserEngine()
        _ = await engine.perform(BrowserRequest(id: "iniciar", action: "leer"))
        engine.webView.loadHTMLString("""
        <title>Interacciones</title><style>#zona:hover{background:rgb(1, 2, 3)}</style>
        <button id="zona" onmouseover="document.title='Hover'">Zona</button>
        <input id="primero" aria-label="Primero"><input id="segundo" aria-label="Segundo">
        <input aria-label="Fuera" onfocus="document.querySelector('form input').focus()">
        <form onsubmit="event.preventDefault();document.title='Enviado'">
        <input aria-label="Enviar" name="demo"><button>Enviar</button></form>
        <input type="file" multiple aria-label="Archivo" onchange="document.title='Subida '+this.files.length">
        <div id="listo"></div><script>
        setTimeout(()=>document.querySelector('#listo').textContent='Ya listo',150)
        </script>
        """, baseURL: URL(string: "https://prueba.alfalfa.test/"))
        for _ in 0..<100 where engine.webView.title != "Interacciones" {
            try await Task.sleep(nanoseconds: 20_000_000)
        }
        return engine
    }

    func testWaitForTextReferenceURLLoadAndTimeout() async throws {
        let engine = try await page()
        defer { engine.close() }
        _ = await engine.perform(BrowserRequest(id: "arbol", action: "leer"))
        for condition: [String: Any] in [["texto": "Ya listo"], ["ref": "e1"],
                                       ["url_contiene": "alfalfa.test"], ["carga": true]] {
            let reply = await engine.perform(BrowserRequest(id: "espera", action: "esperar", fields: condition))
            XCTAssertTrue(reply.ok, reply.json)
        }
        let timeout = await engine.perform(BrowserRequest(id: "timeout", action: "esperar",
                                                          fields: ["texto": "Nunca aparece", "tiempo_ms": 50]))
        XCTAssertTrue(timeout.error?.contains("tiempo de espera") ?? false)
    }

    func testKeysFocusTabAndAskBeforeSubmitting() async throws {
        let engine = try await page()
        defer { engine.close() }
        let tab = await engine.perform(BrowserRequest(id: "tab", action: "tecla",
                                                      fields: ["objetivo": "Primero", "tecla": "Tab"]))
        XCTAssertTrue(tab.ok, tab.json)
        let focus = try await engine.webView.evaluateJavaScript("document.activeElement.id") as? String
        XCTAssertEqual(focus, "segundo")
        let blocked = await engine.perform(BrowserRequest(id: "enter", action: "tecla",
                                                          fields: ["objetivo": "Enviar", "tecla": "Enter"]))
        XCTAssertEqual(blocked.permissionKind, "formulario")
        XCTAssertNotEqual(engine.webView.title, "Enviado")
        let sent = await engine.perform(BrowserRequest(id: "enter2", action: "tecla",
                                                       fields: ["objetivo": "Enviar",
                                                                "tecla": "Enter", "permitido": ["formulario"]]))
        XCTAssertTrue(sent.ok, sent.json)
        XCTAssertEqual(engine.webView.title, "Enviado")
    }

    func testHoverRunsThePageHandlerAndCSS() async throws {
        let engine = try await page()
        defer { engine.close() }
        let window = BrowserTestWindow(contentRect: NSRect(x: 80, y: 80, width: 1100, height: 800),
                                       styleMask: [.titled], backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        engine.webView.removeFromSuperview()
        window.contentView = engine.webView
        window.orderBack(nil)
        defer { window.close() }
        let hover = await engine.perform(BrowserRequest(id: "hover", action: "hover", fields: ["objetivo": "Zona"]))
        XCTAssertTrue(hover.ok, hover.json)
        XCTAssertEqual(engine.webView.title, "Hover")
        let color = try await engine.webView.evaluateJavaScript(
            "getComputedStyle(document.querySelector('#zona')).backgroundColor") as? String
        XCTAssertEqual(color, "rgb(1, 2, 3)")
    }

    func testUploadsOnlyProjectFilesAndTheirActualBytes() async throws {
        let engine = try await page()
        defer { engine.close() }
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("subida-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let file = root.appendingPathComponent("demo.txt")
        try Data("Datos inventados".utf8).write(to: file)
        let state = await engine.perform(BrowserRequest(id: "estado", action: "estado"))
        let upload = await engine.perform(BrowserRequest(id: "subir", action: "subir",
                                                         fields: ["objetivo": "Archivo",
                                                                  "proyecto": root.path, "rutas": [file.path],
                                                                  "documento_aprobado": state.result["documento"] ?? "",
                                                                  "url_aprobada": state.result["url"] ?? ""]))
        XCTAssertTrue(upload.ok, upload.json)
        XCTAssertEqual(engine.webView.title, "Subida 1")
        let bytes = try await engine.webView.callAsyncJavaScript(
            "return await document.querySelector('input[type=file]').files[0].text()",
            arguments: [:], in: nil, contentWorld: .page) as? String
        XCTAssertEqual(bytes, "Datos inventados")
        let escape = await engine.perform(BrowserRequest(id: "escape", action: "subir",
                                                         fields: ["objetivo": "Archivo",
                                                                  "proyecto": root.path, "rutas": ["/etc/hosts"]]))
        XCTAssertFalse(escape.ok)
    }

    func testFocusHandlerCannotSkipTheFormPermission() async throws {
        let engine = try await page()
        defer { engine.close() }
        let reply = await engine.perform(BrowserRequest(id: "enter", action: "tecla",
                                                        fields: ["objetivo": "Fuera", "tecla": "Enter"]))
        XCTAssertEqual(reply.permissionKind, "formulario")
        XCTAssertEqual(engine.webView.title, "Interacciones")
    }

    func testHoverDeadlineReleasesAnOperationThatDoesNotFinishOnTime() async throws {
        let begin = Date()
        do {
            let value: Int = try await BrowserDeadline.run(seconds: 0.02) {
                try? await Task.sleep(nanoseconds: 1_000_000_000)
                return 1
            }
            XCTFail("El plazo debía caducar: \(value)")
        } catch {
            XCTAssertTrue(String(describing: error).contains("plazo"))
        }
        XCTAssertLessThan(Date().timeIntervalSince(begin), 0.5)
    }

    func testProjectFileRejectsHardlinksAndSymlinks() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("archivos-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let file = root.appendingPathComponent("original.txt")
        let link = root.appendingPathComponent("enlace.txt")
        try Data("Dato".utf8).write(to: file)
        try FileManager.default.linkItem(at: file, to: link)
        XCTAssertThrowsError(try BrowserProjectFile.read(path: link, root: root, remaining: 100))
        try FileManager.default.removeItem(at: link)
        try FileManager.default.createSymbolicLink(at: link, withDestinationURL: file)
        XCTAssertThrowsError(try BrowserProjectFile.read(path: link, root: root, remaining: 100))
    }

    func testUploadPermissionDoesNotSurviveADocumentChange() async throws {
        let engine = try await page()
        defer { engine.close() }
        let state = await engine.perform(BrowserRequest(id: "estado", action: "estado"))
        engine.webView.loadHTMLString("<title>Otra</title><input type=file aria-label=Archivo>",
                                      baseURL: URL(string: "https://prueba.alfalfa.test/"))
        for _ in 0..<100 where engine.webView.title != "Otra" {
            try await Task.sleep(nanoseconds: 20_000_000)
        }
        do {
            let reply = try await engine.evaluate(BrowserScripts.uploadFiles, [
                "objetivo": "Archivo", "documento_aprobado": state.result["documento"] ?? "",
                "url_aprobada": state.result["url"] ?? "", "archivos": []])
            XCTFail("No debió entregar una autorización antigua: \(reply)")
        } catch {
            XCTAssertTrue(String(describing: error).contains("página cambió"))
        }
    }
}

/// Igual que TestWKWebViewHostWindow de WebKit: XCTest no se registra como app de primer plano.
private final class BrowserTestWindow: NSWindow {
    override var isKeyWindow: Bool { true }
}
