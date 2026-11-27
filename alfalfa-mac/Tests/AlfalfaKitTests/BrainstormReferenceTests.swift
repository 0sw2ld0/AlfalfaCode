import AlfalfaKit
import XCTest

final class BrainstormReferenceTests: XCTestCase {
    private var folder: URL!

    override func setUpWithError() throws {
        folder = FileManager.default.temporaryDirectory.appendingPathComponent("lluvia-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
    }

    override func tearDownWithError() throws {
        try? FileManager.default.removeItem(at: folder)
    }

    private func file(_ name: String, bytes: Int = 10) throws -> URL {
        let url = folder.appendingPathComponent(name)
        try Data(repeating: 0x61, count: bytes).write(to: url)
        return url
    }

    func testMarkdownAndTextUpToTheLimitAreAccepted() throws {
        for name in ["notas.md", "NOTAS.Markdown", "idea.txt"] {
            let url = try file(name, bytes: BrainstormReference.maximumBytes)
            XCTAssertEqual(BrainstormReference.pick([url]), url, name)
        }
    }

    func testOtherFilesAreRefused() throws {
        XCTAssertNil(BrainstormReference.pick([try file("grande.md", bytes: BrainstormReference.maximumBytes + 1)]))
        XCTAssertNil(BrainstormReference.pick([try file("informe.pdf")]))
        XCTAssertNil(BrainstormReference.pick([folder.appendingPathComponent("falta.md")]))
        let directory = folder.appendingPathComponent("carpeta.md")
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        XCTAssertNil(BrainstormReference.pick([directory]))
        let link = folder.appendingPathComponent("enlace.md")
        try FileManager.default.createSymbolicLink(at: link, withDestinationURL: try file("destino.md"))
        XCTAssertNil(BrainstormReference.pick([link]))
        XCTAssertNil(BrainstormReference.pick([URL(string: "https://example.com/notas.md")!]))
    }

    func testOnlyOneFileAtATime() throws {
        XCTAssertNil(BrainstormReference.pick([try file("a.md"), try file("b.md")]))
        XCTAssertNil(BrainstormReference.pick([]))
    }
}
