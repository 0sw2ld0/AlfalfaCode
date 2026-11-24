import Foundation

/// El perfil `ui` de PACAI en el cliente de la app: lo que declara en su `hello`.
extension PacaiClient {
    /// Lo que la app sabe dibujar (`render.ui`): el catálogo base entero y las tres acciones.
    static let screenCatalog: [String: Any] = [
        "version": "1", "catalog": "base", "actions": ["send", "set", "open_url"],
        "limits": ["max_nodes": 300, "max_depth": Screen.maxDepth]
    ]
}
