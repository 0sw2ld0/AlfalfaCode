@testable import AlfalfaKit
import XCTest

final class ImportedConfigTests: XCTestCase {
    private let line = """
    {"version": 2, "tipo": "importadas", "cwd": "/p", "raiz": "/p", "archivo": "/h/importadas.json",
     "avisos": ["opencode.json: JSON inválido en la línea 3"], "errores": [],
     "elementos": [
      {"clave": "mcp:eco@/p/opencode.json", "corta": "mcp:eco", "tipo": "mcp", "nombre": "eco",
       "herramienta": "OpenCode", "origen": "/p/opencode.json", "lugar": "opencode.json", "ambito": "proyecto",
       "detalle": "orden · python3", "estado": "pendiente", "autorizable": true},
      {"clave": "mcp:docs@/p/.mcp.json", "tipo": "mcp", "nombre": "docs", "herramienta": "Claude Code",
       "origen": "/p/.mcp.json", "lugar": ".mcp.json", "ambito": "proyecto", "detalle": "HTTP · x.y/mcp",
       "estado": "autorizado", "autorizable": true, "red": false},
      {"clave": "agente:revisor@/p/opencode.json", "tipo": "agente", "nombre": "revisor", "herramienta": "OpenCode",
       "origen": "/p/opencode.json", "lugar": "opencode.json", "ambito": "proyecto", "detalle": "Revisa",
       "estado": "cambio", "autorizable": true},
      {"clave": "comando:probar@/p/opencode.json", "tipo": "comando", "nombre": "probar", "herramienta": "OpenCode",
       "origen": "/p/opencode.json", "lugar": "opencode.json", "ambito": "proyecto", "detalle": "Corre pruebas",
       "estado": "activo", "autorizable": false},
      {"clave": "comando:revisar@/p/.claude/commands/revisar.md", "tipo": "comando", "nombre": "revisar",
       "herramienta": "Claude Code", "origen": "/p/.claude/commands/revisar.md", "lugar": ".claude/commands/revisar.md",
       "ambito": "proyecto", "detalle": "", "estado": "activo", "autorizable": false},
      {"clave": "comando:viejo@/p/opencode.json", "tipo": "comando", "nombre": "viejo", "herramienta": "OpenCode",
       "origen": "/p/opencode.json", "lugar": "opencode.json", "ambito": "proyecto", "detalle": "",
       "estado": "duplicado", "autorizable": false, "tapado_por": ".opencode/opencode.json"},
      {"clave": "instrucciones:CLAUDE.md@/p/CLAUDE.md", "tipo": "instrucciones", "nombre": "CLAUDE.md",
       "herramienta": "Claude Code", "origen": "/p/CLAUDE.md", "lugar": "CLAUDE.md", "ambito": "proyecto",
       "detalle": "", "estado": "duplicado", "autorizable": false, "tapado_por": "AGENTS.md"}]}
    """

    private func decoded() throws -> ImportedConfig {
        let compact = line.replacingOccurrences(of: "\n", with: " ")
        guard case let .imported(config)? = AlfalfaMessage.decode(line: compact) else {
            XCTFail("no se decodificó el evento importadas")
            throw CancellationError()
        }
        return config
    }

    func testDecodesTheImportListWithStatesAndPlaces() throws {
        let config = try decoded()
        XCTAssertEqual(config.folder, "/p")
        XCTAssertEqual(config.items.count, 7)
        XCTAssertEqual(config.warnings, ["opencode.json: JSON inválido en la línea 3"])
        XCTAssertEqual(config.pending.map(\.name), ["eco", "revisor"])
        let docs = try XCTUnwrap(config.items.first { $0.name == "docs" })
        XCTAssertEqual(docs.stateTitle, "Autorizado, sin red")
        XCTAssertFalse(docs.needsAuthorization)
        XCTAssertEqual(config.items(of: .agente).first?.stateTitle, "Cambió: vuelve a autorizarlo")
        XCTAssertEqual(config.items(of: .instrucciones).first?.coveredBy, "AGENTS.md")
        XCTAssertEqual(config.items.first?.place, "opencode.json")
    }

    func testProjectCommandsSkipBuiltInsAndInactiveOnes() throws {
        let config = try decoded()
        // /revisar es una orden propia de la app: la del proyecto no la tapa. /viejo está duplicada.
        XCTAssertEqual(config.commands(matching: "").map(\.name), ["probar"])
        XCTAssertEqual(config.commands(matching: "PR").map(\.name), ["probar"])
        XCTAssertTrue(config.commands(matching: "x").isEmpty)
    }

    func testIgnoresRowsWithoutKeyOrUnknownKind() {
        let line = #"{"version": 2, "tipo": "importadas", "elementos": [{"tipo": "mcp", "nombre": "x"},"#
            + #"{"clave": "k", "tipo": "otro", "nombre": "y"}]}"#
        guard case let .imported(config)? = AlfalfaMessage.decode(line: line) else {
            return XCTFail("no se decodificó")
        }
        XCTAssertTrue(config.items.isEmpty)
    }
}
