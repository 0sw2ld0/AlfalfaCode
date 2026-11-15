@testable import AlfalfaKit
import XCTest

/// Un clic en un enlace del agente no ejecuta nada: las páginas se abren y los archivos se enseñan.
final class AgentLinksTests: XCTestCase {
    private let folder = URL(fileURLWithPath: "/tmp/proyecto", isDirectory: true)

    func testWebPagesOpen() {
        for text in ["https://alfalfacode.cuycoders.dev/", "http://ejemplo.org/a?b=1", "HTTPS://Ejemplo.org"] {
            let url = URL(string: text)!
            XCTAssertEqual(AgentLinks.action(for: url, folder: folder), .open(url), text)
        }
    }

    func testFilesAreRevealedNotOpened() {
        let script = URL(string: "file:///tmp/proyecto/informe.command")!
        XCTAssertEqual(AgentLinks.action(for: script, folder: folder),
                       .reveal(URL(fileURLWithPath: "/tmp/proyecto/informe.command")))
        XCTAssertEqual(AgentLinks.action(for: URL(string: "docs/informe.md")!, folder: folder),
                       .reveal(URL(fileURLWithPath: "/tmp/proyecto/docs/informe.md")))
        XCTAssertEqual(AgentLinks.action(for: URL(string: "./a/../b.txt")!, folder: folder),
                       .reveal(URL(fileURLWithPath: "/tmp/proyecto/b.txt")))
    }

    func testOtherSchemesAreRefused() {
        for text in ["shortcuts://run-shortcut?name=Borrar", "x-apple.systempreferences:com.apple.preference",
                     "javascript:alert(1)", "vscode://file/etc/passwd", "ssh://host", "mailto:a@b.c",
                     "tel:123", "https://", "//ejemplo.org/x"] {
            XCTAssertEqual(AgentLinks.action(for: URL(string: text)!, folder: folder), .refuse, text)
        }
        XCTAssertEqual(AgentLinks.action(for: URL(string: "docs/x.md")!, folder: nil), .refuse)
    }
}
