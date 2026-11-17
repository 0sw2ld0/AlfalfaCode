@testable import AlfalfaKit
import XCTest

/// De extremo a extremo: un `alfalfa --pacai` de verdad con su dueño (aquí, `AlfalfaProcess` con el cliente PACAI de
/// la app) y la app mirándolo en vivo por el socket de la sesión. Hace falta un Python con las dependencias del motor:
/// `ALFALFA_PYTHON=/ruta/a/python swift test --filter PacaiObserverEndToEndTests`. Sin él, se salta.
final class PacaiObserverEndToEndTests: XCTestCase {

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

    func testTheAppWatchesAnotherProcessSessionLive() async throws {
        guard let python = ProcessInfo.processInfo.environment["ALFALFA_PYTHON"] else {
            throw XCTSkip("Sin ALFALFA_PYTHON no hay motor de verdad que lanzar.")
        }
        let repo = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent()
            .deletingLastPathComponent().deletingLastPathComponent()
        // Corta: macOS sólo admite 103 bytes en la ruta del socket de la sesión.
        let base = URL(fileURLWithPath: "/tmp").appendingPathComponent("alfe2e-" + UUID().uuidString.prefix(8))
        let project = base.appendingPathComponent("proyecto")
        let home = base.appendingPathComponent("casa")
        for folder in [project, home] {
            try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        }
        addTeardownBlock { try? FileManager.default.removeItem(at: base) }
        let script = base.appendingPathComponent("modelo.py")
        try Self.model.write(to: script, atomically: true, encoding: .utf8)
        let launcher = base.appendingPathComponent("alfalfa")
        try "#!/bin/sh\nexec \"\(python)\" \"\(script.path)\" \"$@\"\n".write(to: launcher, atomically: true,
                                                                              encoding: .utf8)
        try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: launcher.path)
        var environment = ProcessInfo.processInfo.environment.filter { !$0.key.hasPrefix("ALFALFA_") }
        environment["PYTHONPATH"] = repo.path
        environment["HOME"] = home.path
        let sessions = home.appendingPathComponent("alfalfa/sesiones")
        environment["ALFALFA_HOME"] = sessions.deletingLastPathComponent().path

        // El dueño: lanza el encargo y no contesta el permiso hasta que la app ya esté mirando.
        let owner = AlfalfaProcess(launcher: launcher,
                                   arguments: Launcher.pacaiArguments(resume: nil, model: nil, effort: nil,
                                                                      readOnly: false),
                                   directory: project, environment: environment,
                                   pacai: PacaiClient(opening: .init(task: "Mira las ramas")))
        let asked = expectation(description: "el motor pide permiso")
        let ownerDone = expectation(description: "el dueño termina")
        let state = OwnerState()
        owner.onMessage = { message in
            if case let .session(info) = message { state.set(session: info.id) }
            if case let .permission(request) = message { state.set(permission: request.id); asked.fulfill() }
            if case .turnFinished = message { owner.closeInput() }
        }
        owner.onExit = { _, _ in ownerDone.fulfill() }
        try owner.start()
        await fulfillment(of: [asked], timeout: 60)
        let (sessionID, permissionID) = state.get()
        let id = try XCTUnwrap(sessionID)
        let socket = PacaiObserver.socketURL(sessionID: id, root: sessions)
        XCTAssertTrue(FileManager.default.fileExists(atPath: socket.path))

        // La app mira: la foto trae el permiso pendiente y el corte del historial.
        let result = await PacaiObserver.connect(to: socket)
        guard case let .success(ready) = result else { return XCTFail("\(result)") }
        XCTAssertEqual(ready.sessionID, id)
        XCTAssertGreaterThan(ready.historyUntil ?? 0, 0)
        XCTAssertTrue(ready.state.contains { if case let .permission(r) = $0 { return r.id == permissionID }
                                             return false }, "\(ready.state)")
        let seen = Seen()
        let closed = expectation(description: "la sesión de fuera termina")
        await MainActor.run {
            ready.observer.attach({ seen.append($0) }, onClose: { closed.fulfill() })
        }

        // Sólo el dueño contesta; la app se entera y ve el resto en vivo.
        owner.send(.permission(id: try XCTUnwrap(permissionID), approved: true))
        await fulfillment(of: [ownerDone, closed], timeout: 60)
        let messages = seen.all()
        XCTAssertTrue(messages.contains(.permissionResolved(id: permissionID!, approved: true,
                                                            reason: PacaiClient.relayedAnswer)), "\(messages)")
        XCTAssertTrue(messages.contains { if case let .event(e) = $0 { return e.text == "Hecho" } else { return false } })
        XCTAssertTrue(messages.contains { if case .turnFinished = $0 { return true } else { return false } })
        guard case .finished? = messages.last else { return XCTFail("sin el cierre: \(messages)") }
        XCTAssertFalse(FileManager.default.fileExists(atPath: socket.path), "el motor borra su socket al salir")
    }
}

private final class OwnerState: @unchecked Sendable {
    private let lock = NSLock()
    private var session: String?
    private var permission: String?

    func set(session: String) { lock.lock(); self.session = session; lock.unlock() }
    func set(permission: String) { lock.lock(); self.permission = permission; lock.unlock() }
    func get() -> (String?, String?) { lock.lock(); defer { lock.unlock() }; return (session, permission) }
}

private final class Seen: @unchecked Sendable {
    private let lock = NSLock()
    private var messages: [AlfalfaMessage] = []

    func append(_ batch: [AlfalfaMessage]) { lock.lock(); messages += batch; lock.unlock() }
    func all() -> [AlfalfaMessage] { lock.lock(); defer { lock.unlock() }; return messages }
}
