@testable import AlfalfaKit
import XCTest

/// La tarjeta de permiso de una edición: archivo, líneas +/− y contenido, sin JSON escapado.
final class EditDiffTests: XCTestCase {

    func testSplitsAPatchByFileWithItsCounts() throws {
        let text = ["--- a/schema/generar.py", "+++ b/schema/generar.py", "@@ -1,2 +1,2 @@", " import json",
                    "-VIEJO = 1", "+NUEVO = 2", "+OTRO = 3",
                    "--- /dev/null", "+++ b/docs/nuevo.md", "+# Hola",
                    "--- a/viejo.txt", "+++ /dev/null", "-adiós",
                    "--- a/a.py", "+++ b/b.py", "@@", "-x", "+y"].joined(separator: "\n")
        let diff = try XCTUnwrap(EditDiff(text))
        XCTAssertEqual(diff.files.map(\.path), ["schema/generar.py", "docs/nuevo.md", "viejo.txt", "b.py"])
        XCTAssertEqual(diff.files.map(\.change), [.modified, .added, .deleted, .renamed(from: "a.py")])
        XCTAssertEqual(diff.files[0].added, 2)
        XCTAssertEqual(diff.files[0].removed, 1)
        XCTAssertEqual(diff.files[0].lines, ["@@ -1,2 +1,2 @@", " import json", "-VIEJO = 1", "+NUEVO = 2", "+OTRO = 3"])
        XCTAssertEqual(diff.added, 4)
        XCTAssertEqual(diff.removed, 3)
    }

    func testALineStartingWithDashesIsNotTakenForAHeader() throws {
        let diff = try XCTUnwrap(EditDiff("--- a/x.md\n+++ b/x.md\n@@\n--- separador\n+=== separador"))
        XCTAssertEqual(diff.files.count, 1)
        XCTAssertEqual(diff.files[0].removed, 1)
        XCTAssertEqual(diff.files[0].added, 1)
    }

    func testWithoutHeadersItIsNotAnEditDiff() {
        XCTAssertNil(EditDiff("+ recuerda usar pytest\n- antes era unittest"))
        XCTAssertNil(EditDiff(""))
    }

    func testTheExternalContentEditCardCarriesItsDiffAndSessionScope() throws {
        let line = #"{"version":2,"tipo":"permiso","id":"e1","argv":["write_file","secrets/a.txt"],"#
            + #""proposito":"Autorizar write_file","motivo":"contenido_externo","motivos":["contenido_externo"],"#
            + #""justificacion":"El agente ha leído contenido externo (read_web) y esta acción toca un archivo con secretos.","#
            + #""para_sesion":true,"diff":"--- /dev/null\n+++ b/secrets/a.txt\n@@ -0,0 +1 @@\n+x"}"#
        guard case let .permission(request)? = AlfalfaMessage.decode(line: line) else { return XCTFail("sin permiso") }
        XCTAssertTrue(request.forSession)
        let diff = try XCTUnwrap(EditDiff(try XCTUnwrap(request.diff)))
        XCTAssertEqual(diff.files.first?.path, "secrets/a.txt")
        XCTAssertEqual(diff.files.first?.change, .added)
        XCTAssertEqual(diff.files.first?.added, 1)
    }

    func testNetworkAfterExternalContentSaysBothThings() throws {
        let both = PermissionRequest(id: "x", argv: ["git", "fetch"], purpose: "", reason: .network,
                                     reasons: [.network, .externalContent])
        let scope = try XCTUnwrap(both.scope)
        XCTAssertTrue(scope.contains("órdenes ajenas"))
        XCTAssertTrue(scope.contains("red abierta"), "no pierde cómo correrá la orden")
        let alone = PermissionRequest(id: "y", argv: ["spawn_agent"], purpose: "", reason: .externalContent,
                                      reasons: [.externalContent])
        XCTAssertTrue(try XCTUnwrap(alone.scope).contains("los demás permisos siguen vigentes"))
    }
}
