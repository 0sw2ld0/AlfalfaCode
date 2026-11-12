@testable import AlfalfaKit
import XCTest

/// Imágenes y vídeos locales que el agente cita en su respuesta, como en la app de Codex.
final class MarkdownMediaTests: XCTestCase {
    private var folder: URL!

    override func setUpWithError() throws {
        folder = FileManager.default.temporaryDirectory
            .appendingPathComponent("markdown-media-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: folder.appendingPathComponent("arte"),
                                                withIntermediateDirectories: true)
        for name in ["arte/caminar.png", "arte/correr.gif", "clip.mp4", "foto (1).jpg", "con espacio.webp", "nota.txt"] {
            FileManager.default.createFile(atPath: folder.appendingPathComponent(name).path, contents: Data([0]))
        }
    }

    override func tearDownWithError() throws {
        try? FileManager.default.removeItem(at: folder)
    }

    private func file(_ name: String) -> URL { folder.appendingPathComponent(name).standardizedFileURL }

    func testImageAloneOnItsLineIsAMediaBlock() {
        let path = file("arte/caminar.png").path
        let text = "Aquí están:\n![Tolgrenn — caminar](\(path))\nY nada más."
        XCTAssertEqual(MarkdownText.blocks(text, folder: folder), [
            .paragraph("Aquí están:"),
            .media([MarkdownMedia(alt: "Tolgrenn — caminar", source: path, url: file("arte/caminar.png"), kind: .image)]),
            .paragraph("Y nada más.")
        ])
    }

    func testRelativePathsUseTheSessionFolder() {
        let blocks = MarkdownText.blocks("![correr](arte/correr.gif)", folder: folder)
        XCTAssertEqual(blocks, [.media([MarkdownMedia(alt: "correr", source: "arte/correr.gif",
                                                      url: file("arte/correr.gif"), kind: .image)])])
        // Sin carpeta de sesión no hay a qué referir la ruta: se queda como texto.
        XCTAssertEqual(MarkdownText.blocks("![correr](arte/correr.gif)"), [.paragraph("![correr](arte/correr.gif)")])
    }

    func testVideoTildeAndFileURL() {
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        let exists: (String) -> Bool = { $0 == home + "/Movies/demo.mov" }
        XCTAssertEqual(MarkdownText.blocks("![demo](~/Movies/demo.mov)", fileExists: exists), [
            .media([MarkdownMedia(alt: "demo", source: "~/Movies/demo.mov",
                                  url: URL(fileURLWithPath: home + "/Movies/demo.mov"), kind: .video)])
        ])
        let clip = file("clip.mp4")
        XCTAssertEqual(MarkdownText.blocks("![](\(clip.absoluteString))"), [
            .media([MarkdownMedia(alt: "", source: clip.absoluteString, url: clip, kind: .video)])
        ])
    }

    func testConsecutiveImagesAndListItemsFormOneGallery() {
        let text = """
        - ![caminar](arte/caminar.png)
        - ![correr](arte/correr.gif)
        ![a](arte/caminar.png) ![b](clip.mp4)

        ![sola](arte/caminar.png)
        """
        let blocks = MarkdownText.blocks(text, folder: folder)
        XCTAssertEqual(blocks.count, 2)
        guard case let .media(gallery) = blocks.first, case let .media(alone) = blocks.last else {
            return XCTFail("\(blocks)")
        }
        XCTAssertEqual(gallery.map(\.alt), ["caminar", "correr", "a", "b"])
        XCTAssertEqual(gallery.map(\.kind), [.image, .image, .image, .video])
        // La línea en blanco separa: la última va sola, a todo el ancho.
        XCTAssertEqual(alone.map(\.alt), ["sola"])
    }

    func testAngleBracketsParenthesesTitlesAndPercentEncoding() {
        XCTAssertEqual(MarkdownText.blocks("![f](foto (1).jpg)", folder: folder),
                       [.media([MarkdownMedia(alt: "f", source: "foto (1).jpg", url: file("foto (1).jpg"), kind: .image)])])
        XCTAssertEqual(MarkdownText.blocks("![e](<con espacio.webp>)", folder: folder).first,
                       .media([MarkdownMedia(alt: "e", source: "con espacio.webp", url: file("con espacio.webp"), kind: .image)]))
        XCTAssertEqual(MarkdownText.blocks("![e](con%20espacio.webp)", folder: folder).first,
                       .media([MarkdownMedia(alt: "e", source: "con%20espacio.webp", url: file("con espacio.webp"), kind: .image)]))
        XCTAssertEqual(MarkdownText.blocks("![t](arte/caminar.png \"Ciclo de paso\")", folder: folder).first,
                       .media([MarkdownMedia(alt: "t", source: "arte/caminar.png", url: file("arte/caminar.png"), kind: .image)]))
    }

    func testRemoteURLsStayAsText() {
        for text in ["![logo](https://example.com/logo.png)", "![logo](http://example.com/logo.png)",
                     "![x](data:image/png;base64,AAAA)"] {
            XCTAssertEqual(MarkdownText.blocks(text, folder: folder, fileExists: { _ in true }), [.paragraph(text)], text)
        }
    }

    func testMissingFilesAndOtherFormatsStayAsText() {
        let missing = "![falta](arte/no-existe.png)"
        XCTAssertEqual(MarkdownText.blocks(missing, folder: folder), [.paragraph(missing)])
        let absolute = "![falta](/no/existe/lamina.png)"
        XCTAssertEqual(MarkdownText.blocks(absolute, folder: folder), [.paragraph(absolute)])
        XCTAssertEqual(MarkdownText.blocks("![nota](nota.txt)", folder: folder), [.paragraph("![nota](nota.txt)")])
        // Si una de las imágenes de la línea no vale, la línea entera se queda como texto.
        let mixed = "![a](arte/caminar.png) ![b](https://example.com/b.png)"
        XCTAssertEqual(MarkdownText.blocks(mixed, folder: folder), [.paragraph(mixed)])
        XCTAssertEqual(MarkdownText.blocks("- ![falta](no.png)", folder: folder), [.bullet("![falta](no.png)")])
    }

    func testImageInsideAParagraphStaysInline() {
        let text = "Mira ![caminar](arte/caminar.png) aquí."
        XCTAssertEqual(MarkdownText.blocks(text, folder: folder), [.paragraph(text)])
        let trailing = "![caminar](arte/caminar.png) y luego texto"
        XCTAssertEqual(MarkdownText.blocks(trailing, folder: folder), [.paragraph(trailing)])
        // Un enlace normal no es una imagen.
        XCTAssertEqual(MarkdownText.blocks("[caminar](arte/caminar.png)", folder: folder),
                       [.paragraph("[caminar](arte/caminar.png)")])
    }

    func testCodeBlocksKeepImageSyntax() {
        let text = "```markdown\n![caminar](arte/caminar.png)\n```"
        XCTAssertEqual(MarkdownText.blocks(text, folder: folder), [.code("![caminar](arte/caminar.png)", language: "markdown")])
    }

    func testInlineImageKeepsItsPathVisible() {
        let attributed = MarkdownText.inline("Mira ![caminar](arte/caminar.png) aquí")
        XCTAssertTrue(String(attributed.characters).contains("arte/caminar.png"), String(attributed.characters))
    }
}

final class MarkdownVisibleImagesTests: XCTestCase {
    func testTextImagesKeepTheirPathOrBecomeLinks() {
        XCTAssertEqual(MarkdownText.visibleImages("Mira ![caminar](arte/caminar.png) aquí"),
                       "Mira caminar (`arte/caminar.png`) aquí")
        XCTAssertEqual(MarkdownText.visibleImages("![](no.png)"), "`no.png`")
        XCTAssertEqual(MarkdownText.visibleImages("![logo](https://example.com/l.png)"), "[logo](https://example.com/l.png)")
        XCTAssertEqual(MarkdownText.visibleImages("`![x](y.png)` literal"), "`![x](y.png)` literal")
        XCTAssertEqual(MarkdownText.visibleImages("¡Hola! sin imágenes"), "¡Hola! sin imágenes")
        let link = MarkdownText.inline("![logo](https://example.com/l.png)")
        XCTAssertEqual(link.runs.compactMap(\.link), [URL(string: "https://example.com/l.png")!])
    }
}
