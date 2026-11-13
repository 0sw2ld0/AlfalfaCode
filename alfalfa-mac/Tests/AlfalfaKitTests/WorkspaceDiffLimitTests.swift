@testable import AlfalfaKit
import Foundation
import XCTest

/// Un repositorio con miles de archivos cambiados daba un `git diff` de más de 100 MB: la app lo
/// leía entero en cada refresco del panel y se quedaba al 30 % de CPU.
final class WorkspaceDiffLimitTests: XCTestCase {
    func testCaptureStopsAtTheLimitOnAWholeLine() async throws {
        let captured = await Workspace.capture(
            "/bin/sh", ["-c", "yes 'una línea' | head -n 500000"], in: URL(fileURLWithPath: "/"), limit: 100_000)
        let result = try XCTUnwrap(captured)
        XCTAssertTrue(result.truncated)
        XCTAssertLessThanOrEqual(result.text.utf8.count, 100_000 + 64 * 1024)
        XCTAssertTrue(result.text.split(separator: "\n").allSatisfy { $0 == "una línea" })
    }

    func testSmallOutputIsWholeAndAFailureIsNil() async {
        let whole = await Workspace.capture("/bin/sh", ["-c", "printf 'a\\nb\\n'"], in: URL(fileURLWithPath: "/"),
                                            limit: 100_000)
        XCTAssertEqual(whole?.text, "a\nb\n")
        XCTAssertEqual(whole?.truncated, false)
        let failed = await Workspace.capture("/bin/sh", ["-c", "exit 3"], in: URL(fileURLWithPath: "/"), limit: 10)
        XCTAssertNil(failed)
    }

    func testNumstatGivesEveryFileAndTheRealTotals() {
        let partial = DiffParser.parse("""
        diff --git a/uno.swift b/uno.swift
        @@ -1 +1 @@
        -viejo
        +nuevo
        diff --git a/dos.swift b/dos.swift
        @@ -1,2 +1 @@
        -a
        """)
        let files = DiffParser.merge(numstat: "1\t1\tuno.swift\n0\t40\tdos.swift\n-\t-\timagen.png\n3\t0\ttres.swift\n",
                                     detailed: partial)
        XCTAssertEqual(files.map(\.path), ["uno.swift", "dos.swift", "imagen.png", "tres.swift"])
        XCTAssertEqual(files.map(\.removed), [1, 40, 0, 0])
        XCTAssertEqual(files[0].lines.count, 3, "conserva las líneas leídas del diff")
        XCTAssertTrue(files[3].lines.isEmpty)
        var snapshot = GitSnapshot(branch: "main", files: files)
        snapshot.gitAvailable = true
        XCTAssertEqual(snapshot.added, 4)
        XCTAssertEqual(snapshot.removed, 41)
        XCTAssertEqual(DiffParser.merge(numstat: "", detailed: partial), partial)
    }
}
