@testable import AlfalfaKit
import XCTest

/// El aviso «Importar · … sin autorizar» de una sesión (`importadas` del evento): se reconoce por sus datos, con
/// `--stdio` v2, con PACAI y al releer la transcripción; la conversación lo pinta una vez, y la hoja «Revisar y
/// autorizar…» ofrece lo que sigue pendiente en la lista recién leída.
final class ImportNoticeTests: XCTestCase {

    // MARK: - Constantes

    private let text = "Importar · mcp docs (Codex), agente revisor (OpenCode), comando probar (OpenCode) sin autorizar: "
        + "no se activan hasta que los autorices con alfalfa --importar o en Ajustes ▸ Importar."
    private let rows: [[String: Any]] = [
        ["clave": "mcp:docs@/casa/.codex/config.toml", "huella": "0123456789abcdef", "tipo": "mcp", "nombre": "docs",
         "herramienta": "Codex", "estado": "pendiente"],
        ["clave": "agente:revisor@/p/opencode.json", "huella": "00112233445566aa", "tipo": "agente",
         "nombre": "revisor", "herramienta": "OpenCode", "estado": "pendiente"],
        ["clave": "comando:probar@/p/opencode.json", "huella": "fedcba9876543210", "tipo": "comando",
         "nombre": "probar", "herramienta": "OpenCode", "estado": "cambio"]]

    // MARK: - Métodos

    func testTheV2EventCarriesEachPendingItemWithItsFingerprint() throws {
        let notice = try XCTUnwrap(decodedV2().importNotice)
        XCTAssertEqual(notice.text, text)
        XCTAssertEqual(notice.pending.map(\.key), rows.map { $0["clave"] as? String })
        XCTAssertEqual(notice.pending.map(\.kind), [.mcp, .agente, .comando])
        XCTAssertEqual(notice.pending.map(\.fingerprint), ["0123456789abcdef", "00112233445566aa", "fedcba9876543210"])
        XCTAssertEqual(notice.pending.map(\.state), ["pendiente", "pendiente", "cambio"])
        XCTAssertEqual(notice.headline, "1 servidor MCP, 1 agente y 1 orden «/» de Codex y OpenCode sin autorizar")
        XCTAssertEqual(notice.names, "docs, revisor y /probar")
    }

    func testOnlyASystemEventWithAuthorizableRowsIsAnImportNotice() throws {
        XCTAssertNil(try decoded(["tipo": "evento", "rol": "system", "texto": "Hooks · aviso"]).importNotice)
        XCTAssertNil(try decoded(["tipo": "evento", "rol": "system", "texto": text,
                                  "importadas": ["pendientes": []]]).importNotice)
        XCTAssertNil(try decoded(["tipo": "evento", "rol": "system", "texto": text, "importadas": ["pendientes": [
            ["clave": "instrucciones:AGENTS.md@/p/AGENTS.md", "tipo": "instrucciones", "nombre": "AGENTS.md"],
            ["tipo": "mcp", "nombre": "sin-clave"]]]]).importNotice)
        XCTAssertNil(try decoded(["tipo": "evento", "rol": "agent", "texto": text,
                                  "importadas": ["pendientes": rows]]).importNotice)
    }

    func testTheHeadlineCountsAndTheNamesStopAtSix() {
        let servers = ["chiyo", "cuyimage", "cuycleaner", "cuyworld-atlas", "meshy", "notas", "git"].map {
            ImportNotice.Pending(key: "mcp:" + $0 + "@/casa/.codex/config.toml", fingerprint: "", kind: .mcp,
                                 name: $0, tool: "Codex")
        }
        XCTAssertEqual(ImportNotice(pending: Array(servers.prefix(5)), text: "").headline,
                       "5 servidores MCP de Codex sin autorizar")
        XCTAssertEqual(ImportNotice(pending: Array(servers.prefix(5)), text: "").names,
                       "chiyo, cuyimage, cuycleaner, cuyworld-atlas y meshy")
        XCTAssertEqual(ImportNotice(pending: servers, text: "").names,
                       "chiyo, cuyimage, cuycleaner, cuyworld-atlas, meshy, notas y 1 más")
        let one = ImportNotice.Pending(key: "comando:a@/p/x", fingerprint: "", kind: .comando, name: "a", tool: "")
        XCTAssertEqual(ImportNotice(pending: [one, one], text: "").headline, "2 órdenes «/» sin autorizar")
    }

    func testPacaiCarriesTheDataInTheMessageStartAndGivesTheSameEvent() throws {
        let client = PacaiClient()
        let imported: [String: Any] = ["pending": [
            ["key": "mcp:docs@/casa/.codex/config.toml", "fingerprint": "0123456789abcdef", "type": "mcp",
             "name": "docs", "tool": "Codex", "status": "pending"],
            ["key": "agente:revisor@/p/opencode.json", "fingerprint": "00112233445566aa", "type": "agent",
             "name": "revisor", "tool": "OpenCode", "status": "pending"],
            ["key": "comando:probar@/p/opencode.json", "fingerprint": "fedcba9876543210", "type": "command",
             "name": "probar", "tool": "OpenCode", "status": "changed"]]]
        var events = client.receive(agent("message_start", ["run_id": "m_run", "role": "system",
                                                            "alfalfa": ["turn": "t1", "imported": imported]])).events
        events += client.receive(agent("message_delta", ["message_id": "m_start",
                                                         "blocks": [["type": "text", "text": text]]],
                                       id: "m_delta")).events
        events += client.receive(agent("message_end", ["message_id": "m_start"], id: "m_end")).events
        guard case let .event(event)? = events.first, events.count == 1 else {
            return XCTFail("se esperaba un evento: \(events)")
        }
        var expected = try decodedV2()
        expected.turn = "t1"
        expected.time = event.time      // la del sobre
        XCTAssertEqual(event, expected)
        // Sin la extensión (otro cliente) el aviso es sólo texto.
        let plain = PacaiClient()
        _ = plain.receive(agent("message_start", ["run_id": "m_run", "role": "system"], id: "m_otro"))
        _ = plain.receive(agent("message_delta", ["message_id": "m_otro", "blocks": [["type": "text", "text": text]]],
                                id: "m_d"))
        guard case let .event(bare)? = plain.receive(agent("message_end", ["message_id": "m_otro"], id: "m_e"))
            .events.first else { return XCTFail("sin evento") }
        XCTAssertNil(bare.importNotice)
        XCTAssertEqual(bare.text, text)
    }

    func testTheSavedTranscriptKeepsTheNotice() throws {
        let row: [String: Any] = ["role": "system", "text": text, "time": 5.0, "turno": "t1",
                                  "importadas": ["pendientes": rows]]
        let line = String(decoding: try JSONSerialization.data(withJSONObject: row), as: UTF8.self)
        let event = try XCTUnwrap(SessionLog.event(fromLine: line))
        XCTAssertEqual(event.importNotice, try decodedV2().importNotice)
    }

    func testTheConversationShowsOneCardPerNoticeAndRemembersWhatWasAuthorized() throws {
        let event = try decodedV2()
        var timeline = Timeline(history: [event], wrapUp: nil)
        // Otro proceso de la misma sesión avisa de lo mismo al empezar su primer turno: no se repite.
        timeline.apply(.event(event))
        var reordered = event
        reordered.importNotice?.pending.reverse()
        timeline.apply(.event(reordered))
        XCTAssertEqual(cards(timeline).count, 1)
        XCTAssertFalse(timeline.items.contains { if case .events = $0 { return true } else { return false } },
                       "el aviso tiene su tarjeta, no una fila de avisos del sistema")
        // Si la carpeta trae otra cosa, sí.
        var other = event
        other.importNotice?.pending.removeLast()
        timeline.apply(.event(other))
        XCTAssertEqual(cards(timeline).count, 2)
        timeline.importsAuthorized(["docs"])
        timeline.importsAuthorized(["docs", "/probar"])
        XCTAssertEqual(cards(timeline).map(\.authorized), [["docs", "/probar"], ["docs", "/probar"]])
        XCTAssertTrue(timeline.markdown(title: "S").contains("> " + text))
    }

    func testMCPNoticesLeaveTheChatButRemainInHistoryAndTheSidebar() throws {
        var timeline = Timeline()
        let imported = try decodedV2()
        var mcpOnly = imported
        mcpOnly.importNotice?.pending.removeAll { $0.kind != .mcp }
        timeline.apply(.event(mcpOnly))
        let notice = try decoded(["tipo": "evento", "rol": "system", "texto": "MCP · Aviso de importación"])
        timeline.apply(.event(notice))
        timeline.apply(.event(notice))
        XCTAssertTrue(timeline.visibleConversation.isEmpty)
        XCTAssertEqual(timeline.mcpNotices.map(\.text), ["MCP · Aviso de importación"])
        XCTAssertFalse(timeline.items.isEmpty)

        timeline.apply(.event(imported))
        let cards = timeline.visibleConversation.compactMap { item -> ImportNotice? in
            if case let .importNotice(_, notice, _) = item { return notice }
            return nil
        }
        XCTAssertEqual(cards.count, 1)
        XCTAssertFalse(try XCTUnwrap(cards.first).pending.contains { $0.kind == .mcp })
    }

    func testTheReviewOffersWhatIsStillPendingWithTheNoticeFirst() throws {
        let notice = try XCTUnwrap(decodedV2().importNotice)
        let line = """
        {"version": 2, "tipo": "importadas", "cwd": "/p", "archivo": "/h/importadas.json", "elementos": [
         {"clave": "comando:nuevo@/p/opencode.json", "tipo": "comando", "nombre": "nuevo", "herramienta": "OpenCode",
          "lugar": "opencode.json", "estado": "pendiente", "autorizable": true, "huella": "1111111111111111"},
         {"clave": "comando:probar@/p/opencode.json", "tipo": "comando", "nombre": "probar", "herramienta": "OpenCode",
          "lugar": "opencode.json", "estado": "cambio", "autorizable": true, "huella": "2222222222222222",
          "vista": "Ejecuta las pruebas"},
         {"clave": "agente:revisor@/p/opencode.json", "tipo": "agente", "nombre": "revisor", "herramienta": "OpenCode",
          "lugar": "opencode.json", "estado": "autorizado", "autorizable": true, "huella": "00112233445566aa"},
         {"clave": "mcp:docs@/casa/.codex/config.toml", "tipo": "mcp", "nombre": "docs", "herramienta": "Codex",
          "lugar": "~/.codex/config.toml", "estado": "pendiente", "autorizable": true, "huella": "0123456789abcdef",
          "definicion": ["orden: uvx docs", "internet: sí"]},
         {"clave": "instrucciones:AGENTS.md@/p/AGENTS.md", "tipo": "instrucciones", "nombre": "AGENTS.md",
          "herramienta": "AGENTS.md", "lugar": "AGENTS.md", "estado": "activo", "autorizable": false}]}
        """
        guard case let .imported(config)? = AlfalfaMessage.decode(line: line.replacingOccurrences(of: "\n", with: " "))
        else { return XCTFail("no se decodificó la lista") }
        // revisor ya se autorizó (en Ajustes o en una terminal); `nuevo` llegó después del aviso y va al final.
        let items = ImportReview.items(config, notice: notice)
        XCTAssertEqual(items.map(\.name), ["docs", "probar", "nuevo"])
        // Se autoriza la huella de lo que se ve ahora, no la del aviso.
        XCTAssertEqual(ImportReview.authorizationKeys(items, selected: ["comando:probar@/p/opencode.json",
                                                                        "mcp:docs@/casa/.codex/config.toml"]),
                       ["mcp:docs@/casa/.codex/config.toml@0123456789abcdef",
                        "comando:probar@/p/opencode.json@2222222222222222"])
        let authorized = ImportReview.authorized(["agente:revisor@/p/opencode.json", "comando:nuevo@/p/opencode.json"],
                                                 in: config)
        XCTAssertEqual(authorized.map(\.name), ["revisor"])
        XCTAssertEqual(ImportReview.names(items), "docs, /probar y /nuevo")
        // Desde la barra lateral o el menú no hay aviso: lo pendiente, en el orden de la lista.
        XCTAssertEqual(ImportReview.items(config).map(\.name), ["nuevo", "probar", "docs"])
        XCTAssertEqual(ImportReview.shortcutTitle(config.pending), "3 importados sin autorizar")
        XCTAssertEqual(ImportReview.headline(config.pending),
                       "1 servidor MCP y 2 órdenes «/» de OpenCode y Codex sin autorizar")
    }

    func testTheSidebarShortcutNamesWhatIsPending() {
        func items(_ kind: ImportedItem.Kind, _ count: Int) -> [ImportedItem] {
            (0..<count).map { ImportedItem(key: "\(kind):n\($0)@/x", kind: kind, name: "n\($0)", tool: "Codex",
                                           place: "~/.codex/config.toml", state: "pendiente", authorizable: true) }
        }
        XCTAssertNil(ImportReview.shortcutTitle([]))
        XCTAssertEqual(ImportReview.shortcutTitle(items(.mcp, 5)), "5 MCP sin autorizar")
        XCTAssertEqual(ImportReview.shortcutTitle(items(.mcp, 1)), "1 MCP sin autorizar")
        XCTAssertEqual(ImportReview.shortcutTitle(items(.agente, 2)), "2 agentes sin autorizar")
        XCTAssertEqual(ImportReview.shortcutTitle(items(.comando, 1)), "1 orden «/» sin autorizar")
        XCTAssertEqual(ImportReview.shortcutTitle(items(.mcp, 1) + items(.agente, 1)), "2 importados sin autorizar")
        XCTAssertEqual(ImportReview.headline(items(.mcp, 5)), "5 servidores MCP de Codex sin autorizar")
    }

    // MARK: - Métodos privados

    private func decodedV2() throws -> AgentEvent {
        try decoded(["tipo": "evento", "rol": "system", "texto": text, "hora": 0, "importadas": ["pendientes": rows]])
    }

    /// Un `evento` de v2 (con su `version`).
    private func decoded(_ fields: [String: Any]) throws -> AgentEvent {
        let object = fields.merging(["version": 2]) { current, _ in current }
        let line = String(decoding: try JSONSerialization.data(withJSONObject: object), as: UTF8.self)
        guard case let .event(event)? = AlfalfaMessage.decode(line: line) else {
            XCTFail("no se decodificó el evento")
            throw CancellationError()
        }
        return event
    }

    private func cards(_ timeline: Timeline) -> [(notice: ImportNotice, authorized: [String])] {
        timeline.items.compactMap {
            if case let .importNotice(_, notice, authorized) = $0 { return (notice, authorized) } else { return nil }
        }
    }

    private func agent(_ type: String, _ payload: [String: Any], id: String = "m_start") -> String {
        let message: [String: Any] = ["pacai": "0.3", "id": id, "type": type, "sender": "agent", "seq": 1,
                                      "session": "s_" + String(repeating: "0", count: 32),
                                      "time": "2026-11-17T10:00:00.000Z", "payload": payload]
        return String(decoding: (try? JSONSerialization.data(withJSONObject: message)) ?? Data(), as: UTF8.self)
    }
}
