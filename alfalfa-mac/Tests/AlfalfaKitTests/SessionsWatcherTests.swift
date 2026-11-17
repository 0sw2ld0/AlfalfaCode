@testable import AlfalfaKit
import XCTest

final class SessionsWatcherTests: XCTestCase {
    func testOnlyRunningStatesAreRunningElsewhere() {
        func summary(_ state: String) -> SessionSummary {
            SessionSummary(id: "a", task: nil, cwd: "/x", state: state, model: nil, effort: nil, date: 0, tokens: 0)
        }
        for state in ["running", "waiting", "starting"] { XCTAssertTrue(summary(state).isRunningElsewhere, state) }
        for state in ["completed", "failed", "blocked", "interrupted", "created", "corrupta"] {
            XCTAssertFalse(summary(state).isRunningElsewhere, state)
        }
    }

    func testSessionIDOfAPath() {
        let root = "/Users/x/.alfalfa/sesiones"
        XCTAssertEqual(SessionsWatcher.sessionID(of: root + "/1ddaf529/task.json", root: root), "1ddaf529")
        XCTAssertEqual(SessionsWatcher.sessionID(of: root + "/1ddaf529/native/1ddaf529/a.jsonl", root: root), "1ddaf529")
        XCTAssertEqual(SessionsWatcher.sessionID(of: root + "/nueva", root: root), "nueva")
        XCTAssertEqual(SessionsWatcher.sessionID(of: root, root: root + "/"), "")
        XCTAssertNil(SessionsWatcher.sessionID(of: "/Users/x/.alfalfa/adjuntos/a.png", root: root))
        XCTAssertNil(SessionsWatcher.sessionID(of: root + "-otra/a", root: root))
        // FSEvents da la ruta real de /var y /tmp.
        XCTAssertEqual(SessionsWatcher.sessionID(of: "/private/var/folders/t/sesiones/ab/task.json",
                                                 root: "/var/folders/t/sesiones"), "ab")
    }

    func testDirectoryFollowsAlfalfaHome() {
        XCTAssertEqual(SessionsWatcher.directory(environment: ["ALFALFA_HOME": "/tmp/casa"]).path, "/tmp/casa/sesiones")
        XCTAssertTrue(SessionsWatcher.directory(environment: [:]).path.hasSuffix("/.alfalfa/sesiones"))
    }

    func testNoticesASessionWrittenFromOutside() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("sesiones-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let seen = expectation(description: "cambio en la sesión abc")
        seen.assertForOverFulfill = false
        let watcher = SessionsWatcher(root: root, latency: 0.2) { ids in
            if ids.contains("abc") { seen.fulfill() }
        }
        XCTAssertTrue(watcher.start())
        defer { watcher.stop() }
        Thread.sleep(forTimeInterval: 0.5)   // FSEvents empieza a contar desde ahora
        let carpeta = root.appendingPathComponent("abc")
        try FileManager.default.createDirectory(at: carpeta, withIntermediateDirectories: true)
        try Data("{}".utf8).write(to: carpeta.appendingPathComponent("task.json"))
        wait(for: [seen], timeout: 20)
    }
}
