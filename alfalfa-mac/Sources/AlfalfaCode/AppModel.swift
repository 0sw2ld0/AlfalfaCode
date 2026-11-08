import AlfalfaKit
import AppKit
import Foundation

enum Route: Equatable {
    case new
    case live(UUID)
    case past(String)
}

struct SidebarRow: Identifiable {
    let id: String
    let title: String
    let status: String
    let tone: Tone
    let route: Route
    let date: Double
    /// Id de la sesión guardada (las vivas lo tienen en cuanto alfalfa lo anuncia).
    var sessionID: String? = nil
    var folder: String? = nil
    var pinned = false
    var archived = false
    /// Una sesión con turno en marcha no se puede eliminar ni bifurcar.
    var running = false

    enum Tone { case working, attention, done, problem, idle }
}

struct SidebarGroup: Identifiable {
    let folder: String
    let rows: [SidebarRow]
    var title: String? = nil
    var id: String { title ?? folder }
    var name: String { title ?? URL(fileURLWithPath: folder).lastPathComponent }
}

enum Presentation {
    static let efforts: [(value: String, label: String)] = [
        ("low", "Bajo"), ("medium", "Medio"), ("high", "Alto"), ("xhigh", "Muy alto"), ("max", "Máximo"),
    ]
    static let defaultModel = "deepseek-v4.1-flash:cloud"

    static func effortLabel(_ value: String) -> String { efforts.first { $0.value == value }?.label ?? value }

    static func shortModel(_ name: String) -> String {
        name.hasSuffix(":cloud") ? String(name.dropLast(6)) : name
    }

    static func abbreviate(_ path: String) -> String {
        let home = NSHomeDirectory()
        return path.hasPrefix(home) ? "~" + path.dropFirst(home.count) : path
    }

    /// Estado guardado en disco de una sesión que esta app no tiene abierta.
    static func stored(_ state: String) -> (label: String, tone: SidebarRow.Tone) {
        switch state {
        case "completed": return ("completada", .done)
        case "failed": return ("fallida", .problem)
        case "blocked": return ("bloqueada", .problem)
        case "corrupta": return ("ilegible", .problem)
        // Una bifurcación recién hecha: espera su primer mensaje.
        case "created": return ("lista", .done)
        default: return ("Interrumpida · se puede reanudar", .attention)
        }
    }

    /// Se puede reanudar sin mensaje: sólo lo que se quedó a medias.
    static func resumable(_ state: String) -> Bool { !["completed", "corrupta", "created"].contains(state) }
}

/// Lo que enseña el aviso de «Buscar actualización».
enum UpdateCheck: Equatable {
    case checking
    case available(UpdateInfo)
    case updating
    case message(title: String, text: String)
}

@MainActor
final class AppModel: ObservableObject {
    @Published var route: Route = .new {
        didSet { releaseParked(leaving: oldValue) }
    }
    @Published private(set) var live: [LiveSession] = []
    /// Sesiones guardadas abiertas con su historial, sin proceso hasta el primer mensaje.
    @Published private(set) var dormant: [String: LiveSession] = [:]
    private var openedAtLaunch = false
    /// Texto que espera en el cuadro de una sesión bifurcada al abrirla.
    private var pendingDrafts: [String: String] = [:]
    @Published private(set) var historyError: [String: String] = [:]
    @Published private(set) var summaries: [SessionSummary] = []
    @Published private(set) var models: [ModelInfo] = []
    @Published private(set) var loadError: String?
    @Published var model = Presentation.defaultModel
    @Published var effort = "medium"
    @Published var readOnly = false
    /// Modo de permisos para los encargos nuevos.
    @Published var mode = PermissionMode.auto.rawValue
    @Published var draft = ""
    @Published private(set) var recents: [String]
    @Published var folder: String?
    @Published private(set) var newSnapshot: GitSnapshot?
    @Published private(set) var newOllama: Bool?
    @Published var search = ""
    @Published var showArchived = false
    @Published var sessionError: String?
    @Published var update: UpdateCheck?
    /// Imágenes del nuevo encargo.
    @Published var draftImages: [URL] = []
    private var pasteMonitor: Any?

    let launcher: URL?
    private let home = FileManager.default.homeDirectoryForCurrentUser
    private static let recentsKey = "recentFolders"

    init() {
        launcher = Launcher.locate()
        recents = UserDefaults.standard.stringArray(forKey: Self.recentsKey) ?? []
        folder = recents.first
        if launcher == nil { loadError = AlfalfaError.launcherNotFound.localizedDescription }
        // ⌘V con una imagen en el portapapeles la adjunta al compositor visible, como en Codex.
        pasteMonitor = NSEvent.addLocalMonitorForEvents(matching: .keyDown) { [weak self] event in
            guard event.modifierFlags.intersection(.deviceIndependentFlagsMask) == .command,
                  event.charactersIgnoringModifiers == "v", let self,
                  let url = ImagePicker.pastedImage() else { return event }
            MainActor.assumeIsolated { self.attachPasted(url) }
            return nil
        }
    }

    private func attachPasted(_ url: URL) {
        switch route {
        case .new: ImagePicker.add([url], to: &draftImages)
        case let .live(id):
            if let session = session(id) { ImagePicker.add([url], to: &session.draftImages) }
        case .past: break
        }
    }

    // MARK: Carga

    func reload() async {
        guard let launcher else { return }
        do {
            let messages = try await AlfalfaProcess.query(launcher: launcher, arguments: ["--sesiones", "--json"], directory: home)
            for case let .sessions(rows) in messages { summaries = rows }
            loadError = nil
            // Para revisar la interfaz sin clics: ALFALFA_ABRIR_SESION=ID abre esa sesión al arrancar.
            if let wanted = ProcessInfo.processInfo.environment["ALFALFA_ABRIR_SESION"], !openedAtLaunch,
               summaries.contains(where: { $0.id == wanted }) {
                openedAtLaunch = true
                route = .past(wanted)
            }
        } catch {
            loadError = "No se pudieron leer las sesiones: \(error.localizedDescription)"
        }
        do {
            let messages = try await AlfalfaProcess.query(launcher: launcher, arguments: ["--modelos", "--json"], directory: home)
            for case let .models(rows) in messages { models = rows }
        } catch {
            models = []
            if loadError == nil { loadError = "No se pudo consultar Ollama para listar modelos." }
        }
        if folder == nil { folder = folderChoices.first }
        await refreshNewPanel()
    }

    var modelChoices: [String] {
        // Los modelos de embeddings no conversan: no se ofrecen para un chat.
        var names = models.map(\.name).filter { !$0.lowercased().contains("embed") }
        if !names.contains(Presentation.defaultModel) { names.insert(Presentation.defaultModel, at: 0) }
        if !names.contains(model) { names.insert(model, at: 0) }
        return names
    }

    /// Recientes elegidas por el usuario y carpetas de sesiones anteriores.
    var folderChoices: [String] {
        var seen = Set<String>()
        let fromSessions = summaries.sorted { $0.date > $1.date }.compactMap(\.cwd)
        let all = (recents + fromSessions).filter { path in
            FileManager.default.fileExists(atPath: path) && seen.insert(path).inserted
        }
        var result = Array(all.prefix(5))
        if let folder, !result.contains(folder) { result.insert(folder, at: 0) }
        return result
    }

    /// Sesiones interrumpidas de la carpeta elegida, las más recientes primero.
    var resumable: [SessionSummary] {
        guard let folder else { return [] }
        return summaries.filter { $0.cwd == folder && Presentation.stored($0.state).tone == .attention }
            .sorted { $0.date > $1.date }.prefix(3).map { $0 }
    }

    // MARK: Actualización

    /// Compara la versión instalada con la última etiqueta del repositorio de AlfalfaCode.
    func checkForUpdate() {
        guard let launcher else {
            update = .message(title: "No se pudo buscar", text: AlfalfaError.launcherNotFound.localizedDescription)
            return
        }
        update = .checking
        Task {
            update = await runUpdate(launcher: launcher, arguments: ["--buscar-actualizacion", "--json"]) { info in
                info.available ? .available(info) : .message(title: "AlfalfaCode está al día", text: info.text)
            }
        }
    }

    func installUpdate() {
        guard let launcher else {
            update = .message(title: "No se pudo actualizar", text: AlfalfaError.launcherNotFound.localizedDescription)
            return
        }
        update = .updating
        Task {
            update = await runUpdate(launcher: launcher, arguments: ["--actualizar", "--json"]) { info in
                guard info.updated else { return .message(title: "AlfalfaCode está al día", text: info.text) }
                return .message(title: "AlfalfaCode actualizado",
                                text: info.text + " Cierra y vuelve a abrir la app para usar la versión nueva.")
            }
        }
    }

    private func runUpdate(launcher: URL, arguments: [String],
                           outcome: (UpdateInfo) -> UpdateCheck) async -> UpdateCheck {
        do {
            let messages = try await AlfalfaProcess.query(launcher: launcher, arguments: arguments, directory: home)
            for message in messages {
                if case let .update(info) = message { return outcome(info) }
                if case let .error(text) = message { return .message(title: "No se pudo actualizar", text: text) }
            }
            return .message(title: "No se pudo actualizar", text: "alfalfa no dio respuesta.")
        } catch {
            return .message(title: "No se pudo actualizar", text: error.localizedDescription)
        }
    }

    // MARK: Carpeta

    func chooseFolder() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.allowsMultipleSelection = false
        panel.prompt = "Elegir"
        panel.message = "Carpeta de trabajo para el encargo"
        if panel.runModal() == .OK, let url = panel.url { select(folder: url.path) }
    }

    func select(folder path: String) {
        folder = path
        recents = ([path] + recents.filter { $0 != path }).prefix(8).map { $0 }
        UserDefaults.standard.set(recents, forKey: Self.recentsKey)
        Task { await refreshNewPanel() }
    }

    func refreshNewPanel() async {
        guard let folder else { newSnapshot = nil; return }
        async let git = Workspace.snapshot(of: URL(fileURLWithPath: folder))
        async let reachable = Workspace.ollamaReachable()
        newSnapshot = await git
        newOllama = await reachable
    }

    // MARK: Sesiones

    func startTask() {
        var task = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        if task.isEmpty, !draftImages.isEmpty { task = "Mira la imagen adjunta." }
        guard !task.isEmpty, let folder, let launcher else { return }
        select(folder: folder)
        let session = LiveSession(cwd: URL(fileURLWithPath: folder), title: task, resumeID: nil,
                                  model: model, effort: effort, readOnly: readOnly, mode: mode)
        open(session, launcher: launcher, task: task, images: draftImages)
        draft = ""
        draftImages = []
    }

    func resume(_ summary: SessionSummary) {
        guard let launcher, let cwd = summary.cwd else { return }
        if let existing = live.first(where: { $0.sessionID == summary.id && $0.timeline.status.isActive }) {
            route = .live(existing.id)
            return
        }
        let session = LiveSession(cwd: URL(fileURLWithPath: cwd), title: summary.task ?? "Sesión \(summary.id)",
                                  resumeID: summary.id, model: summary.model ?? model,
                                  effort: summary.effort ?? effort, readOnly: readOnly,
                                  mode: summary.mode ?? PermissionMode.auto.rawValue)
        open(session, launcher: launcher, task: nil)
    }

    private func open(_ session: LiveSession, launcher: URL, task: String?, images: [URL] = []) {
        session.onChange = { [weak self] in self?.objectWillChange.send() }
        live.insert(session, at: 0)
        route = .live(session.id)
        session.start(launcher: launcher, task: task, images: images)
        Task {
            try? await Task.sleep(nanoseconds: 1_500_000_000)
            await reload()
        }
    }

    func session(_ id: UUID) -> LiveSession? { live.first { $0.id == id } }

    /// Como abrir un hilo en Codex: se carga la conversación y el siguiente mensaje abre un
    /// turno nuevo en la misma sesión (`--reanudar ID "mensaje"`).
    func openPast(_ summary: SessionSummary) {
        if let existing = live.first(where: { $0.sessionID == summary.id && $0.timeline.status.isActive }) {
            route = .live(existing.id)
            return
        }
        guard dormant[summary.id] == nil, let launcher, let cwd = summary.cwd, summary.state != "corrupta" else { return }
        let session = LiveSession(cwd: URL(fileURLWithPath: cwd), title: summary.displayTitle ?? "Sesión \(summary.id)",
                                  resumeID: summary.id, model: summary.model ?? model,
                                  effort: summary.effort ?? effort, readOnly: readOnly,
                                  mode: summary.mode ?? PermissionMode.auto.rawValue)
        session.onChange = { [weak self] in self?.objectWillChange.send() }
        session.onWake = { [weak self, weak session] in
            guard let self, let session else { return }
            self.dormant[summary.id] = nil
            // Una sesión que ya despertó antes vuelve a despertar al cerrarse su proceso: no se duplica.
            if !self.live.contains(where: { $0 === session }) { self.live.insert(session, at: 0) }
            self.route = .live(session.id)
            Task {
                try? await Task.sleep(nanoseconds: 1_500_000_000)
                await self.reload()
            }
        }
        dormant[summary.id] = session
        session.pendingDraft = pendingDrafts.removeValue(forKey: summary.id)
        let resumable = Presentation.resumable(summary.state)
        Task { await loadHistory(summary.id, into: session, launcher: launcher, resumable: resumable) }
    }

    private func loadHistory(_ id: String, into session: LiveSession, launcher: URL, resumable: Bool) async {
        do {
            let messages = try await AlfalfaProcess.query(launcher: launcher, arguments: ["--historial", id, "--json"],
                                                         directory: home)
            for case let .history(_, events, wrapUp, context, tokens) in messages {
                session.restore(Timeline(history: events, wrapUp: wrapUp, context: context, tokens: tokens),
                                launcher: launcher, resumable: resumable)
                historyError[id] = nil
                return
            }
            for case let .error(text) in messages { historyError[id] = text }
        } catch {
            historyError[id] = error.localizedDescription
        }
        // Sin historial se puede seguir igual: la sesión queda vacía pero continuable.
        session.restore(Timeline(history: [], wrapUp: nil), launcher: launcher, resumable: resumable)
    }

    func shutdown() { live.forEach { $0.shutdown() } }

    /// Al salir de una sesión se cierra su stdin (de inmediato o al terminar el turno): no quedan procesos huérfanos.
    private func releaseParked(leaving old: Route) {
        guard old != route else { return }
        if case let .live(id) = route { session(id)?.returned() }
        if case let .live(id) = old { session(id)?.leave() }
    }

    // MARK: Acciones sobre una sesión (clic derecho)

    /// `alfalfa --marcar ID CAMPO VALOR` y recarga la barra lateral.
    func mark(_ row: SidebarRow, _ field: String, _ value: String) {
        guard let id = row.sessionID else { return }
        Task { await manage(["--marcar", id, field, value]) }
    }

    func rename(_ row: SidebarRow, to title: String) {
        let clean = title.trimmingCharacters(in: .whitespacesAndNewlines)
        mark(row, "titulo", clean == row.title ? "" : clean)
    }

    func trash(_ row: SidebarRow) {
        guard let id = row.sessionID, !row.running else { return }
        if case let .live(uuid) = row.route, let session = session(uuid) {
            session.shutdown(interrupt: false)
            live.removeAll { $0.id == uuid }
        }
        if route == row.route { route = .new }
        Task { await manage(["--eliminar-sesion", id]) }
    }

    /// Crea la sesión bifurcada desde el último turno terminado y la abre, lista para otra indicación.
    func fork(_ row: SidebarRow) {
        guard let id = row.sessionID, !row.running else { return }
        fork(sessionID: id, turn: nil, draft: nil)
    }

    /// Bifurca hasta el final de `turn` (o el último terminado) y abre la copia dormida, con
    /// `draft` en el cuadro: así se vuelve a un mensaje para cambiarlo o se prueba otro camino.
    func fork(sessionID id: String, turn: String?, draft: String?) {
        Task {
            // Entre turnos el proceso sigue abierto y guarda el cerrojo: se cierra antes de copiar.
            if let open = live.first(where: { $0.sessionID == id }), !(await open.releaseProcess()) {
                sessionError = "La sesión está trabajando; espera a que termine el turno para volver o bifurcar."
                return
            }
            var arguments = ["--solo-bifurcar", id]
            if let turn { arguments += ["--turno", turn] }
            guard let messages = await manage(arguments) else { return }
            for case let .sessionChanged(newID, _) in messages {
                if let draft { pendingDrafts[newID] = draft }
                route = .past(newID)
            }
        }
    }

    /// Volver al primer mensaje de una sesión: un encargo nuevo en la misma carpeta con ese texto.
    func restart(in folder: URL, with text: String) {
        newChat(in: folder)
        draft = text
    }

    /// La fila de la barra lateral de una sesión abierta, para las órdenes con «/».
    func row(for session: LiveSession) -> SidebarRow? {
        guard let id = session.sessionID else { return nil }
        return groups.flatMap(\.rows).first { $0.sessionID == id }
    }

    // MARK: Chats de la app de Ollama

    /// Títulos y recuentos de los chats de Ollama; el contenido sólo se lee al cargar uno.
    func ollamaChats() async -> (chats: [OllamaChat], error: String?) {
        guard let launcher else { return ([], "No se encuentra la orden alfalfa.") }
        do {
            let messages = try await AlfalfaProcess.query(launcher: launcher, arguments: ["--chats-ollama", "--json"],
                                                         directory: home)
            for case let .ollamaChats(rows) in messages { return (rows, nil) }
            for case let .error(text) in messages { return ([], text) }
            return ([], "Ollama no devolvió la lista de chats.")
        } catch {
            return ([], error.localizedDescription)
        }
    }

    /// Copia el chat en una sesión nueva de la carpeta elegida y la abre lista para seguir.
    func importOllama(_ chat: OllamaChat) async -> String? {
        guard let launcher else { return "No se encuentra la orden alfalfa." }
        guard let folder else { return "Elige antes la carpeta donde trabajará la sesión." }
        do {
            let messages = try await AlfalfaProcess.query(
                launcher: launcher, arguments: ["--importar-ollama", chat.id, "--esfuerzo", effort, "--json"],
                directory: URL(fileURLWithPath: folder))
            for case let .error(text) in messages { return text }
            for case let .sessionChanged(id, _) in messages {
                await reload()
                route = .past(id)
                return nil
            }
            return "No se pudo cargar el chat."
        } catch {
            return error.localizedDescription
        }
    }

    /// `/nuevo`: la pantalla de encargo nuevo con la misma carpeta.
    func newChat(in folder: URL) {
        select(folder: folder.path)
        route = .new
    }

    func copy(_ text: String) {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(text, forType: .string)
    }

    func resumeCommand(_ row: SidebarRow) -> String {
        "cd \(CodeActionsShell.quote(row.folder ?? "~")) && alfalfa --reanudar \(row.sessionID ?? "")"
    }

    /// Carpeta de la sesión en `$ALFALFA_HOME/sesiones`.
    func sessionFolder(_ row: SidebarRow) -> URL? {
        guard let id = row.sessionID else { return nil }
        return Attachments.directory().deletingLastPathComponent().appendingPathComponent("sesiones/\(id)")
    }

    @discardableResult
    private func manage(_ arguments: [String]) async -> [AlfalfaMessage]? {
        guard let launcher else { return nil }
        do {
            let messages = try await AlfalfaProcess.query(launcher: launcher, arguments: arguments, directory: home)
            for case let .error(text) in messages { sessionError = text }
            await reload()
            return messages
        } catch {
            sessionError = error.localizedDescription
            return nil
        }
    }

    // MARK: Barra lateral

    var groups: [SidebarGroup] {
        var rows: [String: [SidebarRow]] = [:]
        let liveIDs = Set(live.compactMap(\.sessionID))
        let byID = Dictionary(summaries.map { ($0.id, $0) }, uniquingKeysWith: { first, _ in first })
        for session in live {
            rows[session.cwd.path, default: []].append(row(for: session, stored: session.sessionID.flatMap { byID[$0] }))
        }
        for summary in summaries where !liveIDs.contains(summary.id) {
            let (label, tone) = Presentation.stored(summary.state)
            rows[summary.cwd ?? "Sin carpeta", default: []].append(
                SidebarRow(id: summary.id, title: summary.displayTitle ?? "Sesión \(summary.id)", status: label,
                           tone: tone, route: .past(summary.id), date: summary.date, sessionID: summary.id,
                           folder: summary.cwd, pinned: summary.pinned, archived: summary.archived))
        }
        let query = search.trimmingCharacters(in: .whitespaces).lowercased()
        func keep(_ row: SidebarRow) -> Bool {
            (showArchived || !row.archived) && (query.isEmpty || row.title.lowercased().contains(query))
        }
        let all = rows.values.flatMap { $0 }
        let pinned = all.filter { $0.pinned && keep($0) }.sorted { $0.date > $1.date }
        let folders = rows.map { folder, items in
            SidebarGroup(folder: folder, rows: items.filter { !$0.pinned && keep($0) }.sorted { $0.date > $1.date })
        }
        .filter { !$0.rows.isEmpty }
        .sorted { ($0.rows.first?.date ?? 0) > ($1.rows.first?.date ?? 0) }
        return (pinned.isEmpty ? [] : [SidebarGroup(folder: "", rows: pinned, title: "Fijadas")]) + folders
    }

    private func row(for session: LiveSession, stored: SessionSummary?) -> SidebarRow {
        let status = session.timeline.status
        let tone: SidebarRow.Tone
        switch status {
        case .waitingPermission: tone = .attention
        case .interrupted: tone = .attention
        case .failed: tone = .problem
        case .completed: tone = .done
        case .idle: tone = .done
        case .blocked: tone = .attention
        default: tone = .working
        }
        return SidebarRow(id: session.id.uuidString, title: stored?.title ?? session.title, status: status.label,
                          tone: tone, route: .live(session.id), date: Date().timeIntervalSince1970,
                          sessionID: session.sessionID, folder: session.cwd.path, pinned: stored?.pinned ?? false,
                          archived: stored?.archived ?? false, running: status.isRunning)
    }
}

enum CodeActionsShell {
    static func quote(_ text: String) -> String { "'" + text.replacingOccurrences(of: "'", with: "'\\''") + "'" }
}
