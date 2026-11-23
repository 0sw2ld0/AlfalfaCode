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
    @AppStorage("BrainstormGroups") private var groupData = Data()
    @State private var selectedGroup: UUID?
    @State private var namingGroup = false
    @State private var groupName = ""

    private var groups: [BrainstormGroup] {
        (try? JSONDecoder().decode([BrainstormGroup].self, from: groupData)) ?? []
    }

    private var configuration: BrainstormConfiguration {
        BrainstormConfiguration(limit: limit, participants: participants.map(\.selection))
    }

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

    private var canStart: Bool {
        !cleanTopic.isEmpty && cleanTopic.count <= 4_000 && !missingProviders
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            Text("Lluvia de ideas")
                .font(.system(size: 22, weight: .semibold))
                .accessibilityAddTraits(.isHeader)

            ViewThatFits(in: .vertical) {
                form.fixedSize(horizontal: false, vertical: true)
                ScrollView { form }
            }

            Divider().overlay(Theme.border)
            footer
        }
        .padding(24)
        .frame(width: 650)
        .frame(maxHeight: 620)
        .background(Theme.background)
        .foregroundStyle(Theme.text)
        .onAppear { topicFocused = true }
        .onChange(of: configuration) { checkSelectedGroup() }
        .onChange(of: moderator) { checkSelectedGroup() }
        .alert("Guardar grupo", isPresented: $namingGroup) {
            TextField("Nombre del grupo", text: $groupName)
            Button("Cancelar", role: .cancel) {}
            Button("Guardar grupo") { saveGroup() }
                .disabled(cleanGroupName.isEmpty || cleanGroupName.count > 80)
        } message: {
            Text("Guarda el moderador, los participantes, sus modelos, el esfuerzo y el límite de intervenciones. Si el nombre ya existe, se actualiza ese grupo.")
        }
    }

    private var form: some View {
        VStack(alignment: .leading, spacing: 18) {
            groupPicker
            question
            roster
            turnLimit
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
        }
    }

    private var groupPicker: some View {
        HStack(spacing: 10) {
            Text("Grupo").font(.subheadline.weight(.semibold))
            Menu {
                Button("Sin grupo") { selectedGroup = nil }
                ForEach(groups) { group in
                    Button(group.name) {
                        selectedGroup = group.id
                        moderator = group.moderator
                        participants = group.configuration.participants.map { ParticipantRow(selection: $0) }
                        limit = group.configuration.limit
                    }
                }
            } label: {
                Text(groups.first { $0.id == selectedGroup }?.name ?? "Sin grupo")
                    .lineLimit(1)
            }
            .accessibilityLabel("Grupo de Lluvia de ideas")
            Button("Guardar como grupo…") {
                groupName = groups.first { $0.id == selectedGroup }?.name ?? ""
                namingGroup = true
            }
        }
    }

    private var cleanGroupName: String { groupName.trimmingCharacters(in: .whitespacesAndNewlines) }

    private func saveGroup() {
        let name = cleanGroupName
        guard !name.isEmpty, name.count <= 80 else { return }
        var updated = groups
        let existing = updated.firstIndex { $0.name.caseInsensitiveCompare(name) == .orderedSame }
        let id = existing.map { updated[$0].id } ?? UUID()
        let group = BrainstormGroup(id: id, name: name, moderator: moderator, configuration: configuration)
        if let existing { updated[existing] = group } else { updated.append(group) }
        guard let data = try? JSONEncoder().encode(updated) else { return }
        groupData = data
        selectedGroup = id
    }

    private func checkSelectedGroup() {
        guard let group = groups.first(where: { $0.id == selectedGroup }) else { return }
        if group.moderator != moderator || group.configuration != configuration { selectedGroup = nil }
    }

    private var question: some View {
        VStack(alignment: .leading, spacing: 9) {
            Text("Pregunta").font(.subheadline.weight(.semibold))
                .accessibilityHidden(true)
            TextField("Describe la idea o la pregunta", text: $topic, axis: .vertical)
                .textFieldStyle(.plain)
                .lineLimit(3...5)
                .padding(10)
                .background(Theme.field, in: RoundedRectangle(cornerRadius: 8))
                .overlay {
                    RoundedRectangle(cornerRadius: 8)
                        .strokeBorder(topicFocused ? Theme.alfalfa : Theme.cardBorder)
                        .allowsHitTesting(false)
                }
                .focused($topicFocused)
                .accessibilityLabel("Pregunta")
            if cleanTopic.count > 4_000 {
                Text("La idea puede tener hasta 4.000 caracteres.")
                    .font(.callout).foregroundStyle(Theme.warning)
            }

            HStack(spacing: 10) {
                Button { chooseReference() } label: { Label("Adjuntar archivo…", systemImage: "paperclip") }
                if let reference {
                    HStack(spacing: 6) {
                        Label(reference.lastPathComponent, systemImage: "doc.text")
                            .lineLimit(1).truncationMode(.middle)
                        Button("Quitar adjunto", systemImage: "xmark") {
                            self.reference = nil
                        }
                        .labelStyle(.iconOnly)
                        .buttonStyle(.borderless)
                    }
                    .font(.callout)
                    .padding(.horizontal, 8).padding(.vertical, 4)
                    .background(Theme.selection, in: RoundedRectangle(cornerRadius: 6))
                    .help(reference.lastPathComponent)
                }
            }
            if let referenceError {
                Text(referenceError).font(.callout).foregroundStyle(Theme.warning)
            }
        }
    }

    private var roster: some View {
        VStack(alignment: .leading, spacing: 10) {
            Grid(alignment: .leading, horizontalSpacing: 12, verticalSpacing: 8) {
                GridRow {
                    Text("Rol").frame(width: 124, alignment: .leading)
                    Text("Modelo").frame(maxWidth: .infinity, alignment: .leading)
                    Text("Esfuerzo").frame(width: 112, alignment: .leading)
                    Spacer().frame(width: 20)
                }
                .font(.callout.weight(.medium))
                .foregroundStyle(Theme.secondary)
                .accessibilityHidden(true)

                Divider().overlay(Theme.border).gridCellUnsizedAxes(.horizontal)
                participant("Moderador", selection: $moderator)
                ForEach($participants) { $row in
                    if let index = participants.firstIndex(where: { $0.id == row.id }) {
                        participant(BrainstormConfiguration.participantName(index), selection: $row.selection,
                                    remove: participants.count > BrainstormConfiguration.participantRange.lowerBound
                                        ? { participants.removeAll { $0.id == row.id } } : nil)
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
        }
    }

    private var turnLimit: some View {
        VStack(alignment: .leading, spacing: 5) {
            HStack(spacing: 12) {
                Text("Máximo de intervenciones").font(.subheadline.weight(.medium))
                HStack(spacing: 0) {
                    Button("Reducir máximo", systemImage: "minus") { limit -= 1 }
                        .disabled(limit <= participants.count)
                    Text(limit.formatted()).monospacedDigit().frame(minWidth: 28)
                        .accessibilityLabel("\(limit) intervenciones")
                    Button("Aumentar máximo", systemImage: "plus") { limit += 1 }
                        .disabled(limit >= BrainstormConfiguration.maximumTurns)
                }
                .labelStyle(.iconOnly)
                .buttonStyle(.borderless)
                .padding(.horizontal, 4).frame(height: 26)
                .background(Theme.selection, in: RoundedRectangle(cornerRadius: 6))
            }
            Text("La sala puede cerrar antes si hay acuerdo.")
                .font(.callout).foregroundStyle(Theme.secondary)
        }
    }

    private var footer: some View {
        HStack(spacing: 10) {
            Text("La pregunta, el adjunto y los aportes se comparten con los modelos elegidos. Se aplican sus cuotas o cargos.")
                .font(.callout).foregroundStyle(Theme.secondary)
                .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 14)
            Button("Cancelar", role: .cancel) { dismiss() }.keyboardShortcut(.cancelAction)
            Button {
                if start(cleanTopic, moderator, configuration, reference) { dismiss() }
            } label: {
                Text("Empezar").fontWeight(.semibold).foregroundStyle(canStart ? Theme.dark : Theme.muted)
            }
            .buttonStyle(.borderedProminent)
            .tint(Theme.alfalfaLight)
            .disabled(!canStart)
        }
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

    private func participant(_ name: String, selection: Binding<BrainstormParticipant>,
                             remove: (() -> Void)? = nil) -> some View {
        GridRow {
            Text(name).frame(width: 124, alignment: .leading)
            Menu {
                ModelMenuItems(app: app, groups: app.modelGroups, selected: selection.wrappedValue.model) { name in
                    selection.wrappedValue.model = name
                }
            } label: {
                let model = selection.wrappedValue.model
                let provider = app.providerName(ModelProvider.id(of: model, in: app.models))
                Text(app.label(for: model) + " · " + provider).lineLimit(1).truncationMode(.middle)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
            .help(app.label(for: selection.wrappedValue.model))
            .accessibilityLabel("Modelo de " + name)

            Picker("Esfuerzo de " + name, selection: selection.effort) {
                ForEach(Presentation.efforts, id: \.value) { effort in
                    Text(effort.label).tag(effort.value)
                }
            }
            .labelsHidden()
            .frame(width: 112)

            Group {
                if let remove {
                    Button("Quitar " + name, systemImage: "minus.circle", action: remove)
                        .labelStyle(.iconOnly)
                        .buttonStyle(.borderless)
                } else {
                    Color.clear.accessibilityHidden(true)
                }
            }
            .frame(width: 20, height: 20)
        }
    }
}

private struct BrainstormGroup: Codable, Identifiable {
    let id: UUID
    let name: String
    let moderator: BrainstormParticipant
    let configuration: BrainstormConfiguration
}
