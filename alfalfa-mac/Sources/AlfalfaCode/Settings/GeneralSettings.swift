import AlfalfaKit
import SwiftUI

/// Ajustes ▸ General: el nombre del pie, la carpeta de los encargos nuevos, el buscador web,
/// si el Mac se mantiene despierto mientras trabaja un agente y si ve los errores al editar.
struct GeneralSettings: View {

    // MARK: - Propiedades

    @EnvironmentObject private var model: AppModel
    @AppStorage(Preferences.Key.displayName) private var name = ""
    @State private var searxngURL = ""

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
