import AlfalfaKit
import AppKit
import SwiftUI
import UniformTypeIdentifiers

struct BrainstormSheet: View {
    private struct ParticipantRow: Identifiable {
        let id = UUID()
        var selection: BrainstormParticipant
    }

    @Environment(\.dismiss) private var dismiss
    @Environment(\.openSettings) private var openSettings
    @EnvironmentObject private var app: AppModel
    @FocusState private var topicFocused: Bool
    @State private var topic = ""
    @State private var reference: URL?
    @State private var referenceError: String?
    @State private var limit = BrainstormConfiguration.defaultTurns
    @State private var moderator: BrainstormParticipant
    @State private var participants: [ParticipantRow]

    let start: (String, BrainstormParticipant, BrainstormConfiguration, URL?) -> Bool

    init(model: String, effort: String,
         start: @escaping (String, BrainstormParticipant, BrainstormConfiguration, URL?) -> Bool) {
        let initial = BrainstormParticipant(model: model, effort: effort)
        _moderator = State(initialValue: initial)
        _participants = State(initialValue: [ParticipantRow(selection: initial), ParticipantRow(selection: initial)])
        self.start = start
    }

    private var cleanTopic: String { topic.trimmingCharacters(in: .whitespacesAndNewlines) }

    private var missingProviders: Bool {
        ([moderator] + participants.map(\.selection)).contains {
            app.info(for: $0.model)?.missingKey != nil
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Lluvia de ideas").font(.headline)

            TextField("Describe la idea o la pregunta", text: $topic, axis: .vertical)
                .textFieldStyle(.roundedBorder)
                .lineLimit(3...5)
                .focused($topicFocused)
            if cleanTopic.count > 4_000 {
                Text("La idea puede tener hasta 4.000 caracteres.")
                    .font(.callout).foregroundStyle(Theme.warning)
            }

            HStack {
                Button { chooseReference() } label: { Label("Adjuntar archivo…", systemImage: "paperclip") }
                if let reference {
                    Text(reference.lastPathComponent)
                        .foregroundStyle(Theme.secondary).lineLimit(1).truncationMode(.middle)
                    Button("Quitar adjunto", systemImage: "xmark.circle") {
                        self.reference = nil
                    }
                    .labelStyle(.iconOnly)
                    .buttonStyle(.borderless)
                }
            }
            if let referenceError {
                Text(referenceError).font(.callout).foregroundStyle(Theme.warning)
            }

            VStack(spacing: 6) {
                participant("Moderador", selection: $moderator)
                ForEach($participants) { $row in
                    if let index = participants.firstIndex(where: { $0.id == row.id }) {
                        HStack(spacing: 6) {
                            participant(participantName(index), selection: $row.selection)
                            if participants.count > BrainstormConfiguration.participantRange.lowerBound {
                                Button(role: .destructive) {
                                    participants.removeAll { $0.id == row.id }
                                } label: { Image(systemName: "minus.circle") }
                                .buttonStyle(.borderless)
                                .accessibilityLabel("Quitar " + participantName(index))
                            }
                        }
                    }
                }
            }
            Button {
                participants.append(ParticipantRow(selection: moderator))
                limit = max(limit, participants.count)
            } label: {
                Label("Añadir participante", systemImage: "plus")
            }
            .disabled(participants.count >= BrainstormConfiguration.participantRange.upperBound)

            Stepper("Máximo de intervenciones: \(limit)", value: $limit,
                    in: participants.count...BrainstormConfiguration.maximumTurns)
            Text("La sala comparte la pregunta, el adjunto y los aportes previos con los modelos elegidos. Se aplican sus cuotas o cargos. El moderador puede cerrar antes si hay acuerdo.")
                .font(.callout).foregroundStyle(Theme.secondary)

            if missingProviders {
                HStack {
                    Text("Configura los proveedores de los modelos elegidos para empezar.")
                        .font(.callout).foregroundStyle(Theme.warning)
                    Button("Proveedores…") {
                        UserDefaults.standard.set(SettingsSection.proveedores.rawValue, forKey: Preferences.Key.section)
                        openSettings()
                    }
                }
            }

            HStack {
                Spacer()
                Button("Cancelar", role: .cancel) { dismiss() }.keyboardShortcut(.cancelAction)
                Button("Empezar") {
                    let configuration = BrainstormConfiguration(limit: limit, participants: participants.map(\.selection))
                    if start(cleanTopic, moderator, configuration, reference) { dismiss() }
                }
                .buttonStyle(.borderedProminent)
                .disabled(cleanTopic.isEmpty || cleanTopic.count > 4_000 || missingProviders)
            }
        }
        .padding(24)
        .frame(width: 610)
        .onAppear { topicFocused = true }
    }

    private func chooseReference() {
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [.plainText] + ["md", "markdown"].compactMap { UTType(filenameExtension: $0) }
        panel.message = "Elige un archivo Markdown o de texto de hasta 16 KiB."
        guard panel.runModal() == .OK, let url = panel.url else { return }
        let attributes = try? url.resourceValues(forKeys: [.isRegularFileKey, .isSymbolicLinkKey, .fileSizeKey])
        guard ["md", "markdown", "txt"].contains(url.pathExtension.lowercased()),
              attributes?.isRegularFile == true, attributes?.isSymbolicLink == false,
              let size = attributes?.fileSize, size <= 16 * 1024 else {
            reference = nil
            referenceError = "Elige un archivo Markdown o de texto de hasta 16 KiB."
            return
        }
        reference = url
        referenceError = nil
    }

    private func participantName(_ index: Int) -> String {
        switch index {
        case 0: return "Propuesta"
        case 1: return "Contraste"
        default: return "Participante \(index + 1)"
        }
    }

    private func participant(_ name: String, selection: Binding<BrainstormParticipant>) -> some View {
        HStack(spacing: 8) {
            Text(name).frame(width: 104, alignment: .leading)
            Menu {
                ModelMenuItems(app: app, groups: app.modelGroups, selected: selection.wrappedValue.model) { name in
                    selection.wrappedValue.model = name
                }
            } label: {
                let model = selection.wrappedValue.model
                let provider = app.providerName(ModelProvider.id(of: model, in: app.models))
                Text(app.label(for: model) + " · " + provider).lineLimit(1).truncationMode(.middle)
            }
            .accessibilityLabel("Modelo de " + name)

            Picker("Esfuerzo de " + name, selection: selection.effort) {
                ForEach(Presentation.efforts, id: \.value) { effort in
                    Text(effort.label).tag(effort.value)
                }
            }
            .labelsHidden()
            .frame(width: 110)
        }
    }
}
