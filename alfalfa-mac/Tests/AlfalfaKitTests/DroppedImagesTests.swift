@testable import AlfalfaKit
import XCTest

final class DroppedImagesTests: XCTestCase {
    private let files: Set<String> = ["/tmp/a b/Captura de pantalla 1.png", "/tmp/x.jpg", "/tmp/y.PNG", "/tmp/doc.pdf"]
    private func exists(_ url: URL) -> Bool { files.contains(url.path) }

    func testDroppedPathBecomesAttachment() {
        let result = DroppedImages.extract(old: "Mira esto ", new: "Mira esto /tmp/a b/Captura de pantalla 1.png", exists: exists)
        XCTAssertEqual(result?.text, "Mira esto")
        XCTAssertEqual(result?.images.map(\.path), ["/tmp/a b/Captura de pantalla 1.png"])
    }

    func testSeveralFormats() {
        XCTAssertEqual(DroppedImages.extract(old: "", new: "/tmp/x.jpg /tmp/y.PNG", exists: exists)?.images.map(\.path),
                       ["/tmp/x.jpg", "/tmp/y.PNG"])
        XCTAssertEqual(DroppedImages.extract(old: "", new: "file:///tmp/x.jpg", exists: exists)?.images.map(\.path), ["/tmp/x.jpg"])
        XCTAssertEqual(DroppedImages.extract(old: "", new: "'/tmp/x.jpg'", exists: exists)?.images.map(\.path), ["/tmp/x.jpg"])
        XCTAssertEqual(DroppedImages.extract(old: "", new: "/tmp/a\\ b/Captura\\ de\\ pantalla\\ 1.png", exists: exists)?
            .images.map(\.path), ["/tmp/a b/Captura de pantalla 1.png"])
        // En medio del texto, sin espacios dobles.
        XCTAssertEqual(DroppedImages.extract(old: "antes  después", new: "antes /tmp/x.jpg después", exists: exists)?.text,
                       "antes después")
    }

    func testOrdinaryTextIsLeftAlone() {
        XCTAssertNil(DroppedImages.extract(old: "", new: "/tmp/no-existe.png", exists: exists))
        XCTAssertNil(DroppedImages.extract(old: "", new: "/tmp/doc.pdf", exists: exists), "no es una imagen")
        XCTAssertNil(DroppedImages.extract(old: "/tmp/x.jp", new: "/tmp/x.jpg", exists: exists), "se escribió una letra")
        XCTAssertNil(DroppedImages.extract(old: "", new: "Revisa /tmp/x.jpg", exists: exists), "texto con una ruta")
        XCTAssertNil(DroppedImages.extract(old: "hola", new: "hol", exists: exists))
    }
}
