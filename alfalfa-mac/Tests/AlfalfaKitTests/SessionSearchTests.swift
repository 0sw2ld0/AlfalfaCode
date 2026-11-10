@testable import AlfalfaKit
import XCTest

final class SessionSearchTests: XCTestCase {
    func testDecodesTheSearchOfTheCommandLine() {
        let line = #"{"version":2,"tipo":"busqueda_sesiones","consulta":"umbral","alcance":"todas","#
            + #""indice_al_dia":false,"resultados":[{"ref":"abc#2","sesion":"abc","posicion":2,"#
            + #""titulo":"Arreglar la compactación","cwd":"/repo","rol":"assistant","hora":1760000000.0,"#
            + #""fecha":"2025-10-09 10:13","turno":"t1","fragmento":"…bajando el umbral.","#
            + #""ancla":"La compactación falla porque"},{"sesion":"sin-ref","fragmento":"x"}]}"#
        guard case let .sessionSearch(found)? = AlfalfaMessage.decode(line: line) else { return XCTFail("busqueda") }
        XCTAssertEqual(found.query, "umbral")
        XCTAssertFalse(found.upToDate)
        XCTAssertEqual(found.hits, [SessionSearchHit(ref: "abc#2", sessionID: "abc", title: "Arreglar la compactación",
                                                     folder: "/repo", role: "assistant", date: 1_760_000_000,
                                                     fragment: "…bajando el umbral.",
                                                     anchor: "La compactación falla porque")])
        XCTAssertEqual(found.hits[0].roleLabel, "Agente")
        XCTAssertFalse(found.hits[0].external)
        let external = #"{"version":2,"tipo":"busqueda_sesiones","consulta":"x","resultados":[{"ref":"s#3","#
            + #""sesion":"s","rol":"tool","fragmento":"web","origen":"externo"}]}"#
        guard case let .sessionSearch(web)? = AlfalfaMessage.decode(line: external) else { return XCTFail("externa") }
        XCTAssertTrue(web.hits[0].external)
    }

    func testAnUntitledHitUsesTheSessionID() {
        let line = #"{"version":2,"tipo":"busqueda_sesiones","consulta":"x","resultados":[{"ref":"s#1","#
            + #""sesion":"s","titulo":"","rol":"user","fragmento":"hola"}]}"#
        guard case let .sessionSearch(found)? = AlfalfaMessage.decode(line: line) else { return XCTFail("busqueda") }
        XCTAssertEqual(found.hits.first?.title, "Sesión s")
        XCTAssertTrue(found.upToDate)
    }

    func testSearchesAllFoldersWithTheSameCommandAsTheTerminal() {
        XCTAssertEqual(SessionSearch.arguments(for: "umbral de Luna"),
                       ["--buscar-sesiones=umbral de Luna", "--todas", "--incluir-externas", "--json", "--tiempo", "3"])
        // Una consulta que empieza por «-» no se toma por otra opción.
        XCTAssertEqual(SessionSearch.arguments(for: "--reindexar").first, "--buscar-sesiones=--reindexar")
    }

    func testOpensTheConversationAtTheFoundMessage() {
        let events = [AgentEvent(role: "user", text: "¿Por qué falla?", time: 1, turn: "t1"),
                      AgentEvent(role: "agent", text: "La  compactación falla porque\nla tarifa cambia", time: 2,
                                 turn: "t1"),
                      AgentEvent(role: "user", text: "Gracias", time: 3, turn: "t2")]
        let timeline = Timeline(history: events, wrapUp: nil)
        let found = timeline.itemID(matching: "la COMPACTACION falla porque la tarifa")
        let agent = timeline.items.first { if case .agent = $0 { return true } else { return false } }
        XCTAssertNotNil(found)
        XCTAssertEqual(found, agent?.id)
        XCTAssertNil(timeline.itemID(matching: "algo que nadie dijo"))
        XCTAssertNil(timeline.itemID(matching: "   "))
    }
}
