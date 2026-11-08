@testable import AlfalfaKit
import XCTest

final class WorkspaceTests: XCTestCase {
    func testDiffParserCountsAndKinds() {
        let diff = """
        diff --git a/suma.py b/suma.py
        index 1..2 100644
        --- a/suma.py
        +++ b/suma.py
        @@ -1,2 +1,2 @@
         def suma(a, b):
        -    return a - b
        +    return a + b
        diff --git a/otro.txt b/otro.txt
        --- a/otro.txt
        +++ b/otro.txt
        @@ -1 +1,2 @@
         uno
        +dos
        """
        let files = DiffParser.parse(diff)
        XCTAssertEqual(files.map(\.path), ["suma.py", "otro.txt"])
        XCTAssertEqual(files[0].added, 1)
        XCTAssertEqual(files[0].removed, 1)
        XCTAssertEqual(files[0].lines.map(\.kind), [.meta, .context, .removed, .added])
        XCTAssertEqual(files[1].added, 1)
        XCTAssertEqual(DiffParser.parse(""), [])
    }

    func testSnapshotOfRealGitRepository() async throws {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent("alfalfa-git-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: dir) }
        func git(_ args: String...) throws {
            let p = Process()
            p.executableURL = URL(fileURLWithPath: "/usr/bin/git")
            p.arguments = ["-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"] + args
            p.currentDirectoryURL = dir
            p.standardOutput = FileHandle.nullDevice
            p.standardError = FileHandle.nullDevice
            try p.run(); p.waitUntilExit()
            XCTAssertEqual(p.terminationStatus, 0, args.joined(separator: " "))
        }
        try git("init", "-b", "principal")
        try "a\nb\n".write(to: dir.appendingPathComponent("f.txt"), atomically: true, encoding: .utf8)
        try git("add", "f.txt")
        try git("commit", "-m", "uno")
        try "a\nc\n".write(to: dir.appendingPathComponent("f.txt"), atomically: true, encoding: .utf8)
        let snapshot = await Workspace.snapshot(of: dir)
        XCTAssertEqual(snapshot.branch, "principal")
        XCTAssertEqual(snapshot.files.map(\.path), ["f.txt"])
        XCTAssertEqual(snapshot.added, 1)
        XCTAssertEqual(snapshot.removed, 1)
    }

    func testSnapshotRedactsOllamaKeyFromDiff() async throws {
        let secret = "clave-falsa-de-prueba-123"
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent("alfalfa-git-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: dir) }
        func git(_ args: String...) throws {
            let p = Process()
            p.executableURL = URL(fileURLWithPath: "/usr/bin/git")
            p.arguments = ["-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"] + args
            p.currentDirectoryURL = dir
            p.standardOutput = FileHandle.nullDevice
            p.standardError = FileHandle.nullDevice
            try p.run(); p.waitUntilExit()
            XCTAssertEqual(p.terminationStatus, 0, args.joined(separator: " "))
        }
        try git("init", "-b", "principal")
        try "token=\n".write(to: dir.appendingPathComponent("c.txt"), atomically: true, encoding: .utf8)
        try git("add", "c.txt")
        try git("commit", "-m", "uno")
        try "token=\(secret)\n".write(to: dir.appendingPathComponent("c.txt"), atomically: true, encoding: .utf8)
        let snapshot = await Workspace.snapshot(of: dir, environment: ["OLLAMA_API_KEY": secret])
        let texts = snapshot.files.flatMap(\.lines).map(\.text)
        XCTAssertEqual(snapshot.added, 1)
        XCTAssertTrue(texts.contains("+token=[REDACTADO]"))
        XCTAssertFalse(texts.contains { $0.contains(secret) })
    }

    func testSnapshotOutsideGitHasNoBranch() async {
        let snapshot = await Workspace.snapshot(of: FileManager.default.temporaryDirectory)
        XCTAssertNil(snapshot.branch)
        XCTAssertEqual(snapshot.files, [])
    }
}
