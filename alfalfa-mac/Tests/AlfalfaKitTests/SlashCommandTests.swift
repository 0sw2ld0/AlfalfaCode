@testable import AlfalfaKit
import XCTest

final class SlashCommandTests: XCTestCase {
    func testInputSeparatesNameAndArgument() {
        XCTAssertEqual(SlashCommand.input("/mo"), SlashCommand.Input(name: "mo", argument: nil))
        XCTAssertEqual(SlashCommand.input("/modelo glm"), SlashCommand.Input(name: "modelo", argument: "glm"))
        XCTAssertNil(SlashCommand.input("hola /modelo"))
        XCTAssertNil(SlashCommand.input("/estado\notra línea"))
    }

    func testMatchingPutsExactFirstAndAcceptsEnglishNames() {
        XCTAssertEqual(SlashCommand.matching("").count, SlashCommand.allCases.count)
        XCTAssertEqual(SlashCommand.matching("mo").first, .modelo)
        XCTAssertEqual(SlashCommand.matching("model"), [.modelo])
        XCTAssertEqual(SlashCommand.matching("co"), [.compactar, .contexto, .copiar])
        XCTAssertEqual(SlashCommand.named("context"), .contexto)
        XCTAssertEqual(SlashCommand.named("compact"), .compactar)
        XCTAssertEqual(SlashCommand.named("init"), .inicializar)
        XCTAssertTrue(SlashCommand.matching("Users/alguien").isEmpty)
    }

    func testAvailabilityFollowsCodex() {
        XCTAssertFalse(SlashCommand.revisar.available(running: true, dormant: false))
        XCTAssertTrue(SlashCommand.modelo.available(running: true, dormant: false))
        XCTAssertFalse(SlashCommand.detener.available(running: false, dormant: false))
        XCTAssertTrue(SlashCommand.detener.available(running: true, dormant: false))
        // Una sesión guardada se abre sin turno para compactarla.
        XCTAssertTrue(SlashCommand.compactar.available(running: false, dormant: true))
        XCTAssertFalse(SlashCommand.detener.available(running: false, dormant: true))
        XCTAssertTrue(SlashCommand.estado.available(running: false, dormant: true))
        XCTAssertNotNil(SlashCommand.inicializar.prompt)
    }

    func testPermissionModes() {
        XCTAssertEqual(PermissionMode.named("plan"), .plan)
        XCTAssertEqual(PermissionMode.named(nil), .auto)
        XCTAssertEqual(PermissionMode.named("libre"), .auto)
        XCTAssertTrue(PermissionMode.omitir.needsConfirmation)
        XCTAssertFalse(PermissionMode.manual.needsConfirmation)
        XCTAssertEqual(SlashCommand.named("mode"), .modo)
        XCTAssertTrue(SlashCommand.modo.takesArgument)
        XCTAssertEqual(Launcher.stdioArguments(task: "Hola", resume: nil, model: nil, effort: nil, readOnly: false,
                                               mode: "manual"),
                       ["--stdio", "--modo", "manual", "--", "Hola"])
        XCTAssertEqual(AlfalfaCommand.settings(model: nil, effort: nil, mode: "plan").line.trimmingCharacters(in: .newlines),
                       #"{"modo":"plan","tipo":"ajustes"}"#)
        let line = #"{"version":2,"tipo":"sesion","id":"a","cwd":"/r","modelo":"m","esfuerzo":"medium","modo":"ediciones"}"#
        XCTAssertEqual(AlfalfaMessage.decode(line: line),
                       .session(SessionInfo(id: "a", cwd: "/r", model: "m", effort: "medium", mode: "ediciones")))
        XCTAssertEqual(PermissionReason(rawValue: "edicion")?.title, "Edición de archivos")
    }

    func testMarkdownExport() {
        var timeline = Timeline(history: [AgentEvent(role: "user", text: "Hola", time: 1),
                                          AgentEvent(role: "agent", text: "**Listo**", time: 2)], wrapUp: nil)
        XCTAssertEqual(timeline.lastAgentText, "**Listo**")
        timeline.addInfo("Estado", [InfoRow("Modelo", "x")])
        XCTAssertEqual(timeline.markdown(title: "Prueba"), "# Prueba\n\n## Tú\n\nHola\n\n## Agente\n\n**Listo**\n")
    }
}
