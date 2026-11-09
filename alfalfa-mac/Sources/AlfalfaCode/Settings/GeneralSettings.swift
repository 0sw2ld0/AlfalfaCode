import AlfalfaKit
import SwiftUI

/// Ajustes ▸ General: el nombre del pie, la carpeta de los encargos nuevos, el buscador web,
/// el presupuesto, si el Mac se mantiene despierto mientras trabaja un agente y si ve los errores al editar.
struct GeneralSettings: View {

    // MARK: - Propiedades

    @EnvironmentObject private var model: AppModel
    @AppStorage(Preferences.Key.displayName) private var name = ""
    @State private var searxngURL = ""
    @State private var sessionBudgetText = ""
    @State private var monthlyBudgetText = ""
    @FocusState private var budgetFocus: BudgetField?

    private enum BudgetField { case session, month }

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            SettingsTitle(text: "General")
            SettingsBlock(title: "Nombre", detail: "Se muestra en el pie de la barra lateral. Déjalo vacío para "
                          + "usar el de tu cuenta del Mac (\(Preferences.shownName(saved: nil, system: account))).") {
                TextField("Nombre", text: $name, prompt: Text(account))
                    .textFieldStyle(.roundedBorder).frame(maxWidth: 320)
                    .accessibilityLabel("Nombre que se muestra")
            }
            SettingsBlock(title: "Carpeta por defecto",
                          detail: "La carpeta con la que empieza cada encargo nuevo. Sin ella, la última que usaste.") {
                HStack(spacing: 10) {
                    Image(systemName: "folder").foregroundStyle(Theme.secondary)
                    Text(model.defaultFolder.map(Presentation.abbreviate) ?? "La última que usaste")
                        .font(Theme.mono).foregroundStyle(model.defaultFolder == nil ? Theme.muted : Theme.text)
                        .lineLimit(1).truncationMode(.middle)
                        .help(model.defaultFolder ?? "")
                }
                HStack {
                    Button("Elegir…") { model.chooseDefaultFolder() }
                    if model.defaultFolder != nil {
                        Button("Usar la última") { model.defaultFolder = nil }
                    }
                }
            }
            SettingsBlock(title: "Búsqueda web",
                          detail: "Con qué buscan y leen la web los agentes. La búsqueda sale de tu conexión desde "
                          + "AlfalfaCode; las órdenes siguen sin red. Si un buscador falla, se prueba el siguiente.") {
                webSearchControls
            }
            SettingsBlock(title: "Presupuesto",
                          detail: "Tope de gasto en dólares, estimado con los precios de models.dev. Al 80 % avisa; "
                          + "al llegar, el turno se detiene antes de pedir otra respuesta al modelo. Lo de los "
                          + "subagentes cuenta para su sesión. Los modelos locales, de plan o de Alfalfa Cloud con "
                          + "cortesía no gastan. Vacío: sin tope.") {
                budgetField("Por sesión", text: $sessionBudgetText, field: .session) { model.sessionBudget = $0 }
                budgetField("Al mes, en este Mac", text: $monthlyBudgetText, field: .month) { model.monthlyBudget = $0 }
                if let spent = model.monthSpent {
                    Text("Este mes: " + spent.text).font(.system(size: 11.5)).foregroundStyle(Theme.muted)
                }
            }
            SettingsBlock(title: "Mantener el Mac despierto",
                          detail: "Mientras trabaja un agente (con sus subagentes, sus órdenes y la espera de un "
                          + "permiso), el Mac no se duerme por inactividad. La pantalla se apaga igual. Con la tapa "
                          + "cerrada y sin corriente se duerme de todos modos, salvo enchufado y con pantalla "
                          + "externa.") {
                Toggle("Impedir que el Mac se duerma mientras trabaja", isOn: $model.keepAwake)
                    .toggleStyle(.switch)
                    .accessibilityLabel("Mantener el Mac despierto mientras trabaja un agente")
            }
            SettingsBlock(title: "Ver los errores al editar",
                          detail: "Tras cada edición, el agente recibe los errores y avisos nuevos de los archivos "
                          + "que tocó: sourcekit-lsp, pyright y typescript-language-server si están instalados, y "
                          + "el linter del proyecto (ruff, ESLint, SwiftLint). No instala nada ni alarga el turno "
                          + "más de unos segundos. Un proyecto puede apagarlo con .alfalfa/ajustes.json.") {
                Toggle("Mirar los archivos editados con el servidor de lenguaje", isOn: $model.diagnostics)
                    .toggleStyle(.switch)
                    .accessibilityLabel("Ver los errores al editar")
            }
        }
        .task {
            await model.reloadWebSearch()
            searxngURL = model.webSearch?.searxngURL ?? ""
        }
        .onAppear {
            sessionBudgetText = BudgetFormat.field(model.sessionBudget)
            monthlyBudgetText = BudgetFormat.field(model.monthlyBudget)
        }
    }

    // MARK: - Propiedades privadas

    private var account: String { NSFullUserName() }

    @ViewBuilder private var webSearchControls: some View {
        if let settings = model.webSearch {
            Picker("Buscador", selection: Binding(get: { settings.mode }, set: { mode in
                Task { await model.setWebSearchMode(mode) }
            })) {
                ForEach(WebSearchSettings.modes, id: \.self) { mode in
                    Text(WebSearchSettings.label(of: mode)).tag(mode)
                }
            }
            .labelsHidden().fixedSize()
            .accessibilityLabel("Buscador web")
            Text(explanation(settings)).font(.system(size: 11.5)).foregroundStyle(Theme.muted)
                .fixedSize(horizontal: false, vertical: true)
            if model.installingWebSearch {
                HStack(spacing: 8) {
                    ProgressView().controlSize(.small)
                    Text("Instalando la búsqueda web…").font(.system(size: 12)).foregroundStyle(Theme.secondary)
                }
            } else if settings.needsInstall {
                Button("Reintentar la instalación") { Task { await model.installWebSearch() } }
            }
            if settings.mode == "searxng" {
                HStack {
                    TextField("http://127.0.0.1:8888", text: $searxngURL).textFieldStyle(.roundedBorder)
                        .frame(maxWidth: 320).accessibilityLabel("URL de SearXNG")
                    Button("Guardar") { Task { await model.setSearXNGURL(searxngURL) } }
                }
            }
        } else {
            Text("Leyendo el buscador…").font(.system(size: 12)).foregroundStyle(Theme.muted)
        }
        if let error = model.webSearchError {
            Text(error).font(.system(size: 12)).foregroundStyle(Theme.danger)
        }
    }

    /// «Por sesión  [ 5 ] $»: vale al confirmar (Intro) o al salir del campo, nunca a media escritura
    /// (escribir «15» no pasa por un tope de 1 $), y también en las sesiones abiertas. Vacío o 0, sin tope.
    private func budgetField(_ label: String, text: Binding<String>, field: BudgetField,
                             apply: @escaping (Double?) -> Void) -> some View {
        let invalid = BudgetFormat.entry(text.wrappedValue) == .invalid
        func commit() {
            switch BudgetFormat.entry(text.wrappedValue) {
            case .noCap: apply(nil)
            case let .amount(value): apply(value)
            case .invalid: break
            }
        }
        return HStack(spacing: 8) {
            Text(label).frame(width: 140, alignment: .leading)
            TextField("Sin tope", text: text).textFieldStyle(.roundedBorder).frame(width: 90)
                .multilineTextAlignment(.trailing)
                .focused($budgetFocus, equals: field)
                .onSubmit(commit)
                .onChange(of: budgetFocus) { old, new in if old == field && new != field { commit() } }
                .onDisappear(perform: commit)
                .accessibilityLabel("Presupuesto " + label.lowercased() + " en dólares")
            Text("$").foregroundStyle(Theme.secondary)
            if invalid {
                Text("Escribe un importe, como 5 o 12,50").font(.system(size: 11.5)).foregroundStyle(Theme.danger)
            }
        }
    }

    private func explanation(_ settings: WebSearchSettings) -> String {
        if settings.remote { return "Esta instalación usa un servicio CuySearch propio; el selector no lo cambia." }
        if settings.needsInstall {
            if model.installingWebSearch {
                return "AlfalfaCode está instalando su librería de búsqueda; en un momento los encargos nuevos tendrán web."
            }
            return model.webSearchError == nil ? "Falta la librería de búsqueda; AlfalfaCode la instala sola al abrirse."
                : "No se pudo instalar la librería de búsqueda; mientras falte, los agentes no tienen web."
        }
        switch settings.mode {
        case "ollama":
            return settings.hasOllamaKey ? "Usa tu clave de Ollama (Proveedores) y, si falla, los motores gratuitos."
                : "Necesita tu clave de Ollama en Proveedores; mientras no esté, se usan los motores gratuitos."
        case "searxng":
            return "Tu SearXNG, por ejemplo el de CuySearch Manager, y si falla, los motores gratuitos."
        case "apagado":
            return "Los agentes no reciben web_search ni read_web."
        default:
            return "DuckDuckGo, Bing, Brave, Mojeek y Wikipedia, en ese orden. Sin claves."
        }
    }
}
