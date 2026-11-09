@testable import AlfalfaKit
import XCTest

/// Ver los errores al editar: el interruptor de Ajustes y lo que enseña la fila de la edición.
final class EditDiagnosticsTests: XCTestCase {

    // MARK: - Métodos públicos

    func testTheSwitchCommand() {
        XCTAssertEqual(AlfalfaCommand.diagnostics(false).line, #"{"activo":false,"tipo":"diagnosticos"}"# + "\n")
        XCTAssertEqual(AlfalfaCommand.diagnostics(true).line, #"{"activo":true,"tipo":"diagnosticos"}"# + "\n")
    }

    func testTheLauncherPassesTheSwitchOnlyWhenOff() {
        XCTAssertEqual(Launcher.stdioArguments(task: "x", resume: nil, model: nil, effort: nil, readOnly: false),
                       ["--stdio", "--", "x"])
        XCTAssertEqual(Launcher.stdioArguments(task: "x", resume: nil, model: nil, effort: nil, readOnly: false,
                                               diagnostics: false),
                       ["--stdio", "--sin-diagnosticos", "--", "x"])
    }

    func testTheSettingIsOnUntilTurnedOff() throws {
        let suite = "alfalfa.pruebas.\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: suite))
        defer { defaults.removePersistentDomain(forName: suite) }
        let preferences = Preferences(defaults: defaults)
        XCTAssertTrue(preferences.diagnostics)
        preferences.diagnostics = false
        XCTAssertFalse(Preferences(defaults: defaults).diagnostics)
    }

    func testTheEditRowCountsTheNewErrors() {
        let start = ToolEvent(id: 1, role: "tool", text: "Inicio edit_file · {'path': 'a.py'}", time: 0,
                              tool: ToolInfo(name: "edit_file", phase: .start, description: "Editar a.py",
                                             target: "a.py"))
        let endInfo = ToolInfo.parse(["nombre": "edit_file", "fase": "fin",
                                      "diagnosticos": ["errores": 2, "avisos": 1, "incompleto": true]])
        XCTAssertEqual(endInfo?.diagnostics, EditDiagnostics(errors: 2, warnings: 1, incomplete: true))
        let end = ToolEvent(id: 2, role: "tool", text: "edit_file\n{\"path\": \"a.py\"}", time: 0, tool: endInfo)
        let call = ToolCalls.calls(from: [start, end]).first
        XCTAssertEqual(call?.diagnostics?.label, "2 errores · 1 aviso")
        XCTAssertEqual(call?.failed, false, "un error de tipo no hace fallar la edición")
        XCTAssertNil(EditDiagnostics(errors: 0, warnings: 0).label)
        XCTAssertEqual(EditDiagnostics(errors: 1).label, "1 error")
        XCTAssertNil(ToolInfo.parse(["nombre": "edit_file", "fase": "fin"])?.diagnostics)
    }
}
