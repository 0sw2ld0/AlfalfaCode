import AlfalfaKit
import AppKit

/// Lo que guarda la ventana de Ajustes para los encargos nuevos.
extension AppModel {

    // MARK: - Propiedades

    var preferences: Preferences { Preferences() }

    /// El modelo con el que empieza cada encargo nuevo. Cambiarlo cambia también el de la pantalla
    /// «Nuevo encargo», para que se vea enseguida.
    var defaultModel: String {
        get { preferences.defaultModel ?? Presentation.defaultModel }
        set {
            preferences.defaultModel = newValue
            model = newValue
        }
    }

    var defaultEffort: String {
        get { preferences.defaultEffort ?? "medium" }
        set {
            preferences.defaultEffort = newValue
            effort = newValue
        }
    }

    var defaultMode: PermissionMode {
        get { preferences.defaultMode ?? .auto }
        set {
            preferences.defaultMode = newValue
            mode = newValue.rawValue
        }
    }

    /// La carpeta fija de los encargos nuevos; `nil` es «la última que usaste».
    var defaultFolder: String? {
        get { preferences.defaultFolder }
        set {
            objectWillChange.send()
            preferences.defaultFolder = newValue
            if let newValue { select(folder: newValue) }
        }
    }

    /// El proveedor del modelo elegido, como lo pone el pie de la barra lateral.
    var currentProviderName: String {
        providerName(ModelProvider.id(of: model, in: models))
    }

    // MARK: - Métodos públicos

    /// Al arrancar: la carpeta, el modelo, el esfuerzo y el modo que se guardaron en Ajustes.
    func applyPreferences() {
        let saved = preferences
        folder = saved.startFolder(recents: recents)
        if let value = saved.defaultModel { model = value }
        if let value = saved.defaultEffort { effort = value }
        if let value = saved.defaultMode { mode = value.rawValue }
    }

    func chooseDefaultFolder() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.allowsMultipleSelection = false
        panel.prompt = "Elegir"
        panel.message = "Carpeta con la que empieza cada encargo nuevo"
        if let current = defaultFolder ?? folder { panel.directoryURL = URL(fileURLWithPath: current) }
        if panel.runModal() == .OK, let url = panel.url { defaultFolder = url.path }
    }
}
