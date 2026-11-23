import AlfalfaKit
import SwiftUI

struct FirstRunGuide: View {
    static let windowID = "primeros-pasos"
    private static let example = "Lee el README de este proyecto y dime cómo ejecutar sus pruebas."

    private enum Step: String, CaseIterable {
        case provider = "Proveedor"
        case task = "Primer encargo"
        case images = "Imágenes"
    }

    @EnvironmentObject private var model: AppModel
    @Environment(\.openSettings) private var openSettings
    @Environment(\.dismissWindow) private var dismissWindow
    @State private var step = Step.provider
    @State private var trialModel: String?
    @State private var trialFolder: URL?
    @State private var startedTrial = false
    @State private var trialError: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            HStack(spacing: 12) {
                LeafIcon(size: 26)
                Text("Primeros pasos").font(.title2.bold())
            }
            Picker("Paso de la guía", selection: $step) {
                ForEach(Step.allCases, id: \.self) { Text($0.rawValue).tag($0) }
            }
            .pickerStyle(.segmented)
            .labelsHidden()

            switch step {
            case .provider: providerStep
            case .task: taskStep
            case .images: imageStep
            }

            Divider()
            HStack {
                Button("Omitir") { close() }.keyboardShortcut(.cancelAction)
                Spacer()
                switch step {
                case .provider: Button("Continuar") { step = .task }.keyboardShortcut(.defaultAction)
                case .task: Button("Siguiente") { step = .images }
                case .images: Button("Listo") { close() }.keyboardShortcut(.defaultAction)
                }
            }
        }
        .padding(24)
        .frame(width: 540)
        .foregroundStyle(Theme.text)
        .background(Theme.background)
    }

    private var providerStep: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Elige el modelo que hará el trabajo").font(.headline)
            Text("Usa Ollama local, conecta tu plan de ChatGPT o guarda la clave de tu proveedor en Ajustes.")
            if !availableModels.isEmpty {
                Picker("Modelo para el ejemplo", selection: Binding(get: { selectedModel ?? "" },
                                                                    set: { trialModel = $0 })) {
                    ForEach(availableModels) { Text($0.label).tag($0.name) }
                }
            }
            Button("Configurar proveedor…") { showProviders() }
            if model.firstRun?.providers.contains(where: { $0.id == "openrouter" && $0.connects }) == true {
                openRouterSetup
            }
        }
    }

    private var openRouterSetup: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("Gratis ahora con OpenRouter").font(.subheadline.bold())
            if let notice = model.firstRun?.notice {
                Label(notice, systemImage: "exclamationmark.triangle")
                    .font(.caption)
                    .foregroundStyle(Theme.warning)
            }
            if let connecting = model.connecting, connecting.provider == "openrouter" {
                HStack {
                    ProgressView().controlSize(.small)
                    Text("Autoriza AlfalfaCode en el navegador.")
                    if let url = connecting.url { Link("Volver a abrir", destination: url) }
                    Button("Cancelar") { model.cancelConnect() }
                }
            } else {
                Button("Conectar con OpenRouter…") { Task { await model.connect("openrouter") } }
                    .disabled(model.connecting != nil)
            }
            if let error = model.providersError {
                Text(error).font(.caption).foregroundStyle(Theme.danger)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private var taskStep: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Prueba AlfalfaCode en una carpeta").font(.headline)
            Text(Self.example)
                .padding(10)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(Theme.field, in: RoundedRectangle(cornerRadius: 8))
            Text("El encargo se abre en solo lectura. El modelo elegido recibe el texto y los archivos que lea.")
                .font(.caption)
                .foregroundStyle(Theme.secondary)
            HStack {
                Button("Elegir carpeta…") {
                    if let chosen = model.pickFolder() { trialFolder = chosen }
                }
                if let trialFolder {
                    Label(trialFolder.lastPathComponent, systemImage: "folder")
                        .lineLimit(1)
                        .help(trialFolder.path)
                }
            }
            if let selectedModel {
                Text("Modelo: \(model.label(for: selectedModel))").font(.caption)
            } else {
                Text("Configura un proveedor para ejecutar el ejemplo.").font(.caption)
            }
            Button("Ejecutar ejemplo") { runExample() }
                .buttonStyle(.borderedProminent)
                .tint(Theme.alfalfa)
                .disabled(model.launcher == nil || trialFolder == nil || selectedModel == nil || startedTrial)
            if startedTrial {
                Text("El encargo está en la ventana principal.").font(.caption)
            }
            if let trialError {
                Text(trialError).font(.caption).foregroundStyle(Theme.danger)
            }
        }
    }

    private var imageStep: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Genera imágenes si tu proyecto las necesita").font(.headline)
            Label("ChatGPT mediante Codex usa tu plan de ChatGPT y requiere iniciar sesión en Codex.",
                  systemImage: "person.crop.circle")
            Label("API de OpenAI usa tu clave y se cobra en tu cuenta de API.", systemImage: "key")
            Label("Ninguno desactiva la generación de imágenes.", systemImage: "nosign")
            Text("AlfalfaCode te pide aprobación antes de enviar cada solicitud de imagen.")
                .font(.caption)
                .foregroundStyle(Theme.secondary)
            Button("Configurar imágenes…") { showProviders(images: true) }
        }
    }

    private var availableModels: [ModelInfo] {
        let configured = Set(model.providers.filter(\.configured).map(\.id))
        return model.models.filter {
            configured.contains($0.provider) && $0.missingKey == nil && !$0.name.localizedCaseInsensitiveContains("embed")
        }
    }

    private var selectedModel: String? {
        let choices = availableModels
        if let trialModel, choices.contains(where: { $0.name == trialModel }) { return trialModel }
        if choices.contains(where: { $0.name == model.model }) { return model.model }
        return choices.first?.name
    }

    private func runExample() {
        guard let selectedModel, let trialFolder, !startedTrial else { return }
        startedTrial = model.startFirstRunTask(Self.example, in: trialFolder, model: selectedModel)
        trialError = startedTrial ? nil : "La carpeta ya no está disponible. Elige otra."
    }

    private func showProviders(images: Bool = false) {
        UserDefaults.standard.set(SettingsSection.proveedores.rawValue, forKey: Preferences.Key.section)
        if images { model.imageSettingsRequest = UUID() }
        openSettings()
    }

    private func close() {
        dismissWindow(id: Self.windowID)
    }
}
