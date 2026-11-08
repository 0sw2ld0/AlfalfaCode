@testable import AlfalfaKit
import XCTest

func fixtureLines(_ name: String) throws -> [String] {
    let url = try XCTUnwrap(Bundle.module.url(forResource: name, withExtension: "jsonl", subdirectory: "Fixtures"))
    return try String(contentsOf: url, encoding: .utf8).split(separator: "\n").map(String.init)
}

final class ProtocolTests: XCTestCase {
    func testDecodesEveryOutputType() throws {
        let lines = try fixtureLines("salidas")
        let decoded = lines.map(AlfalfaMessage.decode(line:))
        XCTAssertEqual(decoded[0], .session(SessionInfo(id: "abc", cwd: "/repo", model: "deepseek-v4.1-flash:cloud", effort: "medium")))
        XCTAssertEqual(decoded[1], .event(AgentEvent(role: "assistant", text: "Archivo corregido", time: 1793900000.0)))
        XCTAssertEqual(decoded[2], .permission(PermissionRequest(id: "p1", argv: ["touch", "resultado"], purpose: "Comprobar escritura")))
        XCTAssertEqual(decoded[3], .permissionResolved(id: "p1", approved: true, reason: "Respuesta del cliente"))
        XCTAssertEqual(decoded[4], .context(tokens: 28400, limit: 262144))
        XCTAssertEqual(decoded[5], .error("Mensaje inválido o id de permiso desconocido o repetido."))
        XCTAssertEqual(decoded[6], .turnFinished(Outcome(state: "completed", summary: "Archivo corregido", exitCode: 0, tokens: 100)))
        XCTAssertEqual(decoded[7], .finished(Outcome(state: "interrupted", summary: "Sesión interrumpida; puedes reanudarla.", exitCode: 130, tokens: 100)))
        guard case let .sessions(rows)? = decoded[8] else { return XCTFail("sesiones") }
        XCTAssertEqual(rows.count, 2)
        XCTAssertEqual(rows[0], SessionSummary(id: "abc", task: "Corregir", cwd: "/repo", state: "interrupted",
                                               model: "deepseek-v4.1-flash:cloud", effort: "medium", date: 1793900000.0, tokens: 100))
        XCTAssertEqual(rows[1].state, "corrupta")
        XCTAssertNil(rows[1].cwd)
        XCTAssertEqual(decoded[9], .models([ModelInfo(name: "deepseek-v4.1-flash:cloud"), ModelInfo(name: "llama3.2:3b")]))
    }

    func testDecodesTheDailyLimitOfAQuota() {
        let quota = #"{"version":2,"tipo":"cuota","servicio":"alfalfa","cuotas":[{"proveedor":"ollama","#
            + #""nombre":"Modelos de Ollama","usado_pct":15,"renueva":"2026-12-01","#
            + #""dia":{"usado_pct":90,"renueva":"2026-11-09T00:00:00+00:00"}}]}"#
        let daily = QuotaWindow(usedPercent: 90, renews: "2026-11-09T00:00:00+00:00")
        let expected = Quota(provider: "ollama", label: "Modelos de Ollama", usedPercent: 15, renews: "2026-12-01",
                             daily: daily)
        XCTAssertEqual(AlfalfaMessage.decode(line: quota), .quota([expected]))
        XCTAssertEqual(expected.tightestPercent, 90)
        XCTAssertNil(Quota(provider: "openai", usedPercent: 3).daily)
    }

    func testDecodesProvidersModelsAndQuota() {
        let models = #"{"version":2,"tipo":"modelos","modelos":[{"name":"glm-5.3-flash:cloud","proveedor":"ollama","nombre":"glm-5.3-flash:cloud"},{"name":"alfalfa/gpt-6-luna","proveedor":"alfalfa","nombre":"GPT-6 Luna - Alfalfa"}],"fallos":{"openai":"No se pudo consultar OpenAI."}}"#
        XCTAssertEqual(AlfalfaMessage.decode(line: models), .models([
            ModelInfo(name: "glm-5.3-flash:cloud"),
            ModelInfo(name: "alfalfa/gpt-6-luna", label: "GPT-6 Luna - Alfalfa", provider: "alfalfa")
        ]))
        let providers = #"{"version":2,"tipo":"proveedores","por_defecto":"openai","proveedores":[{"id":"ollama","nombre":"Ollama","configurado":true,"url":"http://127.0.0.1:11434","clave":false},{"id":"openai","nombre":"OpenAI","configurado":true,"clave":true}]}"#
        XCTAssertEqual(AlfalfaMessage.decode(line: providers), .providers(defaultProvider: "openai", providers: [
            ProviderStatus(id: "ollama", name: "Ollama", configured: true, url: "http://127.0.0.1:11434"),
            ProviderStatus(id: "openai", name: "OpenAI", configured: true, hasKey: true)
        ]))
        let quota = #"{"version":2,"tipo":"cuota","servicio":"alfalfa","cuotas":[{"proveedor":"openai","nombre":"GPT-6 Luna","usado_pct":42.5,"renueva":"2026-12-01"}]}"#
        XCTAssertEqual(AlfalfaMessage.decode(line: quota), .quota([Quota(provider: "openai", label: "GPT-6 Luna",
                                                                         usedPercent: 42.5, renews: "2026-12-01")]))
        let activated = #"{"version":2,"tipo":"activado","servicio":"alfalfa","nombre":"Ana","modelos":["gpt-6-luna"],"cuotas":[]}"#
        XCTAssertEqual(AlfalfaMessage.decode(line: activated), .activated(name: "Ana", quotas: []))
        let sent = #"{"version":2,"tipo":"reporte","enviado":true,"id":7,"soporte":"ayuda@example.com"}"#
        XCTAssertEqual(AlfalfaMessage.decode(line: sent),
                       .reported(ReportResult(sent: true, id: 7, support: "ayuda@example.com")))
        let notSent = #"{"version":2,"tipo":"reporte","enviado":false,"soporte":"","#
            + #""motivo":"No hay servicio Alfalfa configurado."}"#
        XCTAssertEqual(AlfalfaMessage.decode(line: notSent),
                       .reported(ReportResult(sent: false, reason: "No hay servicio Alfalfa configurado.")))
        XCTAssertNil(AlfalfaMessage.decode(line: #"{"version":2,"tipo":"reporte"}"#))
        let search = #"{"version":2,"tipo":"buscador","modo":"searxng","searxng_url":"http://127.0.0.1:9999","#
            + #""remoto":false,"libreria":true,"ollama_clave":true}"#
        XCTAssertEqual(AlfalfaMessage.decode(line: search),
                       .webSearch(WebSearchSettings(mode: "searxng", searxngURL: "http://127.0.0.1:9999",
                                                    hasOllamaKey: true)))
        XCTAssertNil(AlfalfaMessage.decode(line: #"{"version":2,"tipo":"buscador","modo":"google"}"#))
        XCTAssertEqual(WebSearchSettings.modes.map(WebSearchSettings.label(of:)),
                       ["Automático", "Ollama Web Search", "SearXNG", "Apagado"])
        var timeline = Timeline()
        timeline.apply(.quota([Quota(provider: "ollama", usedPercent: 10)]))
        XCTAssertEqual(timeline.quotas.first?.usedPercent, 10)
    }

    func testDecodesOllamaChats() {
        let line = #"{"version":2,"tipo":"chats_ollama","chats":[{"id":"c1","titulo":"Plan del juego","mensajes":7,"modelos":["glm-5.3-flash:cloud"],"fecha":1793534405.0},{"id":"c2"}]}"#
        guard case let .ollamaChats(chats)? = AlfalfaMessage.decode(line: line) else { return XCTFail("no decodifica") }
        XCTAssertEqual(chats, [OllamaChat(id: "c1", title: "Plan del juego", messages: 7,
                                          models: ["glm-5.3-flash:cloud"], date: 1793534405)])
        XCTAssertTrue(chats[0].matches("glm"))
        XCTAssertTrue(chats[0].matches("juego"))
        XCTAssertFalse(chats[0].matches("receta"))
    }

    func testSettingsAndBreakdown() {
        XCTAssertEqual(AlfalfaCommand.settings(model: "glm-5.3-flash:cloud", effort: "high").line.trimmingCharacters(in: .newlines),
                       #"{"esfuerzo":"high","modelo":"glm-5.3-flash:cloud","tipo":"ajustes"}"#)
        XCTAssertEqual(AlfalfaCommand.settings(model: nil, effort: "low").line.trimmingCharacters(in: .newlines), #"{"esfuerzo":"low","tipo":"ajustes"}"#)
        let line = #"{"version":2,"tipo":"contexto","tokens":1000,"limite":8000,"desglose":{"sistema":100,"herramientas":200,"mcp":0,"mensajes":700}}"#
        XCTAssertEqual(AlfalfaMessage.decode(line: line),
                       .context(tokens: 1000, limit: 8000, breakdown: ContextBreakdown(system: 100, tools: 200, mcp: 0, messages: 700)))
    }

    func testDecodesWrapUp() {
        let line = #"{"version":2,"tipo":"turno_terminado","estado":"completed","resumen":"Listo","codigo_salida":0,"tokens":5,"cierre":{"resumen":"Arreglé la suma.","pregunta":"¿Hago commit?","opciones":["Sí, haz commit","Todavía no"]}}"#
        XCTAssertEqual(AlfalfaMessage.decode(line: line),
                       .turnFinished(Outcome(state: "completed", summary: "Listo", exitCode: 0, tokens: 5,
                                             wrapUp: WrapUp(recap: "Arreglé la suma.", ask: "¿Hago commit?",
                                                            options: ["Sí, haz commit", "Todavía no"]))))
        // Sin pregunta no hay botones; un cierre sin resumen se ignora.
        let noAsk = #"{"version":2,"tipo":"turno_terminado","estado":"completed","codigo_salida":0,"cierre":{"resumen":"Listo.","opciones":["Sí"]}}"#
        XCTAssertEqual(AlfalfaMessage.decode(line: noAsk),
                       .turnFinished(Outcome(state: "completed", summary: "", exitCode: 0, tokens: 0,
                                             wrapUp: WrapUp(recap: "Listo."))))
        let empty = #"{"version":2,"tipo":"turno_terminado","estado":"completed","codigo_salida":0,"cierre":{"pregunta":"¿Sigo?"}}"#
        XCTAssertEqual(AlfalfaMessage.decode(line: empty),
                       .turnFinished(Outcome(state: "completed", summary: "", exitCode: 0, tokens: 0)))
    }

    func testDecodesUpdate() {
        let found = #"{"version":2,"tipo":"actualizacion","instalada":"0.1.0","ultima":"0.2.0","etiqueta":"v0.2.0","hay_nueva":true,"actualizado":false,"app_mac_cambio":false,"texto":"Hay una versión nueva"}"#
        XCTAssertEqual(AlfalfaMessage.decode(line: found),
                       .update(UpdateInfo(installed: "0.1.0", latest: "0.2.0", available: true, updated: false,
                                          macAppChanged: false, text: "Hay una versión nueva")))
        let none = #"{"version":2,"tipo":"actualizacion","instalada":"0.1.0","ultima":null,"hay_nueva":false,"texto":"Sin versiones"}"#
        XCTAssertEqual(AlfalfaMessage.decode(line: none),
                       .update(UpdateInfo(installed: "0.1.0", latest: nil, available: false, updated: false,
                                          macAppChanged: false, text: "Sin versiones")))
        // La app empaquetada se actualiza descargando el DMG: el motor dice dónde.
        let app = #"{"version":2,"tipo":"actualizacion","instalada":"0.1.0","ultima":"0.2.0","hay_nueva":true,"instalacion":"app","descarga":"https://github.com/0sw2ld0/AlfalfaCode/releases/latest","texto":"Descárgala"}"#
        guard case let .update(info)? = AlfalfaMessage.decode(line: app) else { return XCTFail("no es una actualización") }
        XCTAssertTrue(info.needsDownload)
        XCTAssertEqual(info.downloadURL?.absoluteString, "https://github.com/0sw2ld0/AlfalfaCode/releases/latest")
        // Un enlace que no es https no se abre.
        let http = app.replacingOccurrences(of: "https://", with: "http://")
        guard case let .update(plain)? = AlfalfaMessage.decode(line: http) else { return XCTFail("no es una actualización") }
        XCTAssertNil(plain.downloadURL)
        // hay_nueva tiene que ser un booleano.
        XCTAssertNil(AlfalfaMessage.decode(line: #"{"version":2,"tipo":"actualizacion","instalada":"0.1.0","hay_nueva":1}"#))
    }

    func testUnknownTypeIsIgnored() throws {
        let lines = try fixtureLines("salidas")
        XCTAssertNil(AlfalfaMessage.decode(line: lines[10]))
        var timeline = Timeline()
        for line in lines.prefix(1) + lines.suffix(1) { if let m = AlfalfaMessage.decode(line: line) { timeline.apply(m) } }
        XCTAssertEqual(timeline.items, [])
        XCTAssertEqual(timeline.status, .working)
    }

    func testRejectsInvalidLines() {
        XCTAssertNil(AlfalfaMessage.decode(line: "no es json"))
        XCTAssertNil(AlfalfaMessage.decode(line: "[1,2]"))
        XCTAssertNil(AlfalfaMessage.decode(line: #"{"tipo":"fin","estado":"completed"}"#))
        XCTAssertNil(AlfalfaMessage.decode(line: #"{"version":1,"tipo":"fin","estado":"completed"}"#))
        XCTAssertNil(AlfalfaMessage.decode(line: #"{"version":2,"tipo":"permiso","id":"p"}"#))
        XCTAssertNil(AlfalfaMessage.decode(line: #"{"version":2,"tipo":"permiso_resuelto","id":"p","aprobado":1}"#))
    }

    func testCommandsAreOneJSONLinePerMessage() {
        XCTAssertEqual(AlfalfaCommand.permission(id: "p1", approved: true).line, #"{"aprobado":true,"id":"p1","tipo":"permiso"}"# + "\n")
        XCTAssertEqual(AlfalfaCommand.permission(id: "p2", approved: false).line, #"{"aprobado":false,"id":"p2","tipo":"permiso"}"# + "\n")
        XCTAssertEqual(AlfalfaCommand.message("Hola \"mundo\"\ncon salto").line,
                       #"{"texto":"Hola \"mundo\"\ncon salto","tipo":"mensaje"}"# + "\n")
        XCTAssertEqual(AlfalfaCommand.cancel.line, #"{"tipo":"cancelar"}"# + "\n")
        XCTAssertEqual(AlfalfaCommand.message("a\nb").line.filter { $0 == "\n" }.count, 1)
    }

    func testLauncherArguments() {
        XCTAssertEqual(Launcher.stdioArguments(task: "-x arregla", resume: nil, model: "m", effort: "high", readOnly: true),
                       ["--stdio", "--modelo", "m", "--esfuerzo", "high", "--solo-lectura", "--", "-x arregla"])
        XCTAssertEqual(Launcher.stdioArguments(task: nil, resume: "abc", model: nil, effort: nil, readOnly: false),
                       ["--stdio", "--reanudar", "abc"])
    }

    func testRedactorHidesOllamaKey() {
        let env = ["OLLAMA_API_KEY": "sk-secreto"]
        XCTAssertEqual(Redactor.clean("clave sk-secreto fin", environment: env), "clave [REDACTADO] fin")
        XCTAssertEqual(Redactor.clean("sin clave", environment: [:]), "sin clave")
        let both = ["OLLAMA_API_KEY": "ollama-secreto", "OPENAI_API_KEY": "sk-openai-secreto"]
        XCTAssertEqual(Redactor.clean("a ollama-secreto b sk-openai-secreto", environment: both),
                       "a [REDACTADO] b [REDACTADO]")
    }
}
