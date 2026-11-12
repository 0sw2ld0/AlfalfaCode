import Foundation

extension BrowserEngine {
    /// Se deriva del almacén real de esta vista, no de un ajuste que pudo cambiar después de abrirla.
    public var profileMode: BrowserProfileMode {
        webView.configuration.websiteDataStore.identifier == nil ? .isolated : .project
    }

    public var profileDescription: String {
        profileMode == .project ? "Guardado para este proyecto · Separado de Safari"
            : "Aislado · Sólo esta sesión · Separado de Safari"
    }
}
