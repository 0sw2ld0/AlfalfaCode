import XCTest
@testable import AlfalfaKit

final class TimelineTests: XCTestCase {
    private func replay(_ name: String) throws -> Timeline {
        var timeline = Timeline()
        for line in try fixtureLines(name) { if let m = AlfalfaMessage.decode(line: line) { timeline.apply(m) } }
        return timeline
    }

    func testGroupsEventsAndShowsUserAgentAndPermission() throws {
        var timeline = Timeline()
        timeline.addUser("Corrige suma.py", queued: false)
        for line in try fixtureLines("sesion_con_permisos") { if let m = AlfalfaMessage.decode(line: line) { timeline.apply(m) } }
        guard case let .events(_, events) = timeline.items[1] else { return XCTFail("eventos") }
        XCTAssertEqual(events.count, 2)
        XCTAssertEqual(events[0].title, "read_file · {'path': 'suma.py'}")
        guard case let .agent(_, text) = timeline.items[2] else { return XCTFail("agente") }
        XCTAssertEqual(text, "El error está en la línea 2.")
        guard case let .permission(_, request, state) = timeline.items[3] else { return XCTFail("permiso") }
        XCTAssertEqual(request.command, "pytest -q")
        XCTAssertEqual(state, .denied("Respuesta del cliente"))
        XCTAssertEqual(timeline.status, .idle)
        XCTAssertEqual(timeline.contextFraction, 0.25)
        XCTAssertEqual(timeline.totalTokens, 321)
    }

    func testPermissionStaysPendingWithoutUserAnswer() {
        var timeline = Timeline()
        timeline.apply(.permission(PermissionRequest(id: "p1", argv: ["rm", "x"], purpose: "")))
        XCTAssertEqual(timeline.status, .waitingPermission)
        XCTAssertEqual(timeline.pendingPermissions.map(\.id), ["p1"])
        // La línea JSONL de aprobación sólo existe si alguien construye el comando.
        timeline.apply(.event(AgentEvent(role: "agent", text: "sigo", time: 0)))
        XCTAssertEqual(timeline.pendingPermissions.count, 1)
        timeline.processExited(code: 0)
        XCTAssertEqual(timeline.pendingPermissions, [])
        guard case let .permission(_, _, state) = timeline.items[0] else { return XCTFail() }
        XCTAssertEqual(state, .denied("Sesión terminada"))
    }

    func testQueuedUserMessageBecomesDeliveredOnEcho() {
        var timeline = Timeline()
        timeline.addUser("segundo", queued: true)
        timeline.apply(.event(AgentEvent(role: "user", text: "segundo", time: 1)))
        XCTAssertEqual(timeline.items.count, 1)
        guard case let .user(_, text, queued) = timeline.items[0] else { return XCTFail() }
        XCTAssertEqual(text, "segundo")
        XCTAssertFalse(queued)
    }

    func testFinWithExit130IsInterrupted() throws {
        var timeline = Timeline()
        timeline.apply(.session(SessionInfo(id: "a", cwd: "/", model: "m", effort: "low")))
        timeline.apply(.finished(Outcome(state: "interrupted", summary: "", exitCode: 130, tokens: 1)))
        XCTAssertEqual(timeline.status, .interrupted)
        XCTAssertEqual(timeline.status.label, "Interrumpida · se puede reanudar")
        timeline.processExited(code: 130)
        XCTAssertEqual(timeline.status, .interrupted)
    }

    func testUnexpectedExitWithoutFinIsInterrupted() {
        var timeline = Timeline()
        timeline.apply(.session(SessionInfo(id: "a", cwd: "/", model: "m", effort: "low")))
        timeline.processExited(code: 1)
        XCTAssertEqual(timeline.status, .interrupted)
        var killed = Timeline()
        killed.processExited(code: 137)
        XCTAssertEqual(killed.status, .interrupted)
    }

    func testExit130EvenAfterTurnFinishedIsInterrupted() {
        var timeline = Timeline()
        timeline.apply(.turnFinished(Outcome(state: "completed", summary: "", exitCode: 0, tokens: 1)))
        XCTAssertEqual(timeline.status, .idle)
        timeline.processExited(code: 130)
        XCTAssertEqual(timeline.status, .interrupted)
    }

    func testCleanFinishStaysCompleted() {
        var timeline = Timeline()
        timeline.apply(.finished(Outcome(state: "completed", summary: "ok", exitCode: 0, tokens: 5)))
        timeline.processExited(code: 0)
        XCTAssertEqual(timeline.status, .completed)
    }

    func testFailedFinishKeepsFailure() {
        var timeline = Timeline()
        timeline.apply(.finished(Outcome(state: "failed", summary: "Fallo", exitCode: 1, tokens: 5)))
        timeline.processExited(code: 1)
        XCTAssertEqual(timeline.status, .failed("Fallo"))
    }

    func testMessageAfterIdleResumesWorking() {
        var timeline = Timeline()
        timeline.apply(.turnFinished(Outcome(state: "completed", summary: "", exitCode: 0, tokens: 1)))
        timeline.apply(.event(AgentEvent(role: "user", text: "más", time: 1)))
        XCTAssertEqual(timeline.status, .working)
    }
}

final class RealSessionTests: XCTestCase {
    /// Grabación real de `alfalfa --stdio` con deepseek-v4.1-flash:cloud: un permiso aprobado,
    /// uno negado y un mensaje enviado durante el trabajo.
    func testReplayOfRealRecording() throws {
        var timeline = Timeline()
        let lines = try fixtureLines("sesion_real")
        for line in lines { XCTAssertNotNil(AlfalfaMessage.decode(line: line), line) ; if let m = AlfalfaMessage.decode(line: line) { timeline.apply(m) } }
        let states = timeline.items.compactMap { item -> PermissionState? in
            if case let .permission(_, _, state) = item { return state } else { return nil }
        }
        XCTAssertEqual(states, [.approved, .denied("Respuesta del cliente")])
        XCTAssertTrue(timeline.items.contains { if case .user(_, "Responde en español y sé breve.", false) = $0 { return true } else { return false } })
        XCTAssertEqual(timeline.contextLimit, 1048576)
        timeline.processExited(code: 1)
        // El fin de la grabación es «blocked» (se negó un permiso): bloqueada, no fallida.
        XCTAssertEqual(timeline.status, .blocked)
    }

    /// Negar un permiso acaba el turno como «blocked» (código 1), pero el proceso sigue vivo.
    func testBlockedTurnKeepsComposerActiveWhileProcessLives() throws {
        var timeline = Timeline()
        for line in try fixtureLines("sesion_real") {
            guard let message = AlfalfaMessage.decode(line: line) else { continue }
            if case .finished = message { break }
            timeline.apply(message)
        }
        XCTAssertEqual(timeline.status, .blocked)
        XCTAssertEqual(timeline.status.label, "bloqueada")
        XCTAssertTrue(timeline.status.isActive)
        // Un mensaje nuevo reanuda el trabajo.
        timeline.apply(.event(AgentEvent(role: "user", text: "otra cosa", time: 1)))
        XCTAssertEqual(timeline.status, .working)
    }

    func testFailedTurnWithLiveProcessStaysActive() {
        var timeline = Timeline()
        timeline.apply(.turnFinished(Outcome(state: "failed", summary: "Fallo", exitCode: 1, tokens: 1)))
        XCTAssertTrue(timeline.status.isActive)
    }

    func testSummaryShownOnceWhenAgentAlreadyPrintedIt() throws {
        var timeline = try replayAll("sesion_real")
        timeline.processExited(code: 1)
        guard case let .finished(outcome)? = try fixtureLines("sesion_real").compactMap(AlfalfaMessage.decode(line:)).last else {
            return XCTFail("fin")
        }
        let shown = timeline.items.filter { item in
            switch item {
            case let .agent(_, text), let .notice(_, text): return text == outcome.summary
            default: return false
            }
        }
        XCTAssertEqual(shown.count, 1)
    }

    func testSummaryIsStillShownWhenAgentDidNotSayIt() {
        var timeline = Timeline()
        timeline.apply(.turnFinished(Outcome(state: "failed", summary: "Sin texto previo", exitCode: 1, tokens: 1)))
        XCTAssertEqual(timeline.items.count, 1)
    }

    private func replayAll(_ name: String) throws -> Timeline {
        var timeline = Timeline()
        for line in try fixtureLines(name) { if let m = AlfalfaMessage.decode(line: line) { timeline.apply(m) } }
        return timeline
    }
}

final class MarkdownTextTests: XCTestCase {
    func testBlocksCoverParagraphsListsAndCode() {
        let text = "Resultado:\n\n- **Corrección** en `suma.py`\n* otra\n1. primero\n2) segundo\n```\nreturn a + b\n```\nFin"
        XCTAssertEqual(MarkdownText.blocks(text), [
            .paragraph("Resultado:"),
            .bullet("**Corrección** en `suma.py`"),
            .bullet("otra"),
            .numbered(1, "primero"),
            .numbered(2, "segundo"),
            .code("return a + b"),
            .paragraph("Fin"),
        ])
    }

    func testInlineBoldAndCode() {
        let attributed = MarkdownText.inline("Usa **negrita** y `código`")
        XCTAssertEqual(String(attributed.characters), "Usa negrita y código")
        let bold = attributed.runs.filter { $0.inlinePresentationIntent?.contains(.stronglyEmphasized) == true }
        XCTAssertEqual(bold.map { String(attributed[$0.range].characters) }, ["negrita"])
        let code = attributed.runs.filter { $0.inlinePresentationIntent?.contains(.code) == true }
        XCTAssertEqual(code.map { String(attributed[$0.range].characters) }, ["código"])
    }

    func testHeadingsQuotesRulesAndTasks() {
        let text = "## 2. El motor valida\nTexto\n\n> cita uno\n> cita dos\n\n---\n- [ ] pendiente\n- [x] hecho\n  - anidado\n    sigue aquí\n### Fin ###"
        XCTAssertEqual(MarkdownText.blocks(text), [
            .heading(2, "2. El motor valida"),
            .paragraph("Texto"),
            .quote("cita uno\ncita dos"),
            .rule,
            .task(false, "pendiente"),
            .task(true, "hecho"),
            .bullet("anidado sigue aquí", level: 1),
            .heading(3, "Fin"),
        ])
    }

    func testTablesAndCodeLanguage() {
        let text = "| Cosa | Dónde |\n|---|:---:|\n| Romance | `Engine.swift:343` |\n| a \\| b |\n\n```swift\nlet x = 1\n```"
        XCTAssertEqual(MarkdownText.blocks(text), [
            .table(header: ["Cosa", "Dónde"], rows: [["Romance", "`Engine.swift:343`"], ["a | b", ""]]),
            .code("let x = 1", language: "swift"),
        ])
    }

    func testTextThatIsNotBlockSyntaxStaysParagraph() {
        XCTAssertEqual(MarkdownText.blocks("#sin espacio\n*sonríe* y sigue\na | b sin separador"),
                       [.paragraph("#sin espacio\n*sonríe* y sigue\na | b sin separador")])
    }

    func testOnlyARunningTurnCanBeStopped() {
        XCTAssertTrue(SessionStatus.working.isRunning)
        XCTAssertTrue(SessionStatus.waitingPermission.isRunning)
        XCTAssertFalse(SessionStatus.idle.isRunning)
        XCTAssertTrue(SessionStatus.idle.isActive)
    }

    func testRestoredHistoryIsReadyToContinue() {
        let line = #"{"version":2,"tipo":"historial","sesion":"abc","eventos":[{"rol":"user","texto":"Hola","hora":1},{"rol":"agent","texto":"Listo","hora":2}],"cierre":{"resumen":"Hecho.","pregunta":"¿Sigo?","opciones":["Sí"]}}"#
        guard case let .history("abc", events, wrapUp, _, _)? = AlfalfaMessage.decode(line: line) else { return XCTFail("no decodifica") }
        var timeline = Timeline(history: events, wrapUp: wrapUp)
        XCTAssertEqual(timeline.status, .idle)
        XCTAssertEqual(timeline.items.count, 3)
        guard case .wrapUp(_, _, false)? = timeline.items.last else { return XCTFail("falta el cierre") }
        timeline.restart()
        XCTAssertEqual(timeline.status, .starting)
    }

    func testFinishedBlockedSessionIsNotFailed() {
        var timeline = Timeline()
        timeline.apply(.finished(Outcome(state: "blocked", summary: "Operaciones sin resolver: {}", exitCode: 1, tokens: 1)))
        XCTAssertEqual(timeline.status, .blocked)
        var failed = Timeline()
        failed.apply(.finished(Outcome(state: "failed", summary: "Fallo", exitCode: 1, tokens: 1)))
        XCTAssertEqual(failed.status, .failed("Fallo"))
    }

    func testBlockedSummariesAreReadable() {
        XCTAssertEqual(BlockedSummary.readable(#"Operaciones sin resolver: {"finding-000008": "La operación terminó con código 1."}"#),
                       "Quedó una operación sin resolver: La operación terminó con código 1. Pide al agente que la revise o la repita.")
        XCTAssertEqual(BlockedSummary.readable(#"Verificación pendiente: {"status": "stale", "checks": []}"#),
                       "Hubo cambios después de la última comprobación; están sin verificar.")
        // Recortado a 400 caracteres: JSON roto, se deja tal cual.
        XCTAssertEqual(BlockedSummary.readable(#"Operaciones sin resolver: {"a": "b"#), #"Operaciones sin resolver: {"a": "b"#)
        XCTAssertEqual(BlockedSummary.readable("Fallo"), "Fallo")
    }

    func testUserMessagesKnowTheirTurn() {
        // Historial: cada evento trae su turno.
        let saved = Timeline(history: [AgentEvent(role: "user", text: "Uno", time: 10, turn: "t1"),
                                       AgentEvent(role: "agent", text: "A", time: 11, turn: "t1"),
                                       AgentEvent(role: "user", text: "Dos", time: 20, turn: "t2")], wrapUp: nil)
        let users = saved.items.compactMap { if case let .user(id, _, _) = $0 { return id } else { return nil } }
        XCTAssertEqual(users.map { saved.userTurns[$0] }, ["t1", "t2"])
        XCTAssertEqual(saved.userTimes[users[1]], 20)
        // Volver al primero es empezar de cero; al segundo, bifurcar hasta el primero.
        XCTAssertEqual(saved.turnBefore(user: users[0]), .some(nil))
        XCTAssertEqual(saved.turnBefore(user: users[1]), .some("t1"))
        // En vivo: el turno llega con turno_terminado y no toca lo que sigue en cola.
        var live = Timeline()
        live.addUser("Hola", queued: false)
        live.addUser("Luego", queued: true)
        live.apply(.turnFinished(Outcome(state: "completed", summary: "", exitCode: 0, tokens: 1, turn: "t9")))
        let ids = live.items.compactMap { if case let .user(id, _, _) = $0 { return id } else { return nil } }
        XCTAssertEqual(live.userTurns[ids[0]], "t9")
        XCTAssertNil(live.userTurns[ids[1]])
        XCTAssertNotNil(live.userTimes[ids[0]])
    }

    func testApprovedCommandRunsUntilItsResult() {
        var timeline = Timeline()
        timeline.apply(.permission(PermissionRequest(id: "p1", argv: ["tools/abrir.sh"], purpose: "Abrir")))
        XCTAssertNil(timeline.runningPermission)
        timeline.apply(.permissionResolved(id: "p1", approved: true, reason: "Respuesta del cliente"))
        XCTAssertEqual(timeline.runningPermission?.id, "p1")
        timeline.apply(.event(AgentEvent(role: "tool", text: "Inicio run_command · {}", time: 1)))
        XCTAssertEqual(timeline.runningPermission?.id, "p1")
        timeline.apply(.event(AgentEvent(role: "tool", text: "run_command {\"exit_code\": 0}", time: 2)))
        XCTAssertNil(timeline.runningPermission)
        timeline.apply(.permission(PermissionRequest(id: "p2", argv: ["rm", "-rf", "x"], purpose: "Borrar")))
        timeline.apply(.permissionResolved(id: "p2", approved: false, reason: "Negado"))
        XCTAssertNil(timeline.runningPermission)
    }

    func testWrapUpClosesWhenTheUserAnswers() {
        var timeline = Timeline()
        let wrapUp = WrapUp(recap: "Listo.", ask: "¿Sigo?", options: ["Sí"])
        timeline.apply(.turnFinished(Outcome(state: "completed", summary: "", exitCode: 0, tokens: 1, wrapUp: wrapUp)))
        guard case let .wrapUp(id, shown, false)? = timeline.items.last else { return XCTFail("falta el cierre") }
        XCTAssertEqual(shown, wrapUp)
        XCTAssertEqual(timeline.status, .idle)
        timeline.addUser("Sí", queued: true)
        XCTAssertTrue(timeline.items.contains(.wrapUp(id: id, wrapUp, answered: true)))
        timeline.dismissWrapUp(id: id)
        XCTAssertFalse(timeline.items.contains { if case .wrapUp = $0 { return true } else { return false } })
    }

    func testUnclosedCodeFenceKeepsContent() {
        XCTAssertEqual(MarkdownText.blocks("```\nx = 1"), [.code("x = 1")])
    }
}
