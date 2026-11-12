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

    /// Mantener el Mac despierto mientras trabaja un agente. Las sesiones abiertas lo aplican ya;
    /// las nuevas lo reciben al arrancar (`--sin-mantener-despierto`).
    var keepAwake: Bool {
        get { preferences.keepAwake }
        set {
            objectWillChange.send()
            preferences.keepAwake = newValue
            for session in live { session.setKeepAwake(newValue) }
        }
    }

    /// Topes en dólares de Ajustes ▸ General ▸ Presupuesto. Las sesiones abiertas los aplican ya;
    /// las nuevas los reciben al arrancar (`--presupuesto`, `--presupuesto-mes`).
    var sessionBudget: Double? {
        get { preferences.sessionBudget }
        set {
            objectWillChange.send()
            preferences.sessionBudget = newValue
            sendBudget()
        }
    }

    var monthlyBudget: Double? {
        get { preferences.monthlyBudget }
        set {
            objectWillChange.send()
            preferences.monthlyBudget = newValue
            sendBudget()
        }
    }

    /// Lo gastado este mes en este Mac según las sesiones abiertas; nil si ninguna lo dijo. Cada una
    /// lee el mismo registro del mes, que sólo crece: el dato más nuevo es el mayor del mes más reciente.
    var monthSpent: BudgetStatus.Limit? {
        let reports = live.compactMap { $0.timeline.budget }
        guard let latest = reports.compactMap(\.monthName).max() else { return reports.map(\.month).max { $0.spent < $1.spent } }
        return reports.filter { $0.monthName == latest }.map(\.month).max { $0.spent < $1.spent }
    }

    /// Ver los errores al editar. Las sesiones abiertas lo aplican desde su siguiente edición;
    /// las nuevas lo reciben al arrancar (`--sin-diagnosticos`).
    var diagnostics: Bool {
        get { preferences.diagnostics }
        set {
            objectWillChange.send()
            preferences.diagnostics = newValue
            for session in live { session.setDiagnostics(newValue) }
        }
    }

    /// Firmar los commits. Las sesiones abiertas lo aplican desde su siguiente `git commit`;
    /// las nuevas lo reciben al arrancar (`--sin-firma`).
    var commitSignature: Bool {
        get { preferences.commitSignature }
        set {
            objectWillChange.send()
            preferences.commitSignature = newValue
            for session in live { session.setCommitSignature(newValue) }
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

    private func sendBudget() {
        let saved = preferences
        // Una sesión con tope propio (puesto desde su panel) lo conserva.
        for session in live {
            session.setBudget(session: saved.sessionBudget(for: session.sessionID), month: saved.monthlyBudget)
        }
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
