@testable import AlfalfaKit
import XCTest

final class Collected: @unchecked Sendable {
    private let lock = NSLock()
    private(set) var messages: [AlfalfaMessage] = []
    private(set) var exit: (Int32, String)?
    func add(_ m: AlfalfaMessage) { lock.lock(); messages.append(m); lock.unlock() }
    func setExit(_ code: Int32, _ tail: String) { lock.lock(); exit = (code, tail); lock.unlock() }
    var snapshot: [AlfalfaMessage] { lock.lock(); defer { lock.unlock() }; return messages }
}

/// Un lanzador falso que habla el protocolo por stdin/stdout, para probar el proceso hijo real.
final class ProcessTests: XCTestCase {
    private func script(_ body: String) throws -> URL {
        let url = FileManager.default.temporaryDirectory.appendingPathComponent("alfalfa-fake-\(UUID().uuidString).sh")
        try ("#!/bin/sh\n" + body).write(to: url, atomically: true, encoding: .utf8)
        try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: url.path)
        addTeardownBlock { try? FileManager.default.removeItem(at: url) }
        return url
    }

    private func wait(_ collected: Collected, timeout: TimeInterval = 10) async throws {
        let deadline = Date().addingTimeInterval(timeout)
        while collected.exit == nil && Date() < deadline { try await Task.sleep(nanoseconds: 20_000_000) }
        XCTAssertNotNil(collected.exit, "el proceso no terminó")
    }

    func testCancellingAQueryTerminatesItsProcess() async throws {
        let marker = FileManager.default.temporaryDirectory.appendingPathComponent("alfalfa-pid-\(UUID().uuidString)")
        addTeardownBlock { try? FileManager.default.removeItem(at: marker) }
        let launcher = try script("echo $$ > '\(marker.path)'\nexec sleep 30\n")
        let task = Task {
            try await AlfalfaProcess.query(launcher: launcher, arguments: [], directory: FileManager.default.temporaryDirectory)
        }
        let deadline = Date().addingTimeInterval(10)
        while !FileManager.default.fileExists(atPath: marker.path) && Date() < deadline {
            try await Task.sleep(nanoseconds: 20_000_000)
        }
        let pid = try XCTUnwrap(Int32(String(contentsOf: marker, encoding: .utf8).trimmingCharacters(in: .whitespacesAndNewlines)))
        let started = Date()
        task.cancel()
        _ = try? await task.value
        XCTAssertLessThan(Date().timeIntervalSince(started), 10, "la consulta cancelada siguió esperando")
        XCTAssertNotEqual(kill(pid, 0), 0, "el proceso sigue vivo")
    }

    func testReadsStdoutAndWritesStdinAsJSONL() async throws {
        let launcher = try script("""
        echo '{"version":2,"tipo":"sesion","id":"x","cwd":"'"$(pwd)"'","modelo":"m","esfuerzo":"low"}'
        echo '{"version":2,"tipo":"permiso","id":"p1","argv":["ls"],"proposito":"listar"}'
        echo 'basura no json'
        echo '{"version":2,"tipo":"desconocido"}'
        while IFS= read -r line; do
          case "$line" in
            *'"tipo":"permiso"'*) echo '{"version":2,"tipo":"permiso_resuelto","id":"p1","aprobado":true,"motivo":"Respuesta del cliente"}';;
            *'"tipo":"mensaje"'*) echo '{"version":2,"tipo":"evento","rol":"user","texto":"recibido","hora":1}';;
            *'"tipo":"cancelar"'*) echo '{"version":2,"tipo":"fin","estado":"interrupted","resumen":"","codigo_salida":130,"tokens":0}'; exit 130;;
          esac
        done
        echo '{"version":2,"tipo":"fin","estado":"completed","resumen":"","codigo_salida":0,"tokens":0}'
        """)
        let dir = FileManager.default.temporaryDirectory
        let process = AlfalfaProcess(launcher: launcher, arguments: ["--stdio"], directory: dir)
        let collected = Collected()
        process.onMessage = { collected.add($0) }
        process.onExit = { collected.setExit($0, $1) }
        try process.start()
        process.send(.permission(id: "p1", approved: true))
        process.send(.message("hola"))
        process.send(.cancel)
        try await wait(collected)
        XCTAssertEqual(collected.exit?.0, 130)
        let kinds = collected.snapshot
        XCTAssertEqual(kinds.count, 5)
        guard case let .session(info) = kinds[0] else { return XCTFail("sesión") }
        XCTAssertEqual(URL(fileURLWithPath: info.cwd).resolvingSymlinksInPath().path, dir.resolvingSymlinksInPath().path)
        XCTAssertEqual(kinds[2], .permissionResolved(id: "p1", approved: true, reason: "Respuesta del cliente"))
        XCTAssertEqual(kinds[3], .event(AgentEvent(role: "user", text: "recibido", time: 1)))
        XCTAssertEqual(kinds[4], .finished(Outcome(state: "interrupted", summary: "", exitCode: 130, tokens: 0)))
    }

    func testEndOfInputClosesProcessAndKeepsTail() async throws {
        let launcher = try script("""
        echo 'diagnóstico' >&2
        cat > /dev/null
        printf '{"version":2,"tipo":"fin","estado":"completed","resumen":"","codigo_salida":0,"tokens":3}'
        """)
        let process = AlfalfaProcess(launcher: launcher, arguments: [], directory: FileManager.default.temporaryDirectory)
        let collected = Collected()
        process.onMessage = { collected.add($0) }
        process.onExit = { collected.setExit($0, $1) }
        try process.start()
        process.closeInput()
        try await wait(collected)
        XCTAssertEqual(collected.exit?.0, 0)
        XCTAssertEqual(collected.exit?.1, "diagnóstico\n")
        XCTAssertEqual(collected.snapshot.count, 1, "la última línea sin salto de línea también se lee")
    }

    func testCloseInputAndWaitEndsIdleProcessWithoutOrphan() async throws {
        let launcher = try script("cat > /dev/null\n")
        let process = AlfalfaProcess(launcher: launcher, arguments: [], directory: FileManager.default.temporaryDirectory)
        let collected = Collected()
        process.onExit = { collected.setExit($0, $1) }
        try process.start()
        XCTAssertTrue(process.isRunning)
        process.closeInputAndWait()
        try await wait(collected)
        XCTAssertEqual(collected.exit?.0, 0)
        XCTAssertFalse(process.isRunning)
    }

    func testQueryCollectsMessages() async throws {
        let launcher = try script(#"echo '{"version":2,"tipo":"modelos","modelos":[{"name":"a"}]}'"#)
        let messages = try await AlfalfaProcess.query(launcher: launcher, arguments: ["--modelos", "--json"],
                                                      directory: FileManager.default.temporaryDirectory)
        XCTAssertEqual(messages, [.models([ModelInfo(name: "a")])])
    }

    func testQueryFailureThrows() async throws {
        let launcher = try script("echo fallo >&2; exit 1")
        do {
            _ = try await AlfalfaProcess.query(launcher: launcher, arguments: [], directory: FileManager.default.temporaryDirectory)
            XCTFail("debía fallar")
        } catch {
            XCTAssertTrue(error.localizedDescription.contains("fallo"))
        }
    }

    func testLauncherLocatesRepositoryScript() {
        let url = Launcher.locate(environment: [:], from: [URL(fileURLWithPath: #filePath)])
        XCTAssertEqual(url?.lastPathComponent, "alfalfa")
        XCTAssertEqual(url?.deletingLastPathComponent().lastPathComponent, "scripts")
    }

    /// Dentro de AlfalfaCode.app manda el motor del bundle, aunque la app esté dentro de un clon.
    func testLauncherPrefersBundledEngine() throws {
        let engine = try script("exit 0")
        let url = Launcher.locate(environment: [:], from: [URL(fileURLWithPath: #filePath)], bundled: engine)
        XCTAssertEqual(url, engine)
    }

    /// ALFALFA_REPO sigue mandando: es el modo desarrollo con la app empaquetada.
    func testRepositoryVariableWinsOverBundledEngine() throws {
        let engine = try script("exit 0")
        let repo = URL(fileURLWithPath: #filePath).deletingLastPathComponent()  // …/Tests/AlfalfaKitTests
            .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
        let url = Launcher.locate(environment: ["ALFALFA_REPO": repo.path], from: [], bundled: engine)
        XCTAssertEqual(url?.standardizedFileURL, repo.appendingPathComponent("scripts/alfalfa").standardizedFileURL)
    }

    /// Un motor del bundle que falta o no es ejecutable no se usa: se busca el clon como antes.
    func testMissingBundledEngineFallsBackToClone() {
        let missing = FileManager.default.temporaryDirectory.appendingPathComponent("no-existe-\(UUID().uuidString)")
        let url = Launcher.locate(environment: [:], from: [URL(fileURLWithPath: #filePath)], bundled: missing)
        XCTAssertEqual(url?.deletingLastPathComponent().lastPathComponent, "scripts")
    }

    /// Con `swift run` o en las pruebas no hay .app, y por tanto no hay motor empaquetado.
    func testBundledEngineOnlyInsideAnApp() {
        XCTAssertNil(Launcher.bundledEngine(bundle: Bundle(for: Self.self)))
    }
}
