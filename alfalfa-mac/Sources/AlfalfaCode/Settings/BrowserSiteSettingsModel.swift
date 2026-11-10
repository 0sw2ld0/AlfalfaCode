import AlfalfaKit
import Foundation

/// Canal de Ajustes sin modelo ni conversaciones. Toda escritura la confirma y ejecuta el motor.
@MainActor
final class BrowserSiteSettingsModel: ObservableObject {

    // MARK: - Propiedades

    @Published private(set) var state: BrowserSiteSettingsState?
    @Published private(set) var permission: PermissionRequest?
    @Published private(set) var error: String?
    @Published private(set) var busy = false

    // MARK: - Propiedades privadas

    private var process: AlfalfaProcess?
    private var generation = UUID()

    // MARK: - Métodos públicos

    func start(launcher: URL?, directory: URL?) {
        stop()
        state = nil
        error = nil
        guard let launcher, let directory else { return }
        let current = generation
        let process = AlfalfaProcess(launcher: launcher, arguments: ["--navegador-sitios", "--stdio"],
                                     directory: directory)
        process.onMessage = { [weak self] message in
            Task { @MainActor in
                guard let self, self.generation == current else { return }
                self.receive(message)
            }
        }
        process.onExit = { [weak self] code, tail in
            Task { @MainActor in
                guard let self, self.generation == current else { return }
                self.busy = false
                self.permission = nil
                if code != 0 { self.error = tail.isEmpty ? "El motor de Ajustes terminó con un error." : tail }
            }
        }
        self.process = process
        do { try process.start() } catch { self.error = error.localizedDescription }
    }

    func propose(allowed: String, denied: String, strict: Bool) {
        guard process?.isRunning == true, !busy else { return }
        let fields: [String: Any] = ["tipo": "configurar_sitios", "permitidos": lines(allowed),
                                     "bloqueados": lines(denied), "estricto": strict]
        guard let data = try? JSONSerialization.data(withJSONObject: fields) else { return }
        busy = true
        error = nil
        process?.write(line: String(decoding: data, as: UTF8.self))
    }

    func answer(approved: Bool) {
        guard let permission else { return }
        process?.send(.permission(id: permission.id, approved: approved))
        self.permission = nil
    }

    func stop() {
        generation = UUID()
        process?.closeInputAndWait()
        process?.terminate()
        process = nil
        permission = nil
        busy = false
    }

    // MARK: - Métodos privados

    private func receive(_ message: AlfalfaMessage) {
        switch message {
        case let .browserSiteSettings(state):
            self.state = state
            busy = false
        case let .permission(request): permission = request
        case let .error(text): error = text; busy = false
        default: break
        }
    }

    private func lines(_ text: String) -> [String] {
        text.split(whereSeparator: \.isNewline).map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty }
    }
}
