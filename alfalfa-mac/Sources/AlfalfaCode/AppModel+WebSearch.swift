import AlfalfaKit
import Foundation

/// Ajustes ▸ General ▸ Búsqueda web: el buscador de los agentes, a través de `alfalfa --buscador`.
extension AppModel {

    // MARK: - Métodos públicos

    func reloadWebSearch() async {
        await webSearchCommand(["--buscador", "--json"])
    }

    func setWebSearchMode(_ mode: String) async {
        await webSearchCommand(["--buscador", mode, "--json"])
    }

    /// La URL del SearXNG del modo SearXNG; vacía vuelve a la de CuySearch Manager.
    func setSearXNGURL(_ url: String) async {
        let value = url.trimmingCharacters(in: .whitespacesAndNewlines)
        await webSearchCommand(["--fijar-searxng", value.isEmpty ? "-" : value, "--json"])
    }

    /// Al arrancar: si falta la librería de búsqueda (por ejemplo, tras un `git pull` sin reinstalar),
    /// la instala sin preguntar. Una vez por arranque; si falla, Ajustes ofrece reintentar.
    func ensureWebSearch() async {
        guard !triedWebSearchInstall else { return }
        triedWebSearchInstall = true
        await reloadWebSearch()
        if webSearch?.needsInstall == true { await installWebSearch() }
    }

    /// Instala la librería de búsqueda en el entorno de AlfalfaCode. Tarda lo que tarde pip.
    func installWebSearch() async {
        guard !installingWebSearch else { return }
        installingWebSearch = true
        await webSearchCommand(["--instalar-buscador", "--json"])
        installingWebSearch = false
    }

    // MARK: - Métodos privados

    private func webSearchCommand(_ arguments: [String]) async {
        guard let launcher else { return }
        do {
            let messages = try await AlfalfaProcess.query(launcher: launcher, arguments: arguments,
                                                          directory: FileManager.default.homeDirectoryForCurrentUser)
            for message in messages {
                if case let .webSearch(settings) = message {
                    webSearch = settings
                    webSearchError = nil
                    return
                }
                if case let .error(text) = message {
                    webSearchError = text
                    return
                }
            }
        } catch {
            webSearchError = error.localizedDescription
        }
    }
}
