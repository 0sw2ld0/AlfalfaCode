@testable import AlfalfaKit
import XCTest

/// La app ve lo mismo con `--pacai` que con `--stdio` v2: en cada escenario grabado con el motor de verdad, el
/// cliente PACAI de la app manda las mismas órdenes y da la misma secuencia de `AlfalfaMessage` que v2.
final class PacaiTranscriptTests: XCTestCase {

    // MARK: - Métodos

    func testPermissionsOnceSessionAlwaysAndDenied() throws { try assertSameEvents("permisos") }

    func testStopWithAPendingPermission() throws { try assertSameEvents("interrupcion") }

    func testQueuedMessagesSendNowAndUnqueue() throws { try assertSameEvents("cola") }

    func testRedirect() throws { try assertSameEvents("redirigir") }

    func testSubagents() throws { try assertSameEvents("subagentes") }

    func testBrowserWithASitePermissionForTheProject() throws { try assertSameEvents("navegador") }

    func testPlanAndChanges() throws { try assertSameEvents("plan_y_cambios") }

    func testCompaction() throws { try assertSameEvents("compactacion") }

    func testSettingsBudgetAndContextMode() throws { try assertSameEvents("ajustes") }

    func testAttachments() throws { try assertSameEvents("adjuntos") }

    func testResumeWithoutAMessage() throws { try assertSameEvents("reanudar") }

    func testTaskQueueAsksForAPermission() throws { try assertSameEvents("cola_de_encargos") }

    // MARK: - Métodos privados

    private func assertSameEvents(_ name: String, file: StaticString = #filePath, line: UInt = #line) throws {
        let transcript = try PacaiTranscript(name)
        let v2 = PacaiTranscript.normalized(try transcript.eventsV2(file: file, line: line))
        let pacai = PacaiTranscript.normalized(try transcript.eventsPacai(file: file, line: line))
        XCTAssertFalse(v2.isEmpty, file: file, line: line)
        for (index, pair) in zip(v2, pacai).enumerated() where pair.0 != pair.1 {
            XCTFail("\(name) #\(index):\n  v2:    \(pair.0)\n  PACAI: \(pair.1)", file: file, line: line)
            break
        }
        XCTAssertEqual(v2.count, pacai.count, "\(name): \(v2.count) eventos con v2 y \(pacai.count) con PACAI",
                       file: file, line: line)
    }
}
