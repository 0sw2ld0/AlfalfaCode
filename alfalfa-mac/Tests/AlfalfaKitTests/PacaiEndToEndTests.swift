@testable import AlfalfaKit
import XCTest

/// De extremo a extremo: `AlfalfaProcess` con el cliente PACAI de la app contra el motor de verdad
/// (`alfalfa --pacai`), con la inferencia sustituida por un modelo falso. Hace falta un Python con las dependencias
/// del motor: `ALFALFA_PYTHON=/ruta/a/python swift test --filter PacaiEndToEndTests`. Sin él, se salta.
final class PacaiEndToEndTests: XCTestCase {

    // MARK: - Constantes

    /// Pide una orden con red (pide permiso) y luego contesta.
    private static let model = """
    import sys
    from unittest.mock import patch
    from alfalfa.terminal import main
    from alfalfa.proveedores.nativo.runtime import NativeRuntime
    async def inference(self, client, endpoint, payload, **kwargs):
        if endpoint == '/api/show':
            return {'capabilities': ['tools']}
        mensajes = payload['messages']
        calls = [] if any(m['role'] == 'tool' for m in mensajes) else [dict(function=dict(
            name='run_command', arguments=dict(argv=['git', 'ls-remote', 'https://ejemplo.com/x'],
                                               purpose='Ver las ramas', needs_network=True)))]
        return dict(message=dict(role='assistant', content='' if calls else 'Hecho', tool_calls=calls), done=True,
                    done_reason='stop', prompt_eval_count=2, eval_count=1)
    with patch.object(NativeRuntime, 'request', inference):
        sys.exit(main(sys.argv[1:]))
    """

    // MARK: - Métodos

    func testATurnWithAPermissionThroughTheRealEngine() throws {
        guard let python = ProcessInfo.processInfo.environment["ALFALFA_PYTHON"] else {
            throw XCTSkip("Sin ALFALFA_PYTHON no hay motor de verdad que lanzar.")
        }
        let repo = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent()
            .deletingLastPathComponent().deletingLastPathComponent()
        let base = FileManager.default.temporaryDirectory.appendingPathComponent("pacai-e2e-\(UUID().uuidString)")
        let project = base.appendingPathComponent("proyecto")
        let home = base.appendingPathComponent("casa")
        for folder in [project, home] {
            try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        }
        addTeardownBlock { try? FileManager.default.removeItem(at: base) }
        let script = base.appendingPathComponent("modelo.py")
        try Self.model.write(to: script, atomically: true, encoding: .utf8)
        let launcher = base.appendingPathComponent("alfalfa")
        let body = "#!/bin/sh\nexec \"\(python)\" \"\(script.path)\" \"$@\"\n"
        try body.write(to: launcher, atomically: true, encoding: .utf8)
        try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: launcher.path)
        // Nada de quien corre la prueba: ni su configuración ni su ~/.alfalfa.
        var environment = ProcessInfo.processInfo.environment.filter { !$0.key.hasPrefix("ALFALFA_") }
        environment["PYTHONPATH"] = repo.path
        environment["HOME"] = home.path
        environment["ALFALFA_HOME"] = home.appendingPathComponent("alfalfa").path
        let client = PacaiClient(opening: .init(task: "Mira las ramas"))
        let arguments = Launcher.pacaiArguments(resume: nil, model: nil, effort: nil, readOnly: false, browser: true)
        let process = AlfalfaProcess(launcher: launcher, arguments: arguments, directory: project,
                                     environment: environment, pacai: client)
        let seen = EngineMessages()
        let finished = expectation(description: "el motor termina")
        process.onMessage = { message in
            seen.append(message)
            if case let .permission(request) = message { process.send(.permission(id: request.id, approved: true)) }
            if case .turnFinished = message { process.closeInput() }
        }
        process.onExit = { _, _ in finished.fulfill() }
        try process.start()
        wait(for: [finished], timeout: 60)
        let messages = seen.all()
        let kinds = messages.map { String(describing: $0).prefix { $0 != "(" } }
        XCTAssertEqual(kinds.first, "session", "\(kinds)")
        XCTAssertTrue(kinds.contains("permission") && kinds.contains("permissionResolved"), "\(kinds)")
        let events = messages.compactMap { message -> AgentEvent? in
            if case let .event(event) = message { return event }
            return nil
        }
        XCTAssertTrue(events.contains { $0.tool?.phase == .end })
        XCTAssertTrue(events.contains { $0.text == "Hecho" })
        guard case let .turnFinished(outcome)? = messages.first(where: {
            if case .turnFinished = $0 { return true } else { return false }
        }) else { return XCTFail("sin turnFinished: \(kinds)") }
        XCTAssertEqual(outcome.state, "completed")
        XCTAssertEqual(kinds.last, "finished")
        // El encargo no vuelve como eco: la app ya lo pintó.
        XCTAssertFalse(events.contains { $0.role == "user" })
    }
}

/// Lo que llega del proceso, desde su cola.
private final class EngineMessages: @unchecked Sendable {
    private let lock = NSLock()
    private var messages: [AlfalfaMessage] = []

    func append(_ message: AlfalfaMessage) { lock.lock(); messages.append(message); lock.unlock() }

    func all() -> [AlfalfaMessage] { lock.lock(); defer { lock.unlock() }; return messages }
}
