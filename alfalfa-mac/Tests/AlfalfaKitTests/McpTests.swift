import XCTest
@testable import AlfalfaKit

/// Campos MCP de `permiso` y `contexto` en `alfalfa --stdio` (opcionales en el protocolo v2).
final class McpTests: XCTestCase {
    private func decoded() throws -> [AlfalfaMessage?] {
        try fixtureLines("mcp").map(AlfalfaMessage.decode(line:))
    }

    func testPermissionCarriesServerToolStructuredArgumentsAndCallID() throws {
        guard case let .permission(request)? = try decoded()[1] else { return XCTFail("permiso") }
        XCTAssertEqual(request.mcp, McpCall(server: "prueba", tool: "contar", name: "mcp__prueba__contar", arguments: [
            McpArgument(key: "filtros", value: #"{"estado":"abierto"}"#),
            McpArgument(key: "nota", value: "stdio"),
            McpArgument(key: "pagina", value: "2"),
            McpArgument(key: "token", value: "[REDACTADO]"),
        ], callID: "a1b2c3d4e5f6"))
        // `mcp` no es un motivo on-request conocido: no cambia el alcance de los comandos.
        XCTAssertNil(request.reason)
        XCTAssertEqual(request.reasons, [])
        XCTAssertNil(request.scope)
        XCTAssertEqual(request.argv, ["mcp__prueba__contar"])
    }

    func testResultEventCorrelatesWithTheCard() throws {
        let rows = try decoded()
        guard case let .permission(request)? = rows[1], case let .event(event)? = rows[3] else { return XCTFail() }
        XCTAssertTrue(event.text.contains(request.mcp!.callID))
    }

    func testCommandCardsAndMalformedMcpStayCompatible() throws {
        let rows = try decoded()
        guard case let .permission(command)? = rows[6], case let .permission(broken)? = rows[7] else { return XCTFail() }
        XCTAssertEqual(command.reason, .dangerous)
        XCTAssertNil(command.mcp)
        XCTAssertEqual(command.rule, "rm con opción de fuerza")
        // Un `mcp` incompleto se ignora: la petición sigue siendo un permiso normal.
        XCTAssertEqual(broken.id, "m2")
        XCTAssertNil(broken.mcp)
        for name in ["salidas", "sesion_con_permisos", "sesion_real", "permisos_bajo_demanda"] {
            for line in try fixtureLines(name) {
                if case let .permission(request)? = AlfalfaMessage.decode(line: line) { XCTAssertNil(request.mcp, name) }
                if case let .context(_, _, mcp, _, _)? = AlfalfaMessage.decode(line: line) { XCTAssertNil(mcp, name) }
            }
        }
    }

    func testContextDecodesBreakdownAndLegacyLines() throws {
        let rows = try decoded()
        let method = "estimado: ceil(bytes UTF-8 del JSON compacto / 4)"
        XCTAssertEqual(rows[0], .context(tokens: 0, limit: 0, mcp: McpContextUsage(
            loadedTokens: 0, loadedCount: 0, baseTokens: 204, method: method, exact: false)))
        XCTAssertEqual(rows[4], .context(tokens: 900, limit: 131072, mcp: McpContextUsage(
            loadedTokens: 23, loadedCount: 1, baseTokens: 204, method: method, exact: false,
            tools: [ContextEntry(name: "mcp__prueba__contar", tokens: 23)], deferredCount: 2, deferredTokens: 310)))
        XCTAssertEqual(rows[5], .context(tokens: 1000, limit: 131072))
        // Un booleano no es un número de tokens: se descarta el desglose, no el evento.
        XCTAssertEqual(rows[8], .context(tokens: 1100, limit: 131072))
    }

    func testTimelineKeepsTheLatestBreakdownNextToTheTotal() throws {
        var timeline = Timeline()
        let rows = try decoded()
        timeline.apply(rows[0]!)
        XCTAssertEqual(timeline.mcpContext?.loadedTokens, 0)
        XCTAssertEqual(timeline.contextLimit, 0)
        for row in rows[1...4] { timeline.apply(row!) }
        XCTAssertEqual(timeline.mcpContext?.loadedTokens, 23)
        XCTAssertEqual(timeline.mcpContext?.totalTokens, 227)
        XCTAssertEqual(timeline.contextTokens, 900)
        guard case let .permission(_, request, state) = timeline.items[0] else { return XCTFail("tarjeta") }
        XCTAssertEqual(request.mcp?.tool, "contar")
        XCTAssertEqual(state, .approved)
        // Un contexto sin desglose actualiza el total y conserva el último desglose conocido.
        timeline.apply(rows[5]!)
        XCTAssertEqual(timeline.contextTokens, 1000)
        XCTAssertEqual(timeline.mcpContext?.loadedTokens, 23)
        XCTAssertEqual(timeline.mcpContext?.summary, "1 esquema cargado · base 204 · estimado")
        XCTAssertEqual(McpContextUsage(loadedTokens: 0, loadedCount: 3, baseTokens: 1, method: "", exact: true).summary,
                       "3 esquemas cargados · base 1 · exacto")
    }

    func testSessionsWithoutMcpHaveNoBreakdown() throws {
        var timeline = Timeline()
        for line in try fixtureLines("sesion_con_permisos") { if let m = AlfalfaMessage.decode(line: line) { timeline.apply(m) } }
        XCTAssertNil(timeline.mcpContext)
    }
}
