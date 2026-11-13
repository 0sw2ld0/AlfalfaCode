@testable import AlfalfaKit
import XCTest

/// Un spawn_agent con un encargo de varios KB tras leer una web colgó la app de la 0.99.13 al pintar
/// su tarjeta de permiso. Lo que llega a SwiftUI desde la tarjeta, las filas de herramientas y el
/// panel de subagentes tiene que estar acotado, sin perder lo que hace falta para decidir.
final class LongPermissionTests: XCTestCase {

    /// Un encargo de unos `size` caracteres, con saltos, comillas y acentos, como los de un modelo.
    private func brief(_ size: Int) -> String {
        let unit = "Revisa el módulo «X» y su prueba 'Y'.\n- Paso con detalle: lee, compara y resume.\n"
        return String(String(repeating: unit, count: size / unit.count + 1).prefix(size))
    }

    /// La línea `permiso` que manda el motor (contenido_externo.autorizar → stdio): argv con el
    /// nombre y los argumentos en JSON, con claves ordenadas y los saltos escapados.
    private func permissionLine(tool: String, arguments: [String: Any], extra: [String: Any] = [:]) throws -> String {
        let json = try XCTUnwrap(String(data: JSONSerialization.data(withJSONObject: arguments, options: [.sortedKeys]),
                                        encoding: .utf8))
        var event: [String: Any] = [
            "version": 2, "tipo": "permiso", "id": "p1", "argv": [tool, json],
            "proposito": "Autorizar " + tool, "motivo": "contenido_externo", "motivos": ["contenido_externo"],
            "justificacion": "El agente ha leído contenido externo (read_web https://ejemplo.com) y esta acción "
                + "maneja otro agente u otra app del Mac.",
            "para_sesion": true
        ]
        event.merge(extra) { $1 }
        return try XCTUnwrap(String(data: JSONSerialization.data(withJSONObject: event), encoding: .utf8))
    }

    private func decodePermission(_ line: String) throws -> PermissionRequest {
        guard case let .permission(request)? = AlfalfaMessage.decode(line: line) else {
            XCTFail("no se decodificó el permiso")
            throw XCTSkip()
        }
        return request
    }

    /// Todo lo que la tarjeta pinta como texto (sin la hoja de «Ver todo»).
    private func painted(_ preview: PermissionPreview) -> [String] {
        [preview.headline ?? "", preview.body.shown, preview.purpose?.shown ?? "", preview.justification?.shown ?? "",
         preview.detail?.shown ?? "", preview.rule ?? ""] + preview.fields.flatMap { [$0.key, $0.value] }
    }

    func testASpawnAgentBriefOf5To50KBReachesSwiftUIBounded() throws {
        for size in [5_000, 20_000, 50_000] {
            let message = brief(size)
            let line = try permissionLine(tool: "spawn_agent", arguments: [
                "message": message, "description": "Revisar el módulo X", "agent_type": "explorador",
                "background": true
            ])
            XCTAssertGreaterThan(line.utf8.count, size, "el evento lleva el encargo entero")
            let start = Date()
            let request = try decodePermission(line)
            let preview = PermissionPreview(request)
            XCTAssertLessThan(Date().timeIntervalSince(start), 0.5, "se calcula sin bloquear el hilo principal")

            // Lo que llega a SwiftUI está acotado, sea cual sea el tamaño del encargo.
            let shown = painted(preview)
            XCTAssertLessThan(shown.map(\.count).reduce(0, +), 6_000, "\(size) caracteres")
            XCTAssertLessThanOrEqual(preview.body.shownLines, PermissionPreview.maxBodyLines)
            for text in shown {
                for part in text.split(separator: "\n") {
                    XCTAssertLessThanOrEqual(part.count, TextPreview.maxLineLength + 1)
                }
            }

            // Lo que hace falta para decidir: qué herramienta, para qué, con qué opciones y el encargo legible.
            XCTAssertEqual(preview.headline, "Encargar a un subagente: Revisar el módulo X")
            XCTAssertEqual(preview.bodyKey, "message")
            XCTAssertFalse(preview.bodyIsCommand)
            XCTAssertTrue(preview.body.shown.hasPrefix("Revisa el módulo «X» y su prueba 'Y'.\n- Paso con detalle"),
                          "saltos de verdad, no «\\n» escapados")
            XCTAssertFalse(preview.body.shown.contains("\\n"))
            XCTAssertEqual(preview.fields.map(\.key), ["agent_type", "background", "description"])
            XCTAssertEqual(preview.fields.first { $0.key == "background" }?.value, "sí")
            XCTAssertTrue(preview.truncated)
            // Y entero en «Ver todo», sin recortes.
            XCTAssertTrue(preview.full.contains(message))
            XCTAssertTrue(preview.full.contains("agent_type: explorador"))
        }
    }

    func testAShortSpawnAgentShowsEverythingWithoutSeeAll() throws {
        let request = try decodePermission(permissionLine(tool: "spawn_agent", arguments: [
            "message": "Busca los usos de cargar_partida.", "description": "Buscar usos"
        ]))
        let preview = PermissionPreview(request)
        XCTAssertEqual(preview.body.shown, "Busca los usos de cargar_partida.")
        XCTAssertFalse(preview.truncated)
    }

    func testCommandsKeepShowingTheExactOrder() throws {
        let request = PermissionRequest(id: "p2", argv: ["git", "push", "origin", "main"], purpose: "Publicar")
        let preview = PermissionPreview(request)
        XCTAssertNil(preview.headline)
        XCTAssertTrue(preview.bodyIsCommand)
        XCTAssertEqual(preview.body.shown, "git push origin main")
        XCTAssertFalse(preview.truncated)
        // Un argumento que parece JSON en una orden de verdad no se toma por herramienta.
        let echo = PermissionPreview(PermissionRequest(id: "p3", argv: ["echo", "{\"a\": 1}", "x"], purpose: ""))
        XCTAssertTrue(echo.bodyIsCommand)
    }

    func testLongPurposeJustificationAndSandboxFailureAreBounded() throws {
        let huge = brief(40_000)
        let line = try permissionLine(tool: "send_input", arguments: ["agent": "a1", "message": "sigue"], extra: [
            "proposito": huge, "justificacion": huge, "sin_sandbox": huge, "regla": huge,
            "motivos": ["salir_sandbox", "contenido_externo"]
        ])
        let preview = PermissionPreview(try decodePermission(line))
        for detail in [preview.purpose, preview.justification, preview.detail] {
            let detail = try XCTUnwrap(detail)
            XCTAssertLessThanOrEqual(detail.shownLines, PermissionPreview.maxDetailLines)
            XCTAssertLessThan(detail.shown.count, PermissionPreview.maxDetailLines * (TextPreview.maxLineLength + 2))
            XCTAssertTrue(detail.truncated)
        }
        XCTAssertLessThanOrEqual(try XCTUnwrap(preview.rule).count, PermissionPreview.maxFieldLength + 1)
    }

    func testToolRowsAndTheLiveRowNeverCarryAWholeArgument() {
        let huge = brief(50_000)
        let info = ToolInfo.parse(["nombre": "spawn_agent", "fase": "inicio", "descripcion": huge, "en_curso": huge])
        let start = ToolEvent(id: 1, role: "tool", text: "Inicio spawn_agent · {'message': 'x'}", time: 10, tool: info)
        let call = ToolCalls.calls(from: [start])[0]
        XCTAssertLessThanOrEqual(call.description.count, TextPreview.maxLineLength + 1)
        XCTAssertLessThanOrEqual(call.ongoing?.count ?? 0, TextPreview.maxLineLength + 1)
        XCTAssertFalse(call.description.contains("\n"), "una sola línea para la fila")
        XCTAssertLessThan(LiveActivity.of(group: [start]).phrase.count, 400)
        XCTAssertLessThan(LiveActivity.of(group: [start], waiting: true).phrase.count, 400)

        // Sin descripción del motor (motores anteriores): la que sale del repr también va acotada.
        let words = String(repeating: "a ", count: 5_000)
        let old = ToolEvent(id: 2, role: "tool",
                            text: "Inicio run_command · {'description': '" + words + "', 'argv': ['ls']}")
        XCTAssertLessThanOrEqual(ToolCalls.calls(from: [old])[0].description.count, TextPreview.maxLineLength + 1)
    }

    func testTheSubagentPanelIsBounded() throws {
        let huge = brief(50_000)
        let row: [String: Any] = ["id": "a1", "nombre": huge, "tipo": "general", "estado": "terminado",
                                  "actividad": huge, "informe": huge, "error": huge]
        let data = try JSONSerialization.data(withJSONObject: ["version": 2, "tipo": "subagentes", "subagentes": [row]])
        guard case let .subagents(list)? = AlfalfaMessage.decode(line: String(decoding: data, as: UTF8.self)) else {
            return XCTFail("no se decodificaron los subagentes")
        }
        let agent = try XCTUnwrap(list.first)
        XCTAssertLessThanOrEqual(agent.name.count, 201)
        XCTAssertLessThanOrEqual(agent.activity.count, TextPreview.maxLineLength + 1)
        XCTAssertLessThan(agent.report.count, 6 * (TextPreview.maxLineLength + 2))
        XCTAssertLessThan(agent.error.count, 6 * (TextPreview.maxLineLength + 2))

        // El primer paso de un subagente es su encargo entero (hasta 20 000 caracteres en una línea).
        let step = AgentEvent(role: "user", text: String(repeating: "palabra ", count: 2_500), time: 0)
        XCTAssertLessThanOrEqual(TextPreview.subagentStep(step).count, TextPreview.maxLineLength + 3)
        let tool = AgentEvent(role: "tool", text: "Inicio read_file · {'path': 'a.py'}\nmás", time: 0)
        XCTAssertEqual(TextPreview.subagentStep(tool), "· read_file · {'path': 'a.py'}")
    }

    func testOneLineCollapsesSpacesAndCuts() {
        XCTAssertEqual(TextPreview.line("  uno\n\n dos\tтри  "), "uno dos три")
        XCTAssertEqual(TextPreview.line("abcdef", limit: 3), "abc…")
        XCTAssertEqual(TextPreview.line("abc", limit: 3), "abc")
        XCTAssertEqual(TextPreview.line("ab cd", limit: 3), "ab…")
        XCTAssertEqual(TextPreview.line(String(repeating: "x", count: 50_000)).count, TextPreview.maxLineLength + 1)
    }
}
