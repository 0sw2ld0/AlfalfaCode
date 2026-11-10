import Foundation

extension AppModel {
    /// La carpeta de la sesión visible; «Nuevo encargo» sólo se usa cuando ésa es la ruta activa.
    var browserSiteProject: URL? {
        switch route {
        case .new: return folder.map { URL(fileURLWithPath: $0) }
        case let .live(id): return session(id)?.cwd
        case let .past(id):
            if let stored = dormant[id] { return stored.cwd }
            return summaries.first(where: { $0.id == id }).flatMap(\.cwd).map { URL(fileURLWithPath: $0) }
        }
    }
}
