@testable import AlfalfaKit
import XCTest

/// Las llamadas seguidas se juntan en un grupo con su resumen; cada fila lleva la descripción
/// del motor y, al abrirla, el comando y su salida.
final class ToolCallsTests: XCTestCase {
    private func line(_ object: [String: Any]) -> String {
        var row = object
        row["version"] = protocolVersion
        row["tipo"] = "evento"
        let data = try! JSONSerialization.data(withJSONObject: row)
        return String(data: data, encoding: .utf8)!
    }

    private func apply(_ timeline: inout Timeline, _ rows: [[String: Any]]) {
        for row in rows {
            guard let message = AlfalfaMessage.decode(line: line(row)) else { return XCTFail("no se leyó \(row)") }
            timeline.apply(message)
        }
    }

    private func start(_ name: String, _ description: String, command: String? = nil,
                       target: String? = nil) -> [String: Any] {
        var tool: [String: Any] = ["nombre": name, "fase": "inicio", "descripcion": description]
        if let command { tool["orden"] = command }
        if let target { tool["objetivo"] = target }
        return ["rol": "tool", "texto": "Inicio \(name) · {}", "hora": 1, "herramienta": tool]
    }

    private func end(_ name: String, _ result: String, code: Int? = nil, disposition: String? = nil) -> [String: Any] {
        var tool: [String: Any] = ["nombre": name, "fase": "fin"]
        if let code { tool["codigo"] = code }
        if let disposition { tool["clasificacion"] = ["disposition": disposition, "status": "x"] }
        return ["rol": "tool", "texto": name + "\n" + result, "hora": 2, "herramienta": tool]
    }

    func testDecodesToolInfo() {
        let message = AlfalfaMessage.decode(line: line(start("run_command", "Ver qué cambió", command: "git status")))
        guard case let .event(event)? = message else { return XCTFail("evento") }
        XCTAssertEqual(event.tool, ToolInfo(name: "run_command", phase: .start, description: "Ver qué cambió",
                                            command: "git status"))
        // Sin `herramienta` (motores anteriores) el evento sigue llegando.
        let old = AlfalfaMessage.decode(line: line(["rol": "tool", "texto": "Inicio read_file · {'path': 'a.py'}"]))
        guard case let .event(plain)? = old else { return XCTFail("evento") }
        XCTAssertNil(plain.tool)
    }

    func testGroupsCallsWithSummaryDescriptionsCommandAndOutput() {
        var timeline = Timeline()
        timeline.addUser("Arregla la suma", queued: false)
        apply(&timeline, [
            start("run_command", "Buscar dónde se guarda session.json", command: "rg -n session.json alfalfa"),
            end("run_command", #"{"output": "alfalfa/runtime.py:12: session.json\n", "exit_code": 0, "argv": ["rg"]}"#,
                code: 0),
            start("read_file", "Leer runtime.py", target: "alfalfa/runtime.py"),
            end("read_file", #"{"path": "alfalfa/runtime.py", "text": "x = 1\n", "sha256": "abc"}"#),
            start("run_command", "Pasar las pruebas", command: "pytest -q"),
            end("run_command", #"{"output": "1 failed", "exit_code": 1}"#, code: 1),
            start("edit_file", "Editar runtime.py", target: "alfalfa/runtime.py"),
            end("edit_file", #"{"ok": true}"#),
            start("read_file", "Leer runtime.py", target: "alfalfa/runtime.py"),
            end("read_file", #"{"text": "x = 2\n"}"#),
        ])
        XCTAssertEqual(timeline.items.count, 2, "las llamadas seguidas van en un solo grupo")
        guard case let .events(_, events) = timeline.items[1] else { return XCTFail("grupo") }
        let calls = ToolCalls.calls(from: events)
        XCTAssertEqual(calls.map(\.description), ["Buscar dónde se guarda session.json", "Leer runtime.py",
                                                  "Pasar las pruebas", "Editar runtime.py", "Leer runtime.py"])
        XCTAssertEqual(ToolCalls.summary(calls), "Ejecutó 2 comandos, leyó 1 archivo, editó 1 archivo")
        XCTAssertEqual(calls[0].command, "rg -n session.json alfalfa")
        XCTAssertEqual(calls[0].output, "alfalfa/runtime.py:12: session.json\n")
        XCTAssertFalse(calls[0].failed)
        XCTAssertEqual(calls[1].output, "x = 1\n")
        XCTAssertEqual(calls[2].exitCode, 1)
        XCTAssertTrue(calls[2].failed)
        XCTAssertTrue(calls.allSatisfy(\.finished))
    }

    func testSummaryNamesEveryKindInOrder() {
        func call(_ name: String, _ target: String? = nil) -> ToolCall {
            ToolCall(id: Int.random(in: 0...1_000_000), name: name, kind: ToolKind(tool: name), description: "",
                     target: target)
        }
        XCTAssertEqual(ToolCalls.summary([call("run_command")]), "Ejecutó 1 comando")
        XCTAssertEqual(ToolCalls.summary([call("read_file", "a"), call("read_file", "b"), call("search"),
                                          call("run_checks"), call("apply_patch"), call("web_search")]),
                       "Leyó 2 archivos, hizo 1 búsqueda, ejecutó 1 comando, editó 1 archivo, consultó la web")
        XCTAssertEqual(ToolCalls.summary([call("buscar_herramientas"), call("spawn_agent"), call("spawn_agent"),
                                          call("mcp__github__issue")]),
                       "Cargó herramientas, lanzó 2 subagentes, usó 1 herramienta")
        XCTAssertEqual(ToolCalls.summary([]), "")
    }

    func testRunningCallHasNoResultYet() {
        var timeline = Timeline()
        apply(&timeline, [
            start("read_file", "Leer a.py"), end("read_file", #"{"text": ""}"#),
            start("run_checks", "Pasar las pruebas del motor", command: "pytest"),
        ])
        guard case let .events(_, events) = timeline.items[0] else { return XCTFail("grupo") }
        XCTAssertEqual(ToolCalls.running(in: events)?.description, "Pasar las pruebas del motor")
        XCTAssertEqual(ToolCalls.summary(ToolCalls.calls(from: events)), "Leyó 1 archivo, ejecutó 1 comando")
    }

    func testResultAfterPermissionCardGoesBackToItsGroup() {
        var timeline = Timeline()
        apply(&timeline, [start("run_command", "Instalar dependencias", command: "npm install")])
        timeline.apply(.permission(PermissionRequest(id: "p1", argv: ["npm", "install"], purpose: "")))
        timeline.apply(.permissionResolved(id: "p1", approved: true, reason: "", session: false))
        apply(&timeline, [end("run_command", #"{"output": "added 3 packages", "exit_code": 0}"#, code: 0)])
        XCTAssertEqual(timeline.items.count, 2)
        guard case let .events(_, events) = timeline.items[0] else { return XCTFail("grupo") }
        let calls = ToolCalls.calls(from: events)
        XCTAssertEqual(calls.count, 1)
        XCTAssertEqual(calls[0].output, "added 3 packages")
        XCTAssertNil(ToolCalls.running(in: events))
        // La llamada siguiente, ya después de la tarjeta, abre otro grupo.
        apply(&timeline, [start("read_file", "Leer package.json")])
        XCTAssertEqual(timeline.items.count, 3)
    }

    func testAgentMessageClosesTheGroup() {
        var timeline = Timeline()
        apply(&timeline, [start("read_file", "Leer a.py"), end("read_file", "{}"),
                          ["rol": "agent", "texto": "Ya lo leí."], start("read_file", "Leer b.py")])
        XCTAssertEqual(timeline.items.count, 3)
    }

    func testSameAgentReplyTwiceInARowShowsOnce() {
        // Sesiones guardadas por motores anteriores: la respuesta repetida tras el recordatorio.
        var timeline = Timeline()
        apply(&timeline, [start("run_checks", "Pasar la prueba"), end("run_checks", #"{"exit_code": 1}"#, code: 1),
                          ["rol": "agent", "texto": "La prueba falla."], ["rol": "agent", "texto": "La prueba falla."]])
        XCTAssertEqual(timeline.items.count, 2)
        // Separadas por trabajo, o distintas, sí se enseñan las dos.
        apply(&timeline, [["rol": "agent", "texto": "Otra cosa."], start("read_file", "Leer a.py"),
                          ["rol": "agent", "texto": "Otra cosa."]])
        XCTAssertEqual(timeline.items.count, 5)
    }

    func testFallbackWithoutEngineDescription() {
        // Eventos de un motor anterior: sólo texto. La fila dice algo razonable.
        let events = [
            ToolEvent(id: 1, role: "tool", text: "Inicio read_file · {'path': 'alfalfa/runtime.py'}"),
            ToolEvent(id: 2, role: "tool", text: "read_file\n{\"text\": \"hola\"}"),
            ToolEvent(id: 3, role: "tool", text: "Inicio run_command · {'argv': ['git', 'status'], 'purpose': 'Ver el estado'}"),
            ToolEvent(id: 4, role: "tool",
                      text: "run_command\n{\"output\": \"limpio\", \"exit_code\": 0, \"argv\": [\"git\", \"status\"], \"purpose\": \"Ver el estado\"}"),
            ToolEvent(id: 5, role: "tool", text: "run_checks\n{\"error\": \"Permiso denegado\"}"),
            ToolEvent(id: 6, role: "system", text: "El proceso p1 terminó."),
        ]
        let entries = ToolCalls.entries(from: events)
        XCTAssertEqual(entries.count, 4)
        let calls = ToolCalls.calls(from: events)
        XCTAssertEqual(calls.map(\.description), ["Leer runtime.py", "Ver el estado", "Pasar una comprobación"])
        XCTAssertEqual(calls[1].command, "git status")
        XCTAssertEqual(calls[1].output, "limpio")
        XCTAssertTrue(calls[2].failed)
        XCTAssertEqual(calls[2].output, "Permiso denegado")
        guard case .note = entries[3] else { return XCTFail("aviso") }
    }

    func testHistoryKeepsToolInfo() {
        let row: [String: Any] = ["version": protocolVersion, "tipo": "historial", "sesion": "s1", "eventos": [
            ["rol": "tool", "texto": "Inicio run_command · {}", "hora": 1,
             "herramienta": ["nombre": "run_command", "fase": "inicio", "descripcion": "Ver qué cambió",
                             "orden": "git diff"]],
        ]]
        let text = String(data: try! JSONSerialization.data(withJSONObject: row), encoding: .utf8)!
        guard case let .history(_, events, _, _, _, _)? = AlfalfaMessage.decode(line: text) else {
            return XCTFail("historial")
        }
        let timeline = Timeline(history: events, wrapUp: nil)
        guard case let .events(_, grouped) = timeline.items[0] else { return XCTFail("grupo") }
        XCTAssertEqual(ToolCalls.calls(from: grouped).first?.command, "git diff")
        XCTAssertEqual(ToolCalls.calls(from: grouped).first?.description, "Ver qué cambió")
    }

    // MARK: - Revisión del PR

    func testCutTurnClosesTheOpenCallAndTheLateResultJoinsIt() {
        var timeline = Timeline()
        apply(&timeline, [["rol": "user", "texto": "Instala"], start("run_command", "Instalar", command: "npm install")])
        // «Enviar ahora»: el turno se corta sin el fin de la orden.
        timeline.apply(.turnFinished(Outcome(state: "interrupted", summary: "", exitCode: 0, tokens: 0)))
        guard case let .events(_, events) = timeline.items[1] else { return XCTFail("grupo") }
        let call = ToolCalls.calls(from: events)[0]
        XCTAssertTrue(call.finished && call.interrupted)
        XCTAssertNil(ToolCalls.running(in: events))
        // El turno siguiente empieza por el resultado «El proceso se interrumpió…» sin inicio.
        apply(&timeline, [["rol": "user", "texto": "Sigue"],
                          end("run_command", #"{"error": "El proceso se interrumpió durante esta herramienta."}"#)])
        XCTAssertEqual(timeline.items.count, 3, "el resultado no abre otro grupo")
        guard case let .events(_, joined) = timeline.items[1] else { return XCTFail("grupo") }
        let calls = ToolCalls.calls(from: joined)
        XCTAssertEqual(calls.count, 1)
        XCTAssertEqual(calls[0].description, "Instalar")
        XCTAssertFalse(calls[0].interrupted)
        XCTAssertEqual(calls[0].output, "El proceso se interrumpió durante esta herramienta.")
    }

    func testNewUserMessageAndSavedHistoryCloseOpenCalls() {
        var timeline = Timeline()
        apply(&timeline, [start("run_checks", "Pasar las pruebas"), ["rol": "user", "texto": "Para"]])
        guard case let .events(_, events) = timeline.items[0] else { return XCTFail("grupo") }
        XCTAssertNil(ToolCalls.running(in: events))
        XCTAssertTrue(ToolCalls.calls(from: events)[0].interrupted)
        let saved = Timeline(history: [AgentEvent(role: "tool", text: "Inicio read_file · {'path': 'a.py'}", time: 1)],
                             wrapUp: nil)
        guard case let .events(_, old) = saved.items[0] else { return XCTFail("grupo") }
        XCTAssertNil(ToolCalls.running(in: old))
    }

    func testSystemNoticeDoesNotSplitACallFromItsResult() {
        var timeline = Timeline()
        apply(&timeline, [start("run_command", "Instalar dependencias", command: "npm install")])
        timeline.apply(.permission(PermissionRequest(id: "p1", argv: ["npm", "install"], purpose: "")))
        timeline.apply(.permissionResolved(id: "p1", approved: true, reason: "", session: false))
        apply(&timeline, [["rol": "system", "texto": "El proceso p1 terminó."],
                          end("run_command", #"{"output": "ok", "exit_code": 0}"#, code: 0)])
        XCTAssertEqual(timeline.items.count, 2, "el aviso y el resultado van al grupo de la llamada")
        guard case let .events(_, events) = timeline.items[0] else { return XCTFail("grupo") }
        let entries = ToolCalls.entries(from: events)
        XCTAssertEqual(entries.count, 2)
        XCTAssertEqual(ToolCalls.calls(from: events).first?.output, "ok")
        XCTAssertNil(ToolCalls.running(in: events))
    }

    func testEngineClassificationDecidesFailure() {
        var timeline = Timeline()
        apply(&timeline, [
            start("run_command", "Comprobar que ya no queda ningún TODO", command: "rg TODO"),
            end("run_command", #"{"output": "", "exit_code": 1}"#, code: 1, disposition: "pass"),
            start("run_checks", "Pasar las pruebas", command: "pytest"),
            end("run_checks", #"{"output": "1 failed", "exit_code": 1}"#, code: 1, disposition: "failure"),
            start("run_command", "Ver el estado", command: "git status"),
            end("run_command", #"{"output": "", "exit_code": 0}"#, code: 0, disposition: "failure"),
        ])
        guard case let .events(_, events) = timeline.items[0] else { return XCTFail("grupo") }
        let calls = ToolCalls.calls(from: events)
        XCTAssertFalse(calls[0].failed)
        XCTAssertEqual(calls[0].exitCode, 1)
        XCTAssertTrue(calls[1].failed)
        XCTAssertTrue(calls[2].failed)
    }

    func testTruncatedOutputIsUnwrapped() {
        let inner = #"{"exit_code": 2, "output": "línea 1\nlínea \"2\"\n\u00e9xito"#
        let wrapped = try! JSONSerialization.data(withJSONObject: ["truncated": true, "text": inner])
        let events = [ToolEvent(id: 1, role: "tool", text: "Inicio run_command · {}",
                                tool: ToolInfo(name: "run_command", phase: .start, command: "make")),
                      ToolEvent(id: 2, role: "tool", text: "run_command\n" + String(data: wrapped, encoding: .utf8)!)]
        let call = ToolCalls.calls(from: events)[0]
        XCTAssertEqual(call.output, "línea 1\nlínea \"2\"\néxito\n…")
        XCTAssertEqual(call.exitCode, 2)
        XCTAssertTrue(call.failed)
    }

    func testPatchCountsItsFiles() {
        var timeline = Timeline()
        var patch = start("apply_patch", "Editar 2 archivos")
        var tool = patch["herramienta"] as! [String: Any]
        tool["objetivos"] = ["a.py", "b.py"]
        patch["herramienta"] = tool
        apply(&timeline, [patch, end("apply_patch", "{}"), start("edit_file", "Editar a.py", target: "a.py"),
                          end("edit_file", "{}")])
        guard case let .events(_, events) = timeline.items[0] else { return XCTFail("grupo") }
        XCTAssertEqual(ToolCalls.summary(ToolCalls.calls(from: events)), "Editó 2 archivos")
        // Motores anteriores: los archivos se leen del texto del parche.
        let old = [ToolEvent(id: 1, role: "tool",
                             text: "Inicio apply_patch · {'input': '*** Begin Patch\\n*** Update File: x.py\\n@@\\n*** Add File: y.py\\n+z\\n*** End Patch'}")]
        XCTAssertEqual(ToolCalls.calls(from: old)[0].targets, ["x.py", "y.py"])
    }
}
