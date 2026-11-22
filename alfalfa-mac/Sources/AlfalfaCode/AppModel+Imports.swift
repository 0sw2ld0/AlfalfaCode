import AlfalfaKit
import Foundation

/// Lo pendiente de una carpeta para el atajo de la barra lateral.
struct PendingImports: Equatable {
    let folder: URL
    let items: [ImportedItem]
}

/// La hoja «Revisar y autorizar…» de una carpeta, abierta desde la barra lateral o el menú.
struct ImportReviewRequest: Identifiable {
    let id = UUID()
    let folder: URL
}

/// Lo que trae una carpeta de OpenCode, Codex y Claude Code (`alfalfa --importar`).
extension AppModel {

    /// La carpeta en la que se trabaja: la de la sesión que se ve o, en «Nuevo encargo», la elegida.
    var importFolder: URL? {
        switch route {
        case let .live(id): return session(id)?.cwd
        case let .past(id):
            return dormant[id]?.cwd ?? summaries.first { $0.id == id }?.cwd.map { URL(fileURLWithPath: $0) }
        case .antigravity: return nil
        case .new: return folder.map { URL(fileURLWithPath: $0) }
        }
    }

    /// Vuelve a leer lo pendiente de `importFolder` para el atajo de la barra lateral.
    func refreshPendingImports() async {
        guard let folder = importFolder else {
            pendingImports = nil
            return
        }
        if await importedConfig(in: folder).config == nil { pendingImports = nil }
    }

    /// «Revisar y autorizar…» de la carpeta en la que se trabaja (barra lateral, menú, ⇧⌘I).
    func reviewImports() {
        guard let folder = importFolder else { return }
        importReview = ImportReviewRequest(folder: folder)
    }

    /// Se autorizó algo de `folder` desde la hoja: las sesiones abiertas en esa carpeta no lo cargan, y su tarjeta
    /// del aviso lo dice.
    func importsAuthorized(_ names: [String], in folder: URL) {
        for session in live + Array(dormant.values) where session.cwd.standardizedFileURL == folder.standardizedFileURL {
            session.importsAuthorized(names)
        }
    }

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
            for case let .imported(config) in messages {
                // Cada lectura (Ajustes, la hoja, el atajo) pone al día el atajo si es de la carpeta en la que se trabaja.
                if folder.standardizedFileURL == importFolder?.standardizedFileURL {
                    pendingImports = PendingImports(folder: folder, items: config.pending)
                }
                return (config, config.errors.first)
            }
            for case let .error(text) in messages { return (nil, text) }
            return (nil, "alfalfa no devolvió la lista.")
        } catch {
            return (nil, error.localizedDescription)
        }
    }
}
