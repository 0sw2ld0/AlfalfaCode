@testable import AlfalfaKit
import XCTest

/// El proceso del motor: Detener vuelve a funcionar, las consultas no retienen su proceso, y se sabe
/// cuándo el motor ya no lee lo que se le escribe.
final class EngineLifetimeTests: XCTestCase {
    private func script(_ body: String) throws -> URL {
        let url = FileManager.default.temporaryDirectory.appendingPathComponent("alfalfa-vida-\(UUID().uuidString).sh")
        try ("#!/bin/sh\n" + body).write(to: url, atomically: true, encoding: .utf8)
        try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: url.path)
        addTeardownBlock { try? FileManager.default.removeItem(at: url) }
        return url
    }

    private func openDescriptors() -> Int {
        (try? FileManager.default.contentsOfDirectory(atPath: "/dev/fd").count) ?? 0
    }

    // MARK: - A15: cada consulta suelta su proceso y sus tuberías

    func testAQueryReleasesItsProcess() async throws {
        let launcher = try script(#"echo '{"version":2,"tipo":"modelos","modelos":[{"name":"a"}]}'"#)
        final class Weak: @unchecked Sendable { weak var process: AlfalfaProcess? }
        let seen = Weak()
        _ = try await AlfalfaProcess.query(launcher: launcher, arguments: [],
                                           directory: FileManager.default.temporaryDirectory,
                                           started: { seen.process = $0 })
        let deadline = Date().addingTimeInterval(5)
        while seen.process != nil && Date() < deadline { try await Task.sleep(nanoseconds: 20_000_000) }
        XCTAssertNil(seen.process, "el proceso de la consulta sigue vivo: un ciclo lo retiene")
    }

    func testManyQueriesDoNotLeakDescriptors() async throws {
        let launcher = try script(#"echo '{"version":2,"tipo":"modelos","modelos":[{"name":"a"}]}'"#)
        _ = try await AlfalfaProcess.query(launcher: launcher, arguments: [],
                                           directory: FileManager.default.temporaryDirectory)
        try await Task.sleep(nanoseconds: 200_000_000)
        let before = openDescriptors()
        for _ in 0..<40 {
            _ = try await AlfalfaProcess.query(launcher: launcher, arguments: [],
                                               directory: FileManager.default.temporaryDirectory)
        }
        try await Task.sleep(nanoseconds: 300_000_000)
        XCTAssertLessThan(openDescriptors() - before, 10, "cada consulta deja descriptores abiertos")
    }

    // MARK: - M22: saber si el motor todavía lee

    func testAcceptsInputUntilItsInputIsClosed() async throws {
        let launcher = try script("while IFS= read -r line; do :; done\nsleep 0.3\n")
        let process = AlfalfaProcess(launcher: launcher, arguments: [], directory: FileManager.default.temporaryDirectory)
        let exited = expectation(description: "salió")
        process.onExit = { _, _ in exited.fulfill() }
        try process.start()
        XCTAssertTrue(process.acceptsInput)
        process.closeInputAndWait()
        XCTAssertFalse(process.acceptsInput, "con la entrada cerrada, un mensaje se perdería")
        await fulfillment(of: [exited], timeout: 10)
        XCTAssertFalse(process.acceptsInput)
    }

    // MARK: - A14: Detener

    func testStopCanBeUsedAgainAfterTheTurnEnds() throws {
        let launcher = URL(fileURLWithPath: "/bin/sh")
        let first = AlfalfaProcess(launcher: launcher, arguments: [], directory: FileManager.default.temporaryDirectory)
        let stop = EngineStop()
        XCTAssertTrue(stop.begin(first))
        XCTAssertFalse(stop.begin(first), "un segundo clic no manda otro cancelar")
        stop.reset()  // el turno terminó
        XCTAssertFalse(stop.stopping)
        XCTAssertTrue(stop.begin(first), "Detener vuelve a funcionar en el turno siguiente")
    }

    func testTheDeadlineOnlyTerminatesTheStoppedEngine() throws {
        let launcher = URL(fileURLWithPath: "/bin/sh")
        let first = AlfalfaProcess(launcher: launcher, arguments: [], directory: FileManager.default.temporaryDirectory)
        let next = AlfalfaProcess(launcher: launcher, arguments: [], directory: FileManager.default.temporaryDirectory)
        let stop = EngineStop()
        XCTAssertTrue(stop.begin(first))
        XCTAssertTrue(stop.shouldTerminate(first), "sigue sin parar: se termina")
        XCTAssertFalse(stop.shouldTerminate(next), "el motor siguiente no se toca")
        XCTAssertFalse(stop.shouldTerminate(nil))
        stop.reset()
        XCTAssertFalse(stop.shouldTerminate(first), "el turno ya terminó: nada que terminar")
    }
}
