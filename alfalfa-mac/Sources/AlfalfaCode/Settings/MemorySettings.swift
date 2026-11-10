import AlfalfaKit
import SwiftUI

/// Ajustes ▸ Memoria: lo que AlfalfaCode aprende de cada sesión. Los tres almacenes de memoria
/// (tuyo, de este Mac y del proyecto de la carpeta elegida), editables; las habilidades que
/// escribió, con su origen, para verlas, bloquearlas o borrarlas; lo que la revisión propuso y
/// espera; y los interruptores. Todo pasa por `alfalfa --memoria` y `alfalfa --aprendido`.
struct MemorySettings: View {

    // MARK: - Propiedades

    @EnvironmentObject private var model: AppModel

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            SettingsTitle(text: "Memoria")
            Text(folder.map { "Carpeta: " + $0.path } ?? "Elige una carpeta en «Nuevo encargo» para ver su proyecto.")
                .font(.system(size: 12, design: .monospaced)).foregroundStyle(Theme.secondary)
                .lineLimit(1).truncationMode(.middle)
            if let error {
                Text(error).font(.system(size: 12)).foregroundStyle(Theme.danger)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if let notice {
                Text(notice).font(.system(size: 12)).foregroundStyle(Theme.alfalfa)
            }
            if let learned { switches(learned) }
            if let memory { stores(memory) }
            if let learned {
                proposals(learned)
                skills(learned)
            }
        }
        .task(id: model.folder) { await load() }
    }

    // MARK: - Propiedades privadas

    @State private var memory: MemoryState?
    @State private var learned: LearnedState?
    @State private var drafts: [String: String] = [:]
    @State private var error: String?
    @State private var notice: String?
    @State private var busy = false
    @State private var opened: [String: [String: String]] = [:]
    @State private var confirmingDelete: LearnedSkill?

    private var folder: URL? {
        (model.folder ?? model.defaultFolder).map { URL(fileURLWithPath: $0) }
    }

    private func switches(_ state: LearnedState) -> some View {
        SettingsBlock(title: "Aprender de cada sesión",
                      detail: "Como Hermes Agent: el agente guarda notas cortas y procedimientos que le sirven en "
                      + "sesiones futuras. Cada cambio de la memoria y de las habilidades lo apruebas viendo su diff, "
                      + "también con «Omitir permisos». El repaso al cerrar un turno sólo propone. Lo nuevo entra en "
                      + "la próxima sesión.") {
            ForEach(LearningSettings.Key.allCases, id: \.self) { key in
                Toggle(label(key), isOn: Binding(get: { state.settings.value(key) },
                                                 set: { value in Task { await setting(key.rawValue, value) } }))
                    .toggleStyle(.switch).disabled(busy)
            }
            Toggle("Aprender en esta carpeta", isOn: Binding(get: { state.learnsHere }, set: { value in
                Task { await change(["proyecto", value ? "sí" : "no"]) }
            }))
            .toggleStyle(.switch).disabled(busy || folder == nil)
        }
    }

    private func label(_ key: LearningSettings.Key) -> String {
        switch key {
        case .memory: return "Memoria (herramienta memoria)"
        case .skills: return "Habilidades aprendidas (herramienta habilidad)"
        case .review:
            return "Repasar lo aprendido al cerrar cada turno (modelo barato o el elegido, respeta el presupuesto)"
        }
    }

    private func stores(_ state: MemoryState) -> some View {
        SettingsBlock(title: "Memoria guardada",
                      detail: "Una entrada por bloque, separadas por una línea «§». Cada almacén tiene un tope; "
                      + "al pasarlo, el agente consolida. Se rechazan claves e instrucciones para el agente.") {
            ForEach(state.stores) { store in
                VStack(alignment: .leading, spacing: 6) {
                    HStack {
                        Text(store.title).font(.system(size: 13, weight: .semibold))
                        if let project = store.project {
                            Text(Presentation.abbreviate(project)).font(.system(size: 11)).foregroundStyle(Theme.muted)
                                .lineLimit(1).truncationMode(.middle)
                        }
                        Spacer()
                        let used = MemoryStore.characters(of: draft(store))
                        Text("\(used)/\(store.limit)").font(.system(size: 11, design: .monospaced))
                            .foregroundStyle(used > store.limit ? Theme.danger : Theme.muted)
                    }
                    TextEditor(text: Binding(get: { draft(store) }, set: { drafts[store.store] = $0 }))
                        .font(.system(size: 12, design: .monospaced)).frame(minHeight: 70, maxHeight: 140)
                        .scrollContentBackground(.hidden).padding(4)
                        .background(Theme.field, in: RoundedRectangle(cornerRadius: 6))
                        .accessibilityLabel("Memoria " + store.title)
                    HStack {
                        Button("Guardar") { Task { await save(store) } }
                            .disabled(busy || draft(store) == store.text)
                        Button("Deshacer cambios") { drafts[store.store] = nil }
                            .disabled(busy || draft(store) == store.text)
                    }
                }
                .padding(10).background(Theme.card, in: RoundedRectangle(cornerRadius: 8))
            }
        }
    }

    @ViewBuilder private func proposals(_ state: LearnedState) -> some View {
        if !state.proposals.isEmpty {
            SettingsBlock(title: "Propuestas sin revisar",
                          detail: "Lo que propuso el repaso al cerrar un turno. Se comprueba otra vez al guardarlo.") {
                ForEach(state.proposals) { proposal in
                    VStack(alignment: .leading, spacing: 6) {
                        Text(proposal.title).font(.system(size: 13, weight: .medium))
                        if !proposal.reason.isEmpty {
                            Text(proposal.reason).font(.system(size: 12)).foregroundStyle(Theme.secondary)
                        }
                        DiffBlock(diff: proposal.diff, maxHeight: 160)
                        HStack {
                            Button("Guardar") { Task { await change(["aplicar", proposal.id]) } }
                            Button("Descartar") { Task { await change(["descartar", proposal.id]) } }
                        }
                        .disabled(busy)
                    }
                    .padding(10).background(Theme.card, in: RoundedRectangle(cornerRadius: 8))
                }
            }
        }
    }

    private func skills(_ state: LearnedState) -> some View {
        SettingsBlock(title: "Habilidades aprendidas",
                      detail: "SKILL.md que escribió AlfalfaCode: las tuyas en ~/.alfalfa/skills/aprendidas y las del "
                      + "proyecto en .alfalfa/skills. Bloqueada, el agente ya no la cambia; tú sí puedes.") {
            if state.skills.isEmpty {
                Text("Todavía ninguna.").font(.system(size: 12)).foregroundStyle(Theme.muted)
            }
            ForEach(state.skills) { skill in skillRow(skill) }
        }
        .confirmationDialog("¿Borrar " + (confirmingDelete?.reference ?? "") + "?",
                            isPresented: Binding(get: { confirmingDelete != nil },
                                                 set: { if !$0 { confirmingDelete = nil } })) {
            Button("Borrar", role: .destructive) {
                if let skill = confirmingDelete { Task { await change(["borrar", skill.reference]) } }
            }
            Button("Cancelar", role: .cancel) {}
        } message: {
            Text("Va a la papelera de ~/.alfalfa/aprendido; desde ahí puedes recuperarla a mano.")
        }
    }

    private func skillRow(_ skill: LearnedSkill) -> some View {
        VStack(alignment: .leading, spacing: 5) {
            HStack(spacing: 8) {
                Text(skill.name).font(.system(size: 13, design: .monospaced))
                Text(skill.scope == "proyecto" ? "Proyecto" : "Tuya").font(.system(size: 11, weight: .medium))
                    .foregroundStyle(Theme.secondary)
                if skill.locked {
                    Label("Bloqueada", systemImage: "lock").font(.system(size: 11)).foregroundStyle(Theme.warning)
                }
                Spacer()
                if skill.thisFolder {
                    Button(opened[skill.key] == nil ? "Ver" : "Ocultar") { Task { await toggleOpen(skill) } }
                    Button(skill.locked ? "Desbloquear" : "Bloquear") {
                        Task { await change([skill.locked ? "desbloquear" : "bloquear", skill.reference]) }
                    }
                    Button("Borrar") { confirmingDelete = skill }
                }
            }
            .disabled(busy)
            if !skill.summary.isEmpty {
                Text(skill.summary).font(.system(size: 12)).foregroundStyle(Theme.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Text(skill.originText).font(.system(size: 11)).foregroundStyle(Theme.muted).textSelection(.enabled)
            if let state = skill.stateText {
                Text(state).font(.system(size: 11)).foregroundStyle(Theme.warning)
            }
            if let files = opened[skill.key] {
                ForEach(files.keys.sorted(), id: \.self) { path in
                    Text(path).font(.system(size: 11, weight: .semibold))
                    Text(files[path] ?? "").font(.system(size: 11, design: .monospaced)).textSelection(.enabled)
                        .padding(6).frame(maxWidth: .infinity, alignment: .leading)
                        .background(Theme.field, in: RoundedRectangle(cornerRadius: 6))
                }
            }
        }
        .padding(10).background(Theme.card, in: RoundedRectangle(cornerRadius: 8))
    }

    // MARK: - Métodos privados

    private func draft(_ store: MemoryStore) -> String { drafts[store.store] ?? store.text }

    private func load() async {
        guard let folder else { return }
        busy = true
        defer { busy = false }
        let memoryResult = await model.memoryState(in: folder)
        let learnedResult = await model.learnedState(in: folder)
        memory = memoryResult.state ?? memory
        learned = learnedResult.state ?? learned
        error = memoryResult.error ?? learnedResult.error
    }

    private func save(_ store: MemoryStore) async {
        guard let folder else { return }
        busy = true
        defer { busy = false }
        let result = await model.memoryState(in: folder, ["escribir", store.store], text: draft(store))
        if let state = result.state {
            memory = state
            drafts[store.store] = nil
            notice = "Guardado. Entra en la próxima sesión."
        }
        error = result.error
    }

    private func setting(_ key: String, _ value: Bool) async {
        await change(["ajustes", key, value ? "sí" : "no"])
    }

    private func change(_ arguments: [String]) async {
        guard let folder else { return }
        busy = true
        defer { busy = false }
        let result = await model.learnedState(in: folder, arguments)
        if let state = result.state {
            learned = state
            notice = state.message
            if arguments.first == "aplicar" { memory = await model.memoryState(in: folder).state ?? memory }
        }
        error = result.error
    }

    private func toggleOpen(_ skill: LearnedSkill) async {
        if opened[skill.key] != nil {
            opened[skill.key] = nil
            return
        }
        guard let folder else { return }
        opened[skill.key] = await model.learnedSkillFiles(skill.reference, in: folder) ?? [:]
    }
}
