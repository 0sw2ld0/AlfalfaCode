import AlfalfaKit
import XCTest

/// Un texto pegado largo se adjunta como archivo en vez de entrar en el cuadro, que se congela.
final class LongPasteTests: XCTestCase {

    func testOnlyLongTextIsAttached() {
        XCTAssertFalse(Attachments.isLongPaste("Arregla el test de imágenes"))
        XCTAssertFalse(Attachments.isLongPaste(String(repeating: "a", count: Attachments.longPasteCharacters - 1)))
        XCTAssertTrue(Attachments.isLongPaste(String(repeating: "a", count: Attachments.longPasteCharacters)))
        XCTAssertFalse(Attachments.isLongPaste(String(repeating: "x\n", count: Attachments.longPasteLines - 2)))
        XCTAssertTrue(Attachments.isLongPaste(String(repeating: "x\n", count: Attachments.longPasteLines - 1)),
                      "muchas líneas cortas también")
    }

    func testStoresTheTextAsAnAttachableTextFile() throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let text = String(repeating: "E   assert not {'generate_image'} · ñ\n", count: 200)
        let url = try Attachments.store(text: text, in: directory)
        XCTAssertEqual(try String(contentsOf: url, encoding: .utf8), text)
        XCTAssertTrue(url.lastPathComponent.hasPrefix("Texto pegado "))
        XCTAssertTrue(Attachments.isFile(url), "el motor lo lee como texto adjunto")
        XCTAssertFalse(Attachments.isImage(url))
        let second = try Attachments.store(text: "otro", in: directory)
        XCTAssertNotEqual(url, second, "dos pegados no se pisan")
    }

    /// Un texto largo que entra por el menú Edición, el clic derecho o arrastrado no pasa por ⌘V:
    /// el cuadro lo saca de su texto al cambiar.
    func testLongTextInsertedAnyWayLeavesTheBox() {
        let log = String(repeating: "E   assert 1 == 2\n", count: 300)
        let pasted = PastedText.extract(old: "Mira este log:  y arréglalo", new: "Mira este log: \(log) y arréglalo")
        XCTAssertEqual(pasted?.pasted, log)
        XCTAssertEqual(pasted?.text, "Mira este log:  y arréglalo")
        let lines = String(repeating: "x\n", count: Attachments.longPasteLines)
        XCTAssertEqual(PastedText.extract(old: "", new: lines)?.pasted, lines, "muchas líneas cortas también")
    }

    func testTypingAndShortPastesStayInTheBox() {
        XCTAssertNil(PastedText.extract(old: "Arregla", new: "Arregla "))
        XCTAssertNil(PastedText.extract(old: "", new: "una frase pegada corta"))
        XCTAssertNil(PastedText.extract(old: "abc", new: "ab"), "borrar no es pegar")
        let almost = String(repeating: "a", count: Attachments.longPasteCharacters - 1)
        XCTAssertNil(PastedText.extract(old: "", new: almost))
    }
}
