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

    /// Conecta OpenRouter en el navegador (OAuth): la clave va al Llavero sin pegarla y su gratis
    /// recomendado queda elegido. Devuelve ese modelo, o nil si no se conectó.
    @discardableResult
    func connect(_ provider: String) async -> String? {
        guard let launcher, connecting == nil else { return nil }
        connecting = Connecting(provider: provider)
        providersError = nil
        defer {
            connecting = nil
            connectProcess = nil
        }
        do {
            let messages = try await AlfalfaProcess.query(
                launcher: launcher, arguments: ["--conectar", provider, "--json"], directory: home,
                progress: { [weak self] message in
                    guard case let .connecting(_, url) = message else { return }
                    Task { @MainActor in self?.connecting?.url = URL(string: url) }
                },
                started: { [weak self] process in
                    Task { @MainActor in self?.connectProcess = process }
                })
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
                    return chosen ?? model
                }
            }
            if connectProcess != nil { providersError = "No se completó la conexión con \(providerName(provider))." }
        } catch {
            providersError = error.localizedDescription
        }
        return nil
    }

    /// Deja de esperar la autorización (el navegador puede quedarse abierto; no pasa nada).
    func cancelConnect() {
        connectProcess?.terminate()
        connectProcess = nil
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
