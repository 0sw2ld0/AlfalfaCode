import AlfalfaKit
import Foundation

/// Esperando la autorización de un proveedor en el navegador.
struct Connecting: Equatable {
    let provider: String
    /// La URL que se abrió, por si el navegador no se abrió solo.
    var url: URL?
}

/// La primera vez sin proveedor: los modelos «Gratis ahora» y OpenRouter con un clic.
extension AppModel {

    /// `alfalfa --primera-vez --json`: si no hay ningún proveedor, los gratis con los que empezar.
    func loadFirstRun() async {
        guard let launcher else { return }
        let messages = try? await AlfalfaProcess.query(launcher: launcher, arguments: ["--primera-vez", "--json"],
                                                       directory: home)
        for case let .firstRun(value) in messages ?? [] {
            firstRun = value.needed ? value : nil
            return
        }
        firstRun = nil
    }

    /// Conecta OpenRouter en el navegador (OAuth): la clave va al Llavero sin pegarla. Con
    /// `onlyKey` (la hoja de la clave) no cambia el proveedor ni el modelo por defecto; si no,
    /// y no había ningún proveedor, queda elegido su gratis recomendado. Devuelve ese modelo,
    /// "" si se conectó sin elegir modelo, o nil si no se conectó.
    @discardableResult
    func connect(_ provider: String, onlyKey: Bool = false) async -> String? {
        guard let launcher, connecting == nil else { return nil }
        let handle = ConnectHandle()
        connectHandle = handle
        connecting = Connecting(provider: provider)
        providersError = nil
        defer {
            if connectHandle === handle {
                connecting = nil
                connectHandle = nil
            }
        }
        var arguments = ["--conectar", provider, "--json"]
        if onlyKey { arguments.append("--solo-clave") }
        do {
            let messages = try await AlfalfaProcess.query(
                launcher: launcher, arguments: arguments, directory: home,
                progress: { [weak self] message in
                    guard case let .connecting(_, url) = message else { return }
                    Task { @MainActor in
                        guard self?.connectHandle === handle else { return }
                        self?.connecting?.url = URL(string: url)
                    }
                },
                started: { process in handle.started(process) })
            if handle.cancelled { return nil }
            for message in messages {
                if case let .error(text) = message {
                    providersError = text
                    return nil
                }
                if case let .connected(_, chosen) = message {
                    await reload()
                    if let chosen {
                        model = chosen
                        defaultModel = chosen
                    }
                    return chosen ?? ""
                }
            }
            providersError = "No se completó la conexión con \(providerName(provider))."
        } catch {
            // Cancelar corta el proceso sin salida: eso no es un error que enseñar.
            if !handle.cancelled { providersError = error.localizedDescription }
        }
        return nil
    }

    /// Deja de esperar la autorización (el navegador puede quedarse abierto; no pasa nada).
    /// Si el proceso aún no había arrancado, se para en cuanto arranque.
    func cancelConnect() {
        connectHandle?.cancel()
        connectHandle = nil
        connecting = nil
    }

    /// Un gratis de un proveedor que necesita su clave: la hoja de siempre la pide.
    func startFree(with provider: FirstRunProvider) {
        guard let name = provider.recommended ?? provider.models.first else { return }
        choose(name) { [weak self] chosen in
            self?.model = chosen
            self?.defaultModel = chosen
            Task { await self?.loadFirstRun() }
        }
    }
}

/// El proceso de `--conectar`, que se puede cancelar antes incluso de que arranque.
final class ConnectHandle: @unchecked Sendable {
    private let lock = NSLock()
    private var process: AlfalfaProcess?
    private var isCancelled = false

    var cancelled: Bool {
        lock.lock()
        defer { lock.unlock() }
        return isCancelled
    }

    func started(_ process: AlfalfaProcess) {
        lock.lock()
        self.process = process
        let stop = isCancelled
        lock.unlock()
        if stop { process.terminate() }
    }

    func cancel() {
        lock.lock()
        isCancelled = true
        let running = process
        lock.unlock()
        running?.terminate()
    }
}
