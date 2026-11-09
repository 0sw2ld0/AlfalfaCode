import AlfalfaKit
import Foundation

/// Lo que trae una carpeta de OpenCode, Codex y Claude Code (`alfalfa --importar`).
extension AppModel {

    // MARK: - Métodos públicos

    /// La lista de `folder`. Con `authorize` o `revoke` (claves completas) cambia antes esas
    /// autorizaciones; `network: false` deja sin red a los servidores MCP que autoriza.
    func importedConfig(in folder: URL, authorize: [String] = [], revoke: [String] = [],
                        network: Bool = true) async -> (config: ImportedConfig?, error: String?) {
        guard let launcher else { return (nil, "No se encuentra la orden alfalfa.") }
        var arguments = ["--importar", "--json"]
        for key in authorize { arguments += ["--autorizar", key] }
        for key in revoke { arguments += ["--retirar", key] }
        if !authorize.isEmpty, !network { arguments.append("--sin-internet") }
        do {
            let messages = try await AlfalfaProcess.query(launcher: launcher, arguments: arguments, directory: folder)
            for case let .imported(config) in messages { return (config, config.errors.first) }
            for case let .error(text) in messages { return (nil, text) }
            return (nil, "alfalfa no devolvió la lista.")
        } catch {
            return (nil, error.localizedDescription)
        }
    }
}
