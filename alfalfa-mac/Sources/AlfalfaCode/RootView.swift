import AlfalfaKit
import SwiftUI

struct RootView: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        HStack(spacing: 0) {
            SidebarView()
                .frame(width: 260)
            Divider().overlay(Theme.border)
            switch model.route {
            case .new:
                NewTaskView()
            case let .live(id):
                if let session = model.session(id) {
                    SessionView(session: session)
                } else {
                    NewTaskView()
                }
            case let .past(id):
                PastSessionView(summaryID: id)
            }
        }
        .background(Theme.background)
        .foregroundStyle(Theme.text)
        .overlay(alignment: .top) { TitleBarArea().frame(height: 28) }
        .ignoresSafeArea(.container, edges: .top)
        .alert(alertTitle, isPresented: alertShown, presenting: model.update) { check in
            if case .available = check {
                Button("Actualizar") { model.installUpdate() }
                Button("Ahora no", role: .cancel) { model.update = nil }
            } else {
                Button("Aceptar", role: .cancel) { model.update = nil }
            }
        } message: { check in
            switch check {
            case let .available(info):
                Text("Hay una versión nueva: \(info.latest ?? "?") (tienes \(info.installed)).")
            case let .message(_, text): Text(text)
            default: EmptyView()
            }
        }
    }

    /// Sólo se enseña el aviso con un resultado; mientras busca o instala, el botón muestra el progreso.
    private var alertShown: Binding<Bool> {
        Binding(get: {
            switch model.update {
            case .available, .message: return true
            default: return false
            }
        }, set: { shown in
            if !shown, model.update != .updating { model.update = nil }
        })
    }

    private var alertTitle: String {
        switch model.update {
        case .available: return "Actualización disponible"
        case let .message(title, _): return title
        default: return ""
        }
    }
}

// MARK: Barra lateral

struct SidebarView: View {
    @EnvironmentObject private var model: AppModel
    @State private var collapsed: Set<String> = []
    @State private var searching = false
    @State private var renaming: SidebarRow?
    @State private var newTitle = ""
    @State private var trashing: SidebarRow?

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            VStack(alignment: .leading, spacing: 2) {
                sidebarButton("plus", "Nuevo encargo", selected: model.route == .new) { model.route = .new }
                sidebarButton("magnifyingglass", "Buscar", selected: searching) {
                    searching.toggle()
                    if !searching { model.search = "" }
                }
                if searching {
                    TextField("Buscar sesiones", text: $model.search)
                        .textFieldStyle(.plain)
                        .padding(8)
                        .background(Theme.field, in: RoundedRectangle(cornerRadius: 8))
                        .padding(.horizontal, 2)
                    Toggle("Mostrar archivadas", isOn: $model.showArchived)
                        .toggleStyle(.checkbox).font(.system(size: 12)).foregroundStyle(Theme.secondary)
                        .padding(.horizontal, 6).padding(.top, 4)
                }
            }
            .padding(.horizontal, 8).padding(.top, 40).padding(.bottom, 12)

            ScrollView {
                VStack(alignment: .leading, spacing: 2) {
                    ForEach(model.groups) { group in
                        Button {
                            if collapsed.contains(group.folder) { collapsed.remove(group.folder) } else { collapsed.insert(group.folder) }
                        } label: {
                            Text("\(group.name) \(collapsed.contains(group.folder) ? "▸" : "▾")")
                                .font(.system(size: 12)).foregroundStyle(Theme.muted)
                                .padding(.horizontal, 10).padding(.top, 8).padding(.bottom, 4)
                        }
                        .buttonStyle(.plain)
                        .help(group.folder)
                        if !collapsed.contains(group.folder) {
                            ForEach(group.rows) { row in sessionRow(row).contextMenu { menu(for: row) } }
                        }
                    }
                }
                .padding(.horizontal, 8)
            }
            Spacer(minLength: 0)
            footer
        }
        .background(Theme.sidebar)
        .alert("Renombrar sesión", isPresented: Binding(get: { renaming != nil }, set: { if !$0 { renaming = nil } })) {
            TextField("Título", text: $newTitle)
            Button("Renombrar") { if let row = renaming { model.rename(row, to: newTitle) }; renaming = nil }
            Button("Cancelar", role: .cancel) { renaming = nil }
        } message: {
            Text("Déjalo vacío para volver al encargo original.")
        }
        .alert("¿Mover la sesión a la Papelera?", isPresented: Binding(get: { trashing != nil }, set: { if !$0 { trashing = nil } }),
               presenting: trashing) { row in
            Button("Mover a la Papelera", role: .destructive) { model.trash(row); trashing = nil }
            Button("Cancelar", role: .cancel) { trashing = nil }
        } message: { row in
            Text("«\(row.title)» desaparece de AlfalfaCode. Puedes recuperarla desde la Papelera mientras no la vacíes.")
        }
        .alert("No se pudo cambiar la sesión", isPresented: Binding(get: { model.sessionError != nil },
                                                                     set: { if !$0 { model.sessionError = nil } })) {
            Button("Aceptar", role: .cancel) { model.sessionError = nil }
        } message: {
            Text(model.sessionError ?? "")
        }
    }

    /// Menú de clic derecho de una sesión, como el de Codex.
    @ViewBuilder private func menu(for row: SidebarRow) -> some View {
        if row.sessionID != nil {
            Button { newTitle = row.title; renaming = row } label: { Label("Renombrar", systemImage: "pencil") }
            Button { model.mark(row, "fijada", row.pinned ? "no" : "sí") } label: {
                Label(row.pinned ? "Dejar de fijar" : "Fijar", systemImage: row.pinned ? "pin.slash" : "pin")
            }
            Divider()
            Button { model.fork(row) } label: { Label("Bifurcar desde el último turno", systemImage: "arrow.triangle.branch") }
                .disabled(row.running)
            Menu {
                Button("Id de la sesión") { model.copy(row.sessionID ?? "") }
                if let folder = row.folder { Button("Ruta de la carpeta") { model.copy(folder) } }
                Button("Orden para reanudar") { model.copy(model.resumeCommand(row)) }
            } label: { Label("Copiar", systemImage: "doc.on.doc") }
            if let folder = model.sessionFolder(row) {
                Button { NSWorkspace.shared.activateFileViewerSelecting([folder]) } label: {
                    Label("Mostrar en Finder", systemImage: "folder")
                }
            }
            Divider()
            Button { model.mark(row, "archivada", row.archived ? "no" : "sí") } label: {
                Label(row.archived ? "Desarchivar" : "Archivar", systemImage: "archivebox")
            }
            Button(role: .destructive) { trashing = row } label: { Label("Mover a la Papelera", systemImage: "trash") }
                .disabled(row.running)
        } else {
            Text("La sesión todavía está empezando")
        }
    }

    private func sidebarButton(_ symbol: String, _ title: String, selected: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            HStack(spacing: 10) {
                Image(systemName: symbol).frame(width: 16)
                Text(title)
                Spacer()
            }
            .padding(.horizontal, 10).frame(minHeight: 36)
            .background(selected ? Theme.selection : .clear, in: RoundedRectangle(cornerRadius: 8))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }

    private func sessionRow(_ row: SidebarRow) -> some View {
        Button { model.route = row.route } label: {
            HStack(spacing: 10) {
                StatusDot(color: color(row.tone), pulsing: row.tone == .working)
                Text(row.title).lineLimit(1).truncationMode(.tail)
                Spacer(minLength: 4)
                Text(row.status == "Interrumpida · se puede reanudar" ? "interrumpida" : row.status)
                    .font(.system(size: 11)).foregroundStyle(color(row.tone)).lineLimit(1)
            }
            .padding(.horizontal, 10).frame(minHeight: 36)
            .background(model.route == row.route ? Theme.selection : .clear, in: RoundedRectangle(cornerRadius: 8))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .help(row.status)
    }

    private func color(_ tone: SidebarRow.Tone) -> Color {
        switch tone {
        case .working: return Theme.alfalfa
        case .attention: return Theme.warning
        case .done: return Theme.alfalfa.opacity(0.55)
        case .problem: return Theme.danger
        case .idle: return Theme.muted
        }
    }

    private var footer: some View {
        HStack(spacing: 10) {
            Text(String(NSFullUserName().prefix(1)).uppercased())
                .font(.system(size: 12, weight: .semibold))
                .frame(width: 26, height: 26)
                .background(Theme.alfalfa, in: Circle()).foregroundStyle(Theme.dark)
            (Text(NSFullUserName()) + Text(" · Ollama").foregroundColor(Theme.muted)).lineLimit(1)
            Spacer()
            Button { Task { await model.reload() } } label: {
                Image(systemName: "arrow.clockwise").foregroundStyle(Theme.secondary)
            }
            .buttonStyle(.plain).help("Actualizar sesiones y modelos")
            Button { model.checkForUpdate() } label: {
                if model.update == .checking || model.update == .updating {
                    ProgressView().controlSize(.small)
                } else {
                    Image(systemName: "arrow.down.circle").foregroundStyle(Theme.secondary)
                }
            }
            .buttonStyle(.plain).help("Buscar actualización de AlfalfaCode")
            .disabled(model.update == .checking || model.update == .updating)
        }
        .padding(14)
        .overlay(alignment: .top) { Divider().overlay(Theme.border) }
    }
}

/// Punto de estado de la barra lateral. Mientras el agente trabaja, late despacio;
/// con «Reducir movimiento» activado se queda fijo.
struct StatusDot: View {
    let color: Color
    let pulsing: Bool
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var dim = false

    var body: some View {
        let animate = pulsing && !reduceMotion
        Circle().fill(color)
            .frame(width: 8, height: 8)
            .opacity(animate && dim ? 0.3 : 1)
            .scaleEffect(animate && dim ? 0.8 : 1)
            .animation(animate ? .easeInOut(duration: 0.9).repeatForever(autoreverses: true) : .default, value: dim)
            .onAppear { dim = animate }
            .onChange(of: animate) { _, now in dim = now }
            .accessibilityHidden(true)
    }
}

// MARK: Sesión anterior

struct PastSessionView: View {
    @EnvironmentObject private var model: AppModel
    let summaryID: String

    var body: some View {
        Group {
            if let session = model.dormant[summaryID] {
                SessionView(session: session, notice: model.historyError[summaryID]).id(session.id)
            } else {
                PastSessionInfo(summaryID: summaryID)
            }
        }
        .task(id: summaryID) {
            if let summary = model.summaries.first(where: { $0.id == summaryID }) { model.openPast(summary) }
        }
    }
}

struct PastSessionInfo: View {
    @EnvironmentObject private var model: AppModel
    let summaryID: String

    var body: some View {
        if let summary = model.summaries.first(where: { $0.id == summaryID }) {
            let (label, _) = Presentation.stored(summary.state)
            HStack(spacing: 0) {
                VStack(alignment: .leading, spacing: 16) {
                    HStack(spacing: 10) {
                        LeafIcon()
                        Text(summary.task ?? "Sesión \(summary.id)").font(.system(size: 15, weight: .semibold)).lineLimit(1)
                    }
                    Text(label).foregroundStyle(Theme.warning)
                    Text(Presentation.abbreviate(summary.cwd ?? "")).font(Theme.mono).foregroundStyle(Theme.secondary)
                    Text("\(Presentation.shortModel(summary.model ?? "—")) · \(Presentation.effortLabel(summary.effort ?? "—")) · \(summary.tokens) tokens")
                        .foregroundStyle(Theme.muted)
                    if Presentation.resumable(summary.state), summary.cwd != nil {
                        Button { model.resume(summary) } label: {
                            Label("Reanudar", systemImage: "arrow.clockwise").fontWeight(.semibold)
                                .padding(.horizontal, 16).frame(minHeight: 36)
                                .background(Theme.light, in: RoundedRectangle(cornerRadius: 8))
                                .foregroundStyle(Theme.dark)
                        }
                        .buttonStyle(.plain)
                    }
                    Spacer()
                }
                .padding(.horizontal, 40).padding(.top, 48)
                .frame(maxWidth: .infinity, alignment: .topLeading)
                Divider().overlay(Theme.border)
                if let cwd = summary.cwd {
                    SidePanel(folder: cwd, snapshot: nil, ollama: nil, timeline: nil)
                        .frame(width: 300)
                }
            }
        } else {
            Color.clear
        }
    }
}
