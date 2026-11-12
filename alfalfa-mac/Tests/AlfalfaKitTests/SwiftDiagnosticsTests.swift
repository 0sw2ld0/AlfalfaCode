@testable import AlfalfaKit
import XCTest

/// Los diagnósticos de Swift (sourcekit-lsp, fuera del sandbox): apagados por defecto, con su
/// interruptor y su propio motivo en la tarjeta de permiso.
final class SwiftDiagnosticsTests: XCTestCase {

    private var suite = ""
    private var defaults = UserDefaults.standard

    override func setUp() {
        super.setUp()
        suite = "alfalfa.pruebas.\(UUID().uuidString)"
        defaults = UserDefaults(suiteName: suite) ?? .standard
    }

    override func tearDown() {
        defaults.removePersistentDomain(forName: suite)
        super.tearDown()
    }

    func testOffUnlessTheUserTurnsThemOn() {
        let preferences = Preferences(defaults: defaults)
        XCTAssertFalse(preferences.swiftDiagnostics, "como el LSP de OpenCode: apagado si nunca se tocó")
        XCTAssertTrue(preferences.diagnostics, "los demás diagnósticos siguen encendidos")
        preferences.swiftDiagnostics = true
        XCTAssertTrue(Preferences(defaults: defaults).swiftDiagnostics)
    }

    func testTheLauncherOnlyAsksForThemWhenOn() {
        func arguments(swift: Bool, diagnostics: Bool = true) -> [String] {
            Launcher.stdioArguments(task: "x", resume: nil, model: nil, effort: nil, readOnly: false,
                                    diagnostics: diagnostics, swiftDiagnostics: swift)
        }
        XCTAssertEqual(arguments(swift: false), ["--stdio", "--", "x"])
        XCTAssertEqual(arguments(swift: true), ["--stdio", "--diagnosticos-swift", "--", "x"])
        // Con los diagnósticos apagados no hay de Swift.
        XCTAssertEqual(arguments(swift: true, diagnostics: false), ["--stdio", "--sin-diagnosticos", "--", "x"])
    }

    func testTheLiveSwitch() {
        XCTAssertEqual(AlfalfaCommand.swiftDiagnostics(true).line, #"{"activo":true,"tipo":"diagnosticos_swift"}"# + "\n")
        XCTAssertEqual(AlfalfaCommand.swiftDiagnostics(false).line,
                       #"{"activo":false,"tipo":"diagnosticos_swift"}"# + "\n")
    }

    func testTheCardSaysItIsOptionalAndNotTheAgent() throws {
        let line = #"{"version":2,"tipo":"permiso","id":"s1","argv":["sourcekit-lsp","--default-workspace-type","compilationDatabase"],"#
            + #""proposito":"Diagnósticos de Swift de AlfalfaCode (opcionales), no una acción del agente","#
            + #""motivo":"diagnosticos_swift","motivos":["diagnosticos_swift"],"sin_sandbox":"…"}"#
        guard case let .permission(request)? = AlfalfaMessage.decode(line: line) else { return XCTFail("sin permiso") }
        XCTAssertEqual(request.reason, .swiftDiagnostics)
        XCTAssertEqual(request.reason?.title, "Diagnósticos de Swift (opcional)")
        XCTAssertNotEqual(request.reason?.title, PermissionReason.leaveSandbox.title)
        XCTAssertFalse(PermissionReason.swiftDiagnostics.leavesSandbox, "no se pinta como una orden que sale")
        let scope = try XCTUnwrap(request.scope)
        XCTAssertTrue(scope.contains("no una acción del agente"))
        XCTAssertTrue(scope.contains("sólo desactiva los diagnósticos de Swift en esta sesión"))
        XCTAssertFalse(request.forSession, "un «no» ya vale para toda la sesión")
    }
}

extension SwiftDiagnosticsTests {
    func testTheLiveRowSaysWhatItWaitsFor() {
        let events = [ToolEvent(id: 1, role: "tool", text: "Inicio edit_file · {'path': 'Suma.swift'}", time: 10)]
        let activity = LiveActivity.of(group: events, waiting: true, waitingFor: "los diagnósticos de Swift")
        XCTAssertEqual(activity.phase, .waiting)
        XCTAssertEqual(activity.phrase, "Esperando tu permiso para los diagnósticos de Swift")
    }
}
