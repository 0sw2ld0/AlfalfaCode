@testable import AlfalfaKit
import WebKit
import XCTest

/// El navegador propio: el puente de mensajes con el motor y lo que lee y hace en páginas HTML locales.
@MainActor
final class BrowserTests: XCTestCase {

    // MARK: - Propiedades privadas

    /// Una página con lo que el agente lee y maneja: texto, enlaces, campos, un formulario y una descarga.
    nonisolated private static let page = """
    <!doctype html><html><head><title>Prueba</title></head><body>
    <script>window.secreto = 42;</script>
    <h1>Bienvenida</h1>
    <p>Un párrafo con <b>texto</b> visible.</p>
    <p style="display:none">Texto escondido</p>
    <div aria-hidden="true">Decoración</div>
    <a href="/info">Más información</a>
    <button id="contar" onclick="contar()">Contar</button>
    <span id="cuenta">Sin pulsar</span>
    <label for="nombre">Tu nombre</label> <input id="nombre" name="nombre">
    <span id="eco"></span>
    <form id="entrar" aria-label="Entrar" method="post" action="/login"
          onsubmit="event.preventDefault(); document.getElementById('estado').textContent = 'Enviado'">
      <label>Correo <input name="correo" type="email" placeholder="tu@correo"></label>
      <label>Clave <input name="clave" type="password"></label>
      <label>País <select name="pais"><option>Perú</option><option>España</option></select></label>
      <button>Entrar</button>
    </form>
    <span id="estado">Sin enviar</span>
    <a href="/informe.pdf" download>Bajar informe</a>
    <div style="height: 3000px"></div>
    <script>
      window.veces = 0;
      const texto = (id, valor) => { document.getElementById(id).textContent = valor; };
      window.contar = () => texto('cuenta', 'Pulsado ' + (++window.veces));
      document.getElementById('nombre').addEventListener('input', e => texto('eco', 'Hola ' + e.target.value));
    </script>
    </body></html>
    """

    // MARK: - Puente de mensajes

    func testDecodesARequestAndEncodesItsReply() throws {
        let line = #"{"version":2,"tipo":"navegador","id":"n1","accion":"pulsar","ref":"e3","#
            + #""permitido":["formulario"]}"#
        guard case let .browser(request)? = AlfalfaMessage.decode(line: line) else { return XCTFail("navegador") }
        XCTAssertEqual(request.id, "n1")
        XCTAssertEqual(request.action, "pulsar")
        XCTAssertEqual(request.string("ref"), "e3")
        XCTAssertEqual(request.strings("permitido"), ["formulario"])
        XCTAssertNil(request.fields["tipo"])
        XCTAssertNil(AlfalfaMessage.decode(line: #"{"version":2,"tipo":"navegador","accion":"leer"}"#))

        let ok = AlfalfaCommand.browserReply(.success("n1", ["titulo": "Demo", "url": "http://127.0.0.1:8000/"])).line
        let object = try XCTUnwrap(try JSONSerialization.jsonObject(with: Data(ok.utf8)) as? [String: Any])
        XCTAssertEqual(object["tipo"] as? String, "navegador_respuesta")
        XCTAssertEqual(object["id"] as? String, "n1")
        XCTAssertEqual(object["ok"] as? Bool, true)
        XCTAssertEqual((object["resultado"] as? [String: Any])?["titulo"] as? String, "Demo")
        XCTAssertTrue(ok.hasSuffix("}\n"))

        let permission = BrowserReply.permission("n2", kind: "contrasena", detail: "Campo «Clave»")
        XCTAssertFalse(permission.ok)
        XCTAssertEqual(permission.permissionKind, "contrasena")
        XCTAssertEqual(BrowserReply.failure("n3", "No hay página.").error, "No hay página.")
    }

    func testTheEngineIsOfferedWithAFlag() {
        XCTAssertTrue(Launcher.stdioArguments(task: "x", resume: nil, model: nil, effort: nil, readOnly: false,
                                              browser: true).contains("--navegador"))
        XCTAssertFalse(Launcher.stdioArguments(task: "x", resume: nil, model: nil, effort: nil, readOnly: false)
            .contains("--navegador"))
        XCTAssertEqual(PermissionReason(rawValue: "navegador")?.title, "Navegador")
    }

    func testClassifiesAddressesLikeTheEngine() throws {
        func kind(_ text: String) throws -> BrowserAddress.Kind {
            BrowserAddress.kind(of: try XCTUnwrap(URL(string: text)))
        }
        XCTAssertEqual(try kind("https://ejemplo.org/"), .publicInternet)
        XCTAssertEqual(try kind("http://8.8.8.8/"), .publicInternet)
        XCTAssertEqual(try kind("http://127.0.0.1:8000/"), .local)
        XCTAssertEqual(try kind("http://localhost:3000"), .local)
        XCTAssertEqual(try kind("http://[::1]:5000/"), .local)
        XCTAssertEqual(try kind("http://192.168.1.1/"), .privateNetwork)
        XCTAssertEqual(try kind("http://10.1.2.3/"), .privateNetwork)
        XCTAssertEqual(try kind("http://169.254.169.254/"), .privateNetwork)
        XCTAssertEqual(try kind("http://impresora.local/"), .privateNetwork)
        XCTAssertEqual(try kind("http://router/"), .privateNetwork)
        XCTAssertEqual(BrowserAddress.origin(of: try XCTUnwrap(URL(string: "http://127.0.0.1:8000/a"))), "127.0.0.1:8000")
        XCTAssertEqual(BrowserAddress.origin(of: try XCTUnwrap(URL(string: "https://Ejemplo.org/"))), "ejemplo.org:443")
        XCTAssertEqual(BrowserAddress.normalized("ejemplo.org")?.absoluteString, "https://ejemplo.org")
        XCTAssertEqual(BrowserAddress.normalized("localhost:8000")?.absoluteString, "http://localhost:8000")
        XCTAssertNil(BrowserAddress.normalized("file:///etc/passwd"))
        XCTAssertNil(BrowserAddress.normalized("dos palabras"))
    }

    // MARK: - Páginas locales

    private func engineWithPage(_ html: String = page) async throws -> BrowserEngine {
        let engine = BrowserEngine()
        _ = await engine.perform(BrowserRequest(id: "0", action: "estado"))
        engine.webView.loadHTMLString(html, baseURL: URL(string: "https://prueba.alfalfa.test/"))
        let deadline = Date().addingTimeInterval(20)
        while Date() < deadline {
            let script = "document.readyState + ':' + document.title"
            let state = try? await engine.webView.evaluateJavaScript(script) as? String
            if state == "complete:Prueba" { return engine }
            try await Task.sleep(nanoseconds: 50_000_000)
        }
        XCTFail("La página de prueba no cargó")
        return engine
    }

    private func perform(_ engine: BrowserEngine, _ action: String,
                         _ fields: [String: Any] = [:]) async -> BrowserReply {
        await engine.perform(BrowserRequest(id: UUID().uuidString, action: action, fields: fields))
    }

    private func tree(_ engine: BrowserEngine) async -> String {
        await perform(engine, "leer", ["modo": "arbol"]).result["arbol"] as? String ?? ""
    }

    private func ref(_ name: String, in tree: String) -> String? {
        guard let line = tree.split(separator: "\n").first(where: { $0.contains("«\(name)»") && $0.contains("[e") }),
              let range = line.range(of: #"\[e\d+\]"#, options: .regularExpression) else { return nil }
        return String(line[range].dropFirst().dropLast())
    }

    private func pageText(_ engine: BrowserEngine, _ id: String) async throws -> String {
        try await engine.webView.evaluateJavaScript("document.getElementById('\(id)').textContent") as? String ?? ""
    }

    func testReadsTheTreeWithReferencesAndTheText() async throws {
        let engine = try await engineWithPage()
        XCTAssertTrue(engine.isPanelOpen)
        let reply = await perform(engine, "leer", [:])
        XCTAssertTrue(reply.ok, reply.error ?? "")
        let tree = reply.result["arbol"] as? String ?? ""
        XCTAssertEqual(reply.result["titulo"] as? String, "Prueba")
        XCTAssertTrue(tree.contains("- heading «Bienvenida» (nivel 1)"), tree)
        XCTAssertTrue(tree.contains("- text «Un párrafo con»"), tree)
        XCTAssertTrue(tree.contains("- link «Más información» [e1] (→ /info)"), tree)
        XCTAssertTrue(tree.contains("button «Contar» [e2]"), tree)
        XCTAssertTrue(tree.contains("textbox «Tu nombre» [e3]"), tree)
        XCTAssertTrue(tree.contains("- form «Entrar» (POST /login)"), tree)
        XCTAssertTrue(tree.contains("  - textbox «Correo» [e4] (pista: \"tu@correo\")"), tree)
        XCTAssertTrue(tree.contains("textbox «Clave» [e5] (contraseña)"), tree)
        XCTAssertTrue(tree.contains("combobox «País» [e6] (elegido: \"Perú\", opciones: \"Perú\" \"España\")"), tree)
        XCTAssertTrue(tree.contains("button «Entrar» [e7] (envía el formulario)"), tree)
        XCTAssertFalse(tree.contains("escondido"), tree)
        XCTAssertFalse(tree.contains("Decoración"), tree)
        XCTAssertEqual(reply.result["referencias"] as? Int, 8)

        let text = await perform(engine, "leer", ["modo": "texto"]).result["texto"] as? String ?? ""
        XCTAssertTrue(text.contains("Bienvenida") && text.contains("Un párrafo con texto visible."), text)
        XCTAssertFalse(text.contains("Texto escondido"))
    }

    func testClicksByReferenceAndByText() async throws {
        let engine = try await engineWithPage()
        let tree8 = await tree(engine)

        let button = try XCTUnwrap(ref("Contar", in: tree8))
        let first = await perform(engine, "pulsar", ["ref": button])
        XCTAssertTrue(first.ok, first.error ?? "")
        XCTAssertEqual(first.result["elemento"] as? String, "button «Contar»")
        XCTAssertEqual(first.result["navego"] as? Bool, false)
        let value1 = try await pageText(engine, "cuenta")

        XCTAssertEqual(value1, "Pulsado 1")
        let second = await perform(engine, "pulsar", ["objetivo": "contar"])
        XCTAssertTrue(second.ok, second.error ?? "")
        let value2 = try await pageText(engine, "cuenta")

        XCTAssertEqual(value2, "Pulsado 2")
        let missing = await perform(engine, "pulsar", ["objetivo": "Nada así"])
        XCTAssertTrue(missing.error?.contains("No hay ningún elemento visible") ?? false)
        let download = await perform(engine, "pulsar", ["objetivo": "Bajar informe"])
        XCTAssertTrue(download.error?.contains("browser_download") ?? false, download.error ?? "")
    }

    func testSendingAFormWaitsForPermission() async throws {
        let engine = try await engineWithPage()
        let tree9 = await tree(engine)

        let submit = try XCTUnwrap(ref("Entrar", in: tree9))
        let asked = await perform(engine, "pulsar", ["ref": submit])
        XCTAssertEqual(asked.permissionKind, "formulario")
        let value3 = try await pageText(engine, "estado")

        XCTAssertEqual(value3, "Sin enviar")
        let sent = await perform(engine, "pulsar", ["ref": submit, "permitido": ["formulario"]])
        XCTAssertTrue(sent.ok, sent.error ?? "")
        let value4 = try await pageText(engine, "estado")

        XCTAssertEqual(value4, "Enviado")
    }

    func testTypesIntoFieldsAndAsksBeforeAPassword() async throws {
        let engine = try await engineWithPage()
        let current = await tree(engine)
        let name = try XCTUnwrap(ref("Tu nombre", in: current))
        let typed = await perform(engine, "escribir", ["ref": name, "texto": "Luna", "borrar": true])
        XCTAssertTrue(typed.ok, typed.error ?? "")
        let value5 = try await pageText(engine, "eco")

        XCTAssertEqual(value5, "Hola Luna")
        let password = try XCTUnwrap(ref("Clave", in: current))
        let asked = await perform(engine, "escribir", ["ref": password, "texto": "secreto"])
        XCTAssertEqual(asked.permissionKind, "contrasena")
        let allowed = await perform(engine, "escribir",
                                    ["ref": password, "texto": "secreto", "permitido": ["contrasena"]])
        XCTAssertEqual(allowed.result["valor"] as? String, "(oculto)")
        let tree6 = await tree(engine)

        XCTAssertTrue(tree6.contains("(contraseña, escrita)"))
        let submit = await perform(engine, "escribir", ["objetivo": "Correo", "texto": "ana@correo.pe", "enviar": true])
        XCTAssertEqual(submit.permissionKind, "formulario")
        let country = await perform(engine, "escribir", ["objetivo": "País", "texto": "España"])
        XCTAssertTrue(country.ok, country.error ?? "")
        let tree7 = await tree(engine)

        XCTAssertTrue(tree7.contains("elegido: \"España\""))
    }

    func testJavaScriptRunsIsolatedUnlessItModifiesThePage() async throws {
        let engine = try await engineWithPage()
        let title = await perform(engine, "js", ["codigo": "return document.title"])
        XCTAssertEqual(title.result["valor"] as? String, "\"Prueba\"")
        XCTAssertEqual(title.result["cambios_dom"] as? Int, 0)
        let isolated = await perform(engine, "js", ["codigo": "return typeof window.secreto"])
        XCTAssertEqual(isolated.result["valor"] as? String, "\"undefined\"")
        let page = await perform(engine, "js", ["codigo": "return window.secreto", "modifica": true])
        XCTAssertEqual(page.result["valor"] as? String, "42")
        let failing = await perform(engine, "js", ["codigo": "throw new Error('mal')"])
        XCTAssertFalse(failing.ok)
        XCTAssertTrue(failing.error?.contains("mal") ?? false, failing.error ?? "")
    }

    func testScrollsAndTakesAScreenshot() async throws {
        let engine = try await engineWithPage()
        let scrolled = await perform(engine, "desplazar", ["direccion": "abajo"])
        XCTAssertTrue(scrolled.ok, scrolled.error ?? "")
        XCTAssertGreaterThan(scrolled.result["y"] as? Int ?? 0, 0)
        let path = FileManager.default.temporaryDirectory.appendingPathComponent("navegador-\(UUID().uuidString).png")
        defer { try? FileManager.default.removeItem(at: path) }
        let shot = await perform(engine, "captura", ["ruta": path.path])
        XCTAssertTrue(shot.ok, shot.error ?? "")
        let data = try Data(contentsOf: path)
        XCTAssertEqual(Array(data.prefix(4)), [0x89, 0x50, 0x4E, 0x47])
        XCTAssertGreaterThan(shot.result["ancho"] as? Int ?? 0, 0)
    }

    func testReferencesExpireWhenThePageChanges() async throws {
        let engine = try await engineWithPage()
        _ = await tree(engine)
        engine.webView.loadHTMLString(Self.page.replacingOccurrences(of: "Bienvenida", with: "Otra"),
                                      baseURL: URL(string: "https://prueba.alfalfa.test/otra"))
        try await Task.sleep(nanoseconds: 800_000_000)
        let stale = await perform(engine, "pulsar", ["ref": "e2"])
        XCTAssertTrue(stale.error?.contains("ya no existe") ?? false, stale.error ?? "")
    }

    func testLocalAddressesNeedTheEngineApproval() async throws {
        let engine = BrowserEngine()
        let blocked = await perform(engine, "abrir", ["url": "http://127.0.0.1:59999/"])
        XCTAssertTrue(blocked.error?.contains("no está aprobada") ?? false, blocked.error ?? "")
        let approved = await perform(engine, "abrir", ["url": "http://127.0.0.1:59999/", "red_local": ["127.0.0.1:59999"]])
        XCTAssertTrue(approved.error?.contains("No se pudo abrir") ?? false, approved.error ?? approved.json)
        let file = await perform(engine, "abrir", ["url": "file:///etc/hosts"])
        XCTAssertTrue(file.error?.contains("http y https") ?? false)
    }
}
