import AppKit
@testable import AlfalfaKit
import XCTest

/// El texto corrido de una respuesta va en una sola pieza: se selecciona arrastrando de un bloque al siguiente.
final class MarkdownProseTests: XCTestCase {

    func testRunsOfTextBlocksBecomeOneSelectablePiece() {
        let blocks = MarkdownText.blocks("""
        ## Hecho

        Arreglé la selección.

        - uno
        - dos

        > una cita

        ```swift
        let a = 1
        ```

        Y otro párrafo.
        """, folder: nil)
        let segments = MarkdownProse.segments(blocks)
        XCTAssertEqual(segments.count, 3, "texto, código y texto")
        guard case let .prose(first) = segments[0], case .block(.code) = segments[1],
              case let .prose(last) = segments[2] else { return XCTFail("\(segments)") }
        XCTAssertEqual(first.count, 5, "título, párrafo, dos viñetas y la cita, juntos")
        XCTAssertEqual(last, [.paragraph("Y otro párrafo.")])
    }

    /// Lo que se copia: cada bloque en su línea, con su viñeta o número, y sin las marcas de Markdown.
    func testCopiedTextKeepsEachBlockOnItsLine() {
        let text = MarkdownProse.attributed([
            .heading(2, "Hecho"), .paragraph("Con **negrita** y `código`."), .bullet("uno"),
            .numbered(2, "dos"), .task(true, "tres"), .quote("cita"),
        ])
        XCTAssertEqual(text.string, "Hecho\nCon negrita y código.\n•\tuno\n2.\tdos\n☑\ttres\ncita")
    }

    func testInlineFormattingAndLinks() throws {
        let text = MarkdownProse.attributed([.paragraph("Mira **esto** y [la guía](https://example.com).")])
        let string = text.string as NSString
        let bold = try XCTUnwrap(text.attribute(.font, at: string.range(of: "esto").location, effectiveRange: nil)
                                 as? NSFont)
        XCTAssertTrue(bold.fontDescriptor.symbolicTraits.contains(.bold))
        let link = text.attribute(.link, at: string.range(of: "la guía").location, effectiveRange: nil) as? URL
        XCTAssertEqual(link, URL(string: "https://example.com"))
    }

    /// Una viñeta de varias líneas queda alineada tras el marcador; el aire va entre bloques, no al final.
    func testListsHangAndOnlyBlocksAreSpaced() throws {
        let text = MarkdownProse.attributed([.paragraph("Antes"), .bullet("punto", level: 1)])
        let string = text.string as NSString
        let before = try XCTUnwrap(text.attribute(.paragraphStyle, at: 0, effectiveRange: nil) as? NSParagraphStyle)
        XCTAssertEqual(before.paragraphSpacing, 12)
        let item = try XCTUnwrap(text.attribute(.paragraphStyle, at: string.range(of: "punto").location,
                                                effectiveRange: nil) as? NSParagraphStyle)
        XCTAssertEqual(item.firstLineHeadIndent, 28)
        XCTAssertEqual(item.headIndent, 54)
        XCTAssertEqual(item.paragraphSpacing, 0, "tras el último bloque, el aire lo pone la conversación")
    }
}
