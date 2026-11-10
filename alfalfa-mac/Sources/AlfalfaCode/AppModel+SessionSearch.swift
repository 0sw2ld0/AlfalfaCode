import AlfalfaKit
import Foundation

/// «Buscar» de la barra lateral dentro de las conversaciones: la misma orden que la terminal
/// (`alfalfa --buscar-sesiones CONSULTA --todas --json`) y, al pulsar un resultado, la sesión
/// abierta en ese mensaje.
extension AppModel {

    /// Busca `query` tras una pausa corta; si mientras tanto cambió el texto, no hace nada. La llama
    /// `.task(id:)`: al escribir otra letra la tarea se cancela y su proceso `alfalfa` se termina.
    func searchContent(_ query: String) async {
        let wanted = query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard wanted.count >= SessionSearch.minimumLength, let launcher else {
            contentSearch = nil
            searchingContent = false
            return
        }
        try? await Task.sleep(nanoseconds: 350_000_000)
        guard !Task.isCancelled, search.trimmingCharacters(in: .whitespacesAndNewlines) == wanted else { return }
        searchingContent = true
        let messages = try? await AlfalfaProcess.query(launcher: launcher,
                                                       arguments: SessionSearch.arguments(for: wanted), directory: home)
        // Cancelada (otra letra) o ya vieja: la búsqueda nueva pone lo suyo.
        guard !Task.isCancelled, search.trimmingCharacters(in: .whitespacesAndNewlines) == wanted else { return }
        searchingContent = false
        for case let .sessionSearch(result) in messages ?? [] {
            contentSearch = result
            return
        }
        contentSearch = SessionSearch(query: wanted, hits: [])
    }

    /// Abre la sesión del resultado y la lleva al mensaje encontrado.
    func open(_ hit: SessionSearchHit) {
        if let session = live.first(where: { $0.sessionID == hit.sessionID }) {
            session.searchAnchor = hit.anchor
            route = .live(session.id)
            return
        }
        if let session = dormant[hit.sessionID] {
            session.searchAnchor = hit.anchor
        } else {
            pendingAnchors[hit.sessionID] = hit.anchor
        }
        route = .past(hit.sessionID)
    }
}
