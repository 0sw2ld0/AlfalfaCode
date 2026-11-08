import AlfalfaKit
import SwiftUI

/// Ajustes ▸ General: el nombre del pie, la carpeta de los encargos nuevos y el buscador web.
struct GeneralSettings: View {

    // MARK: - Propiedades

    @EnvironmentObject private var model: AppModel
    @AppStorage(Preferences.Key.displayName) private var name = ""
    @State private var searxngURL = ""
    @State private var installing = false

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
            if !settings.library && !settings.remote {
                HStack(spacing: 10) {
                    Button(installing ? "Instalando…" : "Instalar ahora") {
                        installing = true
                        Task {
                            await model.installWebSearch()
                            installing = false
                        }
                    }
                    .disabled(installing)
                    if installing { ProgressView().controlSize(.small) }
                }
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
        if !settings.library && settings.mode != "apagado" {
            return "Falta la librería de búsqueda en el entorno de AlfalfaCode (pasa tras un git pull sin "
                + "reinstalar). Sin ella los agentes no reciben las herramientas web."
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
