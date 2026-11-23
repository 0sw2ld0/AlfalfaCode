import AlfalfaKit
import AppKit
import SwiftUI
import UniformTypeIdentifiers

struct BrainstormPrototypeSheet: View {
    private enum ImageProvider: String {
        case codex, openai, none

        var title: String {
            switch self {
            case .codex: return "ChatGPT mediante Codex"
            case .openai: return "API de OpenAI"
            case .none: return "Sin generador disponible"
            }
        }
    }

    @EnvironmentObject private var app: AppModel
    @Environment(\.dismiss) private var dismiss
    @Environment(\.openSettings) private var openSettings
    @State private var selection = 0
    @State private var useReference = false
    @State private var reference: URL?
    @State private var provider: ImageProvider?
    @State private var refreshID = UUID()
    @State private var queryFailed = false
    @State private var launchFailed = false

    let participants: [BrainstormParticipant]
    let directory: URL
    let start: (BrainstormParticipant, URL?) -> Bool

    private var participant: BrainstormParticipant { participants[selection] }
    private var canSeeReference: Bool { app.allowsImages(participant.model) }
    private var missingModel: Bool { app.info(for: participant.model)?.missingKey != nil }
    private var ready: Bool {
        (provider == .codex || provider == .openai) && !missingModel
            && (!useReference || (canSeeReference && reference != nil))
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            VStack(alignment: .leading, spacing: 6) {
                Text("Delegar mockups").font(.title2.weight(.semibold))
                    .accessibilityAddTraits(.isHeader)
                Text("El agente preparará el prompt a partir de la idea y la síntesis de la sala.")
                    .foregroundStyle(Theme.secondary)
            }
            Divider()
            Picker("Agente de la sala", selection: $selection) {
                ForEach(participants.indices, id: \.self) { index in
                    let name = BrainstormConfiguration.participantName(index)
                    Text(name + " · " + app.label(for: participants[index].model)).tag(index)
                }
            }
            if missingModel {
                Text("Configura el proveedor de este agente o elige otro participante.")
                    .font(.callout).foregroundStyle(Theme.warning)
            }
            providerRow
            Divider()
            referenceControls
            if launchFailed {
                Text("No se pudo iniciar el encargo. Comprueba que la síntesis y el agente sigan disponibles.")
                    .font(.callout).foregroundStyle(Theme.warning)
            }
            HStack {
                Spacer()
                Button("Cancelar", role: .cancel) { dismiss() }.keyboardShortcut(.cancelAction)
                Button("Encargar mockups") {
                    if start(participant, useReference ? reference : nil) { dismiss() }
                    else { launchFailed = true }
                }
                .buttonStyle(.borderedProminent)
                .tint(Theme.alfalfa)
                .disabled(!ready)
            }
        }
        .padding(24)
        .frame(width: 510)
        .background(Theme.background)
        .foregroundStyle(Theme.text)
        .task(id: refreshID) { await loadProvider() }
    }

    private var providerRow: some View {
        VStack(alignment: .leading, spacing: 7) {
            LabeledContent("Generador de imágenes") {
                if let provider { Text(provider.title) }
                else if queryFailed { Text("No se pudo comprobar") }
                else { ProgressView().controlSize(.small).accessibilityLabel("Comprobando generador") }
            }
            if provider == ImageProvider.none || queryFailed {
                HStack {
                    if provider == ImageProvider.none {
                        Button("Configurar generador…") {
                            UserDefaults.standard.set(SettingsSection.proveedores.rawValue, forKey: Preferences.Key.section)
                            app.imageSettingsRequest = UUID()
                            openSettings()
                        }
                    }
                    Button("Volver a comprobar") { refreshID = UUID() }
                }
                .font(.callout)
            }
        }
    }

    private var referenceControls: some View {
        VStack(alignment: .leading, spacing: 7) {
            Toggle("Usar referencia visual", isOn: $useReference)
            if useReference {
                if !canSeeReference {
                    Text("Este agente no ve imágenes. Elige un participante con visión.")
                        .font(.callout).foregroundStyle(Theme.warning)
                }
                HStack {
                    Button("Añadir imagen o captura…") { chooseReference() }
                        .disabled(!canSeeReference)
                    if let reference {
                        Text(reference.lastPathComponent).lineLimit(1).truncationMode(.middle)
                        Button("Quitar referencia", systemImage: "xmark.circle") { self.reference = nil }
                            .labelStyle(.iconOnly).buttonStyle(.borderless)
                    }
                }
            }
        }
    }

    private func chooseReference() {
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [.image]
        panel.prompt = "Adjuntar"
        guard panel.runModal() == .OK, let url = panel.url, Attachments.isImage(url) else { return }
        reference = ImagePicker.keep(url)
    }

    private func loadProvider() async {
        provider = nil
        queryFailed = false
        guard let launcher = app.launcher else { queryFailed = true; return }
        let messages = try? await AlfalfaProcess.query(
            launcher: launcher, arguments: ["--proveedor-imagen", "--json"], directory: directory
        )
        guard !Task.isCancelled else { return }
        for case let .imageProvider(_, available, _, _) in messages ?? [] {
            provider = ImageProvider(rawValue: available)
            if provider != nil { return }
        }
        queryFailed = true
    }
}
