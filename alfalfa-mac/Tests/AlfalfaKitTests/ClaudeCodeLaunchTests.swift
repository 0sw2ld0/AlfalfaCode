import AlfalfaKit
import XCTest

final class ClaudeCodeLaunchTests: XCTestCase {
    func testSavedBrainstormParticipantsUseAnthropic() {
        for family in ["haiku", "sonnet", "opus"] {
            let saved = BrainstormParticipant(model: "claude-code/" + family, effort: "high")
            let native = BrainstormParticipant(model: "anthropic/claude-" + family + "-5-5", effort: "high")
            XCTAssertEqual(saved.replacingClaudeCode, native)
            XCTAssertEqual(native.replacingClaudeCode, native)
        }
    }

    func testSessionStartsInTheSelectedFolderWithTheChosenModelAndMCPDisabled() throws {
        let folder = try temporaryFolder()
        defer { try? FileManager.default.removeItem(at: folder) }
        let executable = try writeExecutable("printf '%s\\n' \"$(/usr/bin/stat -f %i .)\" \"$ENABLE_CLAUDEAI_MCP_SERVERS\" \"$@\"",
                                             name: "Claude's CLI", in: folder)
        let launcher = ClaudeCodeLaunch.session(executable: executable, folder: folder, model: .opus, effort: .high)
        let result = try run(launcher, in: folder)

        XCTAssertEqual(result.status, 0)
        let attributes = try FileManager.default.attributesOfItem(atPath: folder.path)
        let inode = try XCTUnwrap(attributes[.systemFileNumber] as? NSNumber)
        XCTAssertEqual(result.lines, [
            inode.stringValue, "false",
            "--strict-mcp-config", "--mcp-config", #"{"mcpServers":{}}"#,
            "--disallowedTools", "mcp__*", "--no-chrome", "--permission-mode", "default",
            "--model", "opus", "--effort", "high"
        ])
    }

    func testRenewalUsesOfficialLoginAndChecksStatusOnlyOnSuccess() throws {
        let folder = try temporaryFolder()
        defer { try? FileManager.default.removeItem(at: folder) }
        let success = try writeExecutable("printf '%s\\n' \"$*\"", name: "success", in: folder)
        let denied = try writeExecutable("printf '%s\\n' \"$*\"; exit 1", name: "denied", in: folder)

        let signedIn = try run(ClaudeCodeLaunch.signIn(executable: success), in: folder)
        XCTAssertEqual(signedIn.status, 0)
        XCTAssertEqual(signedIn.lines, ["auth login", "auth status --text"])

        let signedOut = try run(ClaudeCodeLaunch.signIn(executable: denied), in: folder)
        XCTAssertEqual(signedOut.status, 1)
        XCTAssertEqual(signedOut.lines, ["auth login"])
    }

    private func temporaryFolder() throws -> URL {
        let folder = FileManager.default.temporaryDirectory
            .appendingPathComponent("Claude's test \(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: false)
        return folder
    }

    private func writeExecutable(_ content: String, name: String, in folder: URL) throws -> URL {
        let url = folder.appendingPathComponent(name)
        try Data("#!/bin/sh\n\(content)\n".utf8).write(to: url)
        try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: url.path)
        return url
    }

    private func run(_ content: String, in folder: URL) throws -> (status: Int32, lines: [String]) {
        let script = folder.appendingPathComponent("Launch.command")
        try Data(content.utf8).write(to: script)

        let process = Process()
        let output = Pipe()
        process.executableURL = URL(fileURLWithPath: "/bin/sh")
        process.arguments = [script.path]
        process.standardOutput = output
        try process.run()
        let data = output.fileHandleForReading.readDataToEndOfFile()
        process.waitUntilExit()

        XCTAssertFalse(FileManager.default.fileExists(atPath: script.path))
        return (process.terminationStatus, String(decoding: data, as: UTF8.self).split(separator: "\n").map(String.init))
    }
}
