import Foundation

/// Qué hace un clic en un enlace del texto del agente.
///
/// Antes se abría con el manejador del sistema, fuera cual fuera su esquema: el agente podía crear
/// `informe.command` y responder «[Abrir el informe](file:///…/informe.command)», y un clic lo
/// ejecutaba en Terminal, fuera del sandbox. Igual con `shortcuts://…` u otra app.
public enum AgentLinkAction: Equatable {
    /// Una página web: se abre en el navegador.
    case open(URL)
    /// Un archivo o carpeta: se enseña en el Finder, sin abrirlo.
    case reveal(URL)
    /// Cualquier otro esquema: no se abre.
    case refuse
}

public enum AgentLinks {
    public static func action(for url: URL, folder: URL?) -> AgentLinkAction {
        switch url.scheme?.lowercased() {
        case "http", "https":
            return url.host?.isEmpty == false ? .open(url) : .refuse
        case "file":
            return .reveal(url.standardizedFileURL)
        case nil:
            // Un enlace relativo (`[informe](docs/informe.md)`): se busca en la carpeta de la sesión.
            guard let folder, !url.relativeString.isEmpty, !url.relativeString.hasPrefix("//") else { return .refuse }
            let path = url.relativeString.removingPercentEncoding ?? url.relativeString
            let target = path.hasPrefix("/") ? URL(fileURLWithPath: path)
                : folder.appendingPathComponent(path).standardizedFileURL
            return .reveal(target)
        default:
            return .refuse
        }
    }
}
