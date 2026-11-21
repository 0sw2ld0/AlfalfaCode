import AlfalfaKit
import SwiftUI

struct RootView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.openSettings) private var openSettings
    /// Arrastrando el borde, entre 200 y 420 pt; se recuerda entre aperturas.
    @AppStorage(Preferences.Key.sidebarWidth) private var storedWidth = SidebarWidth.standard
    private var sidebarWidth: Binding<Double> {
        Binding(get: { SidebarWidth.clamp(storedWidth) }, set: { storedWidth = SidebarWidth.clamp($0) })
    }

    var body: some View {
        HStack(spacing: 0) {
            SidebarView()
                .frame(width: sidebarWidth.wrappedValue)
            Divider().overlay(Theme.border)
                .overlay { SidebarResizeHandle(width: sidebarWidth) }
                .zIndex(1)
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
        .modifier(UpdateAlert())
        .sheet(item: $model.keyRequest) { request in KeySheet(request: request) }
        .onAppear(perform: openSettingsIfRequested)
    }

    // MARK: - Métodos privados

    private func openSettingsIfRequested() {
        guard let section = SettingsSection.requestedAtLaunch() else { return }
        UserDefaults.standard.set(section.rawValue, forKey: Preferences.Key.section)
        openSettings()
    }
}

// MARK: - Barra lateral

/// El borde de la barra lateral, como en Claude Code: se arrastra para ensancharla o
/// estrecharla, con el cursor de redimensionar; doble clic vuelve al ancho de siempre.
struct SidebarResizeHandle: View {
    @Binding var width: Double
    @State private var start: Double?

    var body: some View {
        Color.clear
            .frame(width: 9)
            .contentShape(Rectangle())
            .onHover { inside in
                if inside { NSCursor.resizeLeftRight.push() } else { NSCursor.pop() }
            }
            .gesture(
                DragGesture(minimumDistance: 1, coordinateSpace: .global)
                    .onChanged { drag in
                        let origin = start ?? width
                        start = origin
                        width = SidebarWidth.dragged(from: origin, by: drag.translation.width)
                    }
                    .onEnded { _ in start = nil }
            )
            .onTapGesture(count: 2) { width = SidebarWidth.standard }
            .help("Arrastra para cambiar el ancho de la barra lateral; doble clic para volver al de siempre")
            .accessibilityElement()
            .accessibilityLabel("Ancho de la barra lateral")
            .accessibilityValue("\(Int(width)) puntos")
            .accessibilityAdjustableAction { direction in
                switch direction {
                case .increment: width = SidebarWidth.clamp(width + 20)
                case .decrement: width = SidebarWidth.clamp(width - 20)
                @unknown default: break
                }
            }
    }
}

struct SidebarView: View {
    @EnvironmentObject private var model: AppModel
    @State private var collapsed: Set<String> = []
    @State private var searching = false
    @State private var renaming: SidebarRow?
    @State private var newTitle = ""
    @State private var trashing: SidebarRow?
    @State private var removing: SidebarGroup?

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            VStack(alignment: .leading, spacing: 2) {
                sidebarButton("plus", "Nuevo encargo", selected: model.route == .new) { model.route = .new }
                sidebarButton("magnifyingglass", "Buscar", selected: searching) {
                    searching.toggle()
                    if !searching { model.search = ""; model.contentSearch = nil }
                }
                // Sólo si la carpeta en la que se trabaja trae algo sin autorizar: abre «Revisar y autorizar…».
                if let pending = model.pendingImports, let title = ImportReview.shortcutTitle(pending.items) {
                    sidebarButton("puzzlepiece.extension", title, selected: model.importReview != nil,
                                  tint: Theme.warning) { model.reviewImports() }
                        .help(ImportReview.headline(pending.items) + " en " + pending.folder.lastPathComponent
                              + ". Revisar y autorizar (⇧⌘I)")
                        .accessibilityLabel(ImportReview.headline(pending.items) + ". Revisar y autorizar")
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
                    if searching { ContentSearchResults() }
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
                        .contextMenu { folderMenu(for: group) }
                        if !collapsed.contains(group.folder) {
                            // Las sesiones hijas (propose_session) van debajo de su padre, con sangría.
                            ForEach(nested(group.rows), id: \.row.id) { item in
                                sessionRow(item.row).contextMenu { menu(for: item.row) }
                                    .padding(.leading, CGFloat(item.depth) * 14)
                                    .overlay(alignment: .leading) { childMark(item.depth) }
                            }
                        }
                    }
                }
                .padding(.horizontal, 8)
            }
            Spacer(minLength: 0)
            SidebarFooter()
        }
        .background(Theme.sidebar)
        .task(id: model.search) { await model.searchContent(searching ? model.search : "") }
        .task(id: model.importFolder?.path) { await model.refreshPendingImports() }
        .sheet(item: $model.importReview) { request in
            ImportReviewSheet(folder: request.folder).environmentObject(model)
        }
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
        .alert("¿Quitar la carpeta de la lista?", isPresented: Binding(get: { removing != nil },
                                                                       set: { if !$0 { removing = nil } }),
               presenting: removing) { group in
            Button("Quitar de la lista") { model.removeFromList(group); removing = nil }
            Button("Cancelar", role: .cancel) { removing = nil }
        } message: { group in
            Text(FolderMenu.removalMessage(name: group.name, sessions: group.rows.filter { !$0.running && !$0.archived }.count,
                                           running: group.rows.filter(\.running).count))
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
            Button { model.mark(row, "fijada", !row.pinned) } label: {
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
            if let folder = row.folder {
                Button { NSWorkspace.shared.open(URL(fileURLWithPath: folder, isDirectory: true)) } label: {
                    Label("Mostrar en Finder", systemImage: "folder")
                }
                .disabled(!FileManager.default.fileExists(atPath: folder))
            }
            if let history = model.sessionFolder(row) {
                Button { NSWorkspace.shared.activateFileViewerSelecting([history]) } label: {
                    Label("Mostrar historial en Finder", systemImage: "clock.arrow.circlepath")
                }
            }
            Divider()
            Button { model.mark(row, "archivada", !row.archived) } label: {
                Label(row.archived ? "Desarchivar" : "Archivar", systemImage: "archivebox")
            }
            Button(role: .destructive) { trashing = row } label: { Label("Mover a la Papelera", systemImage: "trash") }
                .disabled(row.running)
        } else {
            Text("La sesión todavía está empezando")
        }
    }

    /// Clic derecho en el título de una carpeta: lo que se hace con el proyecto entero.
    @ViewBuilder private func folderMenu(for group: SidebarGroup) -> some View {
        if FolderMenu.applies(to: group) {
            let url = URL(fileURLWithPath: group.folder, isDirectory: true)
            let exists = FileManager.default.fileExists(atPath: group.folder)
            Button { model.newChat(in: url) } label: { Label("Nuevo encargo aquí", systemImage: "plus") }
                .disabled(!exists)
            Divider()
            Menu {
                Button("Nombre") { model.copy(group.name) }
                Button("Ruta") { model.copy(group.folder) }
            } label: { Label("Copiar", systemImage: "doc.on.doc") }
            Button { NSWorkspace.shared.open(url) } label: { Label("Mostrar en Finder", systemImage: "folder") }
                .disabled(!exists)
            Button { model.importReview = ImportReviewRequest(folder: url) } label: {
                Label("Revisar lo importado…", systemImage: "puzzlepiece.extension")
            }
            .disabled(!exists)
            Divider()
            Button { removing = group } label: { Label("Quitar de la lista…", systemImage: "archivebox") }
        }
    }

    private func sidebarButton(_ symbol: String, _ title: String, selected: Bool, tint: Color? = nil,
                               action: @escaping () -> Void) -> some View {
        Button(action: action) {
            HStack(spacing: 10) {
                Image(systemName: symbol).frame(width: 16).foregroundStyle(tint ?? Theme.text)
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
                Group {
                    if let growth = row.growth {
                        // Las sesiones abiertas llevan su alfalfa, del color del estado: se mece
                        // despacio mientras trabaja y crece con el contexto.
                        AlfalfaActivityIcon(phase: row.tone == .working ? .thinking : .done, size: 16,
                                            tint: color(row.tone))
                            .environment(\.alfalfaGrowth, growth)
                    } else {
                        StatusDot(color: color(row.tone), pulsing: row.tone == .working)
                    }
                }
                .frame(width: 16, height: 16)
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
}

// MARK: - Sesiones hijas

extension SidebarView {
    private func nested(_ rows: [SidebarRow]) -> [(row: SidebarRow, depth: Int)] {
        SessionFamily.nest(rows, id: \.sessionID, parent: model.parentID(of:))
    }

    /// Una raya fina a la izquierda de cada hija, como la de las sesiones de Claude.
    @ViewBuilder private func childMark(_ depth: Int) -> some View {
        if depth > 0 {
            Rectangle().fill(Theme.border).frame(width: 1)
                .padding(.leading, CGFloat(depth) * 14 - 6).padding(.vertical, 6)
                .accessibilityHidden(true)
        }
    }
}

/// Punto de estado de la barra lateral. Mientras el agente trabaja, late despacio;
/// con «Reducir movimiento» activado se queda fijo.
struct StatusDot: View {
    let color: Color
    let pulsing: Bool
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    var body: some View {
        // El pulso lo anima el sistema (Core Animation) y no SwiftUI fotograma a fotograma: con
        // `repeatForever` este punto de 8 pt tenía la app al 22 % de CPU mientras esperaba.
        Image(systemName: "circle.fill")
            .font(.system(size: 8))
            .foregroundStyle(color)
            .frame(width: 8, height: 8)
            .symbolEffect(.pulse, options: .repeating, isActive: pulsing && !reduceMotion)
            .accessibilityHidden(true)
    }
}

// MARK: - Sesión anterior

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
                    Text(label).foregroundStyle(summary.isRunningElsewhere ? Theme.secondary : Theme.warning)
                    Text(Presentation.abbreviate(summary.cwd ?? "")).font(Theme.mono).foregroundStyle(Theme.secondary)
                    Text("\(Presentation.shortModel(summary.model ?? "—")) · \(Presentation.effortLabel(summary.effort ?? "—")) · \(summary.tokens) tokens"
                         + (summary.cost.map { " · " + CatalogFormat.cost($0) + " estimado" } ?? ""))
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

// MARK: - Búsqueda en las conversaciones

/// Los mensajes de las sesiones guardadas que tienen lo buscado (`alfalfa --buscar-sesiones`), debajo
/// del cuadro de búsqueda. Al pulsar uno se abre su sesión en ese mensaje.
struct ContentSearchResults: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        let query = model.search.trimmingCharacters(in: .whitespacesAndNewlines)
        if query.count >= SessionSearch.minimumLength {
            VStack(alignment: .leading, spacing: 2) {
                Text("En las conversaciones").font(.system(size: 12)).foregroundStyle(Theme.muted)
                    .padding(.horizontal, 10).padding(.top, 4).padding(.bottom, 4)
                if let found = model.contentSearch, found.query == query {
                    if found.hits.isEmpty {
                        note(model.searchingContent ? "Buscando…" : "Ningún mensaje lo menciona.")
                    }
                    ForEach(found.hits) { hit in row(hit) }
                    if !found.upToDate { note("Aún se están indexando sesiones antiguas.") }
                } else {
                    note("Buscando…")
                }
                Divider().overlay(Theme.border).padding(.horizontal, 10).padding(.vertical, 6)
            }
        }
    }

    private func note(_ text: String) -> some View {
        Text(text).font(.system(size: 12)).foregroundStyle(Theme.secondary).padding(.horizontal, 10).padding(.bottom, 4)
    }

    private func row(_ hit: SessionSearchHit) -> some View {
        Button { model.open(hit) } label: {
            VStack(alignment: .leading, spacing: 3) {
                Text(hit.title).lineLimit(1).truncationMode(.tail)
                Text(hit.fragment.split(whereSeparator: \.isNewline).joined(separator: " "))
                    .font(.system(size: 12)).foregroundStyle(Theme.secondary).lineLimit(2)
                Text(hit.roleLabel + (hit.external ? " · de fuera" : "") + (hit.date > 0 ? " · "
                    + Date(timeIntervalSince1970: hit.date).formatted(date: .abbreviated, time: .shortened) : ""))
                    .font(.system(size: 11)).foregroundStyle(Theme.muted)
            }
            .padding(.horizontal, 10).padding(.vertical, 6)
            .frame(maxWidth: .infinity, alignment: .leading)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .help(hit.folder.map { Presentation.abbreviate($0) } ?? hit.title)
    }
}
