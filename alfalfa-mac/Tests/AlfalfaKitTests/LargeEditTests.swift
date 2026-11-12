@testable import AlfalfaKit
import XCTest

/// Expandir una edición enorme no cuelga la app: lo que se pinta está acotado y se calcula rápido.
final class LargeEditTests: XCTestCase {

    private func patch(lines: Int) -> String {
        "*** Begin Patch\n*** Update File: schema/generar.py\n@@\n"
            + (0..<lines).map { "+linea \($0) = valor_de_prueba_\($0)" }.joined(separator: "\n") + "\n*** End Patch"
    }

    /// El evento llega como repr de Python: el parche entero en una sola línea con «\n» escapados.
    private func event(_ patch: String) -> String {
        "{'input': '" + patch.replacingOccurrences(of: "\n", with: "\\n") + "'}"
    }

    func testAFiveThousandLinePatchIsBoundedAndFast() throws {
        let call = ToolCall(id: 1, name: "apply_patch", kind: .edit, description: "Editar generar.py",
                            request: event(patch(lines: 5000)))
        let start = Date()
        let text = try XCTUnwrap(EditText.of(call))
        let preview = TextPreview(text)
        let seconds = Date().timeIntervalSince(start)
        XCTAssertLessThan(seconds, 0.5, "debe calcularse sin bloquear el hilo principal")
        XCTAssertEqual(preview.shownLines, TextPreview.maxLines)
        XCTAssertEqual(preview.totalLines, 5004)
        XCTAssertEqual(preview.hiddenLines, 5004 - TextPreview.maxLines)
        XCTAssertTrue(preview.truncated)
        XCTAssertTrue(preview.shown.hasPrefix("*** Begin Patch\n*** Update File: schema/generar.py\n@@\n+linea 0"))
        XCTAssertFalse(preview.shown.contains("\\n"), "saltos de línea de verdad, no escapados")
    }

    func testAHugeSingleLineIsCut() {
        let line = String(repeating: "x", count: 50_000)
        let preview = TextPreview(line)
        XCTAssertEqual(preview.totalLines, 1)
        XCTAssertEqual(preview.shown.count, TextPreview.maxLineLength + 1)
        XCTAssertTrue(preview.shown.hasSuffix("…"))
        XCTAssertTrue(preview.truncated)
        XCTAssertEqual(preview.hiddenLines, 0)
    }

    func testShortTextIsShownWhole() {
        let preview = TextPreview("uno\ndos\n")
        XCTAssertEqual(preview.shown, "uno\ndos\n")
        XCTAssertEqual(preview.totalLines, 3)
        XCTAssertFalse(preview.truncated)
    }

    func testEachEditReadsAsADiff() throws {
        let write = ToolCall(id: 1, name: "write_file", kind: .edit, description: "", request:
                                "{'path': 'a.py', 'content': 'uno\\ndos', 'expected_sha256': 'NEW'}", target: "a.py")
        XCTAssertEqual(EditText.of(write), "+++ a.py\n+uno\n+dos")
        let edit = ToolCall(id: 2, name: "edit_file", kind: .edit, description: "", request:
                                "{'path': 'a.py', 'old': \"it's\", 'new': 'es\\tasí'}", target: "a.py")
        XCTAssertEqual(EditText.of(edit), "--- a.py\n+++ a.py\n-it's\n+es\tasí")
        let other = ToolCall(id: 3, name: "read_file", kind: .read, description: "", request: "{'path': 'a.py'}")
        XCTAssertNil(EditText.of(other))
    }

    func testOnlyFollowsTheEndIfYouWereThere() {
        XCTAssertTrue(AutoScroll.isAtBottom(offset: 940, visible: 500, content: 1500))
        XCTAssertFalse(AutoScroll.isAtBottom(offset: 200, visible: 500, content: 1500))
        XCTAssertTrue(AutoScroll.isAtBottom(offset: 0, visible: 800, content: 300), "todo cabe")
        XCTAssertTrue(AutoScroll.follows(atBottom: true, userJustWrote: false))
        XCTAssertFalse(AutoScroll.follows(atBottom: false, userJustWrote: false), "subiste a leer: no te mueve")
        XCTAssertTrue(AutoScroll.follows(atBottom: false, userJustWrote: true))
    }
}
