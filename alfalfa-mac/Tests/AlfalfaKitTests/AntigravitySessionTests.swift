import Foundation
import XCTest
@testable import AlfalfaKit

final class AntigravitySessionTests: XCTestCase {
    func testMCPImportsOnlyEntriesWithoutCredentialsOrCustomPermissions() throws {
        let data = Data(#"""
        {"servidores":{
          "manual":{"url":"https://docs.example.test/mcp"},
          "inactive":{"url":"https://docs.example.test/mcp","activo":false},
          "stdio":{"orden":"/path/to/server"},
          "local":{"orden":"/path/to/server","argumentos":["--stdio"],"red":true},
          "scoped":{"url":"https://docs.example.test/mcp","habilitadas":["search"]},
          "secret":{"url":"https://docs.example.test/mcp","token_entorno":"TEST_TOKEN"},
          "embedded":{"url":"https://docs.example.test/mcp?token=test"},
          "path":{"url":"https://docs.example.test/token-in-path/mcp"}
        }}
        """#.utf8)
        let servers = try AntigravityMCPServer.portableServers(in: data)
        XCTAssertEqual(servers.map(\.id), ["local", "manual"])
    }

    @MainActor
    func testACPStreamsAndHonorsOneTimeApprovalDenialAndCancellation() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let session = AntigravitySession()
        defer {
            session.disconnect()
            try? FileManager.default.removeItem(at: directory)
        }
        let fixture = try XCTUnwrap(Bundle.module.url(forResource: "antigravity-agent", withExtension: "py",
                                                     subdirectory: "Fixtures"))
        let executable = directory.appendingPathComponent("agy")
        try FileManager.default.copyItem(at: fixture, to: executable)
        try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: executable.path)
        let data = Data(#"{"servidores":{"manual":{"url":"https://docs.example.test/mcp"}}}"#.utf8)
        let servers = try AntigravityMCPServer.portableServers(in: data) + [
            .delegation(launcher: URL(fileURLWithPath: "/test/alfalfa"), model: "openai/test", home: "/test/settings")
        ]
        session.connect(executable: executable, directory: directory, servers: servers)
        try await wait { session.state == .signInRequired }
        session.signIn()
        try await wait { session.state == .ready }
        for action in ["allow", "deny", "cancel"] {
            session.send(action)
            try await wait { !session.approvals.isEmpty }
            let approval = try XCTUnwrap(session.approvals.first)
            XCTAssertEqual(approval.title, "Test command")
            XCTAssertTrue(approval.details?.contains("printf test") == true)
            if action == "cancel" {
                session.cancelTurn()
            } else {
                session.respond(to: approval, allow: action == "allow")
            }
            try await wait { session.state == .ready }
            XCTAssertTrue(session.approvals.isEmpty)
            XCTAssertEqual(session.entries.last?.text, action == "cancel" ? "Turno cancelado." : "OK " + action)
        }
        XCTAssertEqual(session.usage?.used, 4200)
        XCTAssertEqual(session.usage?.size, 20000)
        XCTAssertFalse(session.entries.contains { $0.text.contains("wrong session") })
        XCTAssertFalse(FileManager.default.fileExists(atPath: directory.appendingPathComponent(
            "should-not-exist.txt"
        ).path))
        XCTAssertNil(session.error)
    }

    @MainActor
    private func wait(_ predicate: () -> Bool) async throws {
        let clock = ContinuousClock()
        let deadline = clock.now + .seconds(5)
        while !predicate() && clock.now < deadline {
            try await Task.sleep(for: .milliseconds(20))
        }
        guard predicate() else { throw CocoaError(.fileReadUnknown) }
    }
}
