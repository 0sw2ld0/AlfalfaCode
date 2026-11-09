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
    var sessionID: String?
    var folder: String?
    var pinned = false
    var archived = false
    /// Una sesión con turno en marcha no se puede eliminar ni bifurcar.
    var running = false

    enum Tone { case working, attention, done, problem, idle }
}

struct SidebarGroup: Identifiable {
    let folder: String
    let rows: [SidebarRow]
    var title: String?
    var id: String { title ?? folder }
    var name: String { title ?? URL(fileURLWithPath: folder).lastPathComponent }
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
    /// Un modelo elegido cuyo proveedor no tiene clave: la hoja explica cuál falta y la guarda.
    @Published var keyRequest: KeyRequest?
    /// Ollama, OpenAI y Alfalfa, como los cuenta `alfalfa --proveedores --json`.
    @Published private(set) var providers: [ProviderStatus] = []
    @Published private(set) var defaultProvider = "ollama"
    @Published var providersError: String?
    /// El buscador web de los agentes (`alfalfa --buscador --json`); nil hasta leerlo.
    @Published var webSearch: WebSearchSettings?
    @Published var webSearchError: String?
    @Published var installingWebSearch = false
    /// La instalación automática de la librería de búsqueda se intenta una vez por arranque.
    var triedWebSearchInstall = false
    /// Cuota del mes del servicio Alfalfa (GPT-6 Luna y los Flash), si hay código activado.
    @Published private(set) var alfalfaQuotas: [Quota] = []
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
    /// «Este modelo no ve imágenes» en el cuadro del encargo nuevo.
    let newImageNotice = ImageNotice()
    private var pasteMonitor: Any?

    let launcher: URL?
    private let home = FileManager.default.homeDirectoryForCurrentUser
    private static let recentsKey = "recentFolders"

    init() {
        launcher = Launcher.locate()
        recents = UserDefaults.standard.stringArray(forKey: Self.recentsKey) ?? []
        applyPreferences()
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
        case .new:
            guard allowsImages(model) else { newImageNotice.show(); return }
            ImagePicker.add([url], to: &draftImages)
        case let .live(id):
            guard let session = session(id) else { return }
            guard allowsImages(session.model) else { session.imageNotice.show(); return }
            ImagePicker.add([url], to: &session.draftImages)
        case .past: break
        }
    }

    // MARK: - Carga

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
            adoptListedNames()
        } catch {
            models = []
            if loadError == nil { loadError = "No se pudo consultar Ollama para listar modelos." }
        }
        await reloadProviders()
        // Sin esperar: si falta la librería de búsqueda web, se instala mientras la app sigue.
        Task { await ensureWebSearch() }
        await loadAlfalfaQuota()
        if folder == nil { folder = folderChoices.first }
        await refreshNewPanel()
    }

    /// El nombre visible de un modelo («GPT-6 Luna - Alfalfa»), o el corto si no está en la lista.
    func label(for name: String) -> String {
        let listed = ModelProvider.listed(name, in: models)
        return models.first { $0.name == listed }?.label ?? Presentation.shortModel(name)
    }

    /// Los modelos del selector agrupados por proveedor, en el orden de la lista. Los gratis
    /// van aparte, en «Gratis ahora» (`freeGroups`), salvo el elegido.
    var modelGroups: [(provider: String, names: [String])] {
        let names = modelChoices.filter { name in !(info(for: name)?.free ?? false) || name == model }
        var order: [String] = []
        var groups: [String: [String]] = [:]
        for name in names {
            let provider = ModelProvider.id(of: name, in: models)
            if groups[provider] == nil { order.append(provider) }
            groups[provider, default: []].append(name)
        }
        return order.map { ($0, groups[$0] ?? []) }
    }

    func providerName(_ id: String) -> String {
        ModelProvider.name(of: id, listed: providers)
    }

    // MARK: - Proveedores

    func reloadProviders() async {
        _ = await providerCommand(["--proveedores", "--json"])
    }

    /// Guarda una clave en el Llavero a través de `alfalfa`: va por stdin, nunca en los argumentos.
    func saveKey(_ secret: String, for provider: String) async -> Bool {
        let clean = secret.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !clean.isEmpty else { return false }
        let ok = await providerCommand(["--guardar-clave", provider, "--json"], input: clean)
        if ok { await reload() }
        return ok
    }

    func deleteKey(for provider: String) async {
        if await providerCommand(["--borrar-clave", provider, "--json"]) { await reload() }
    }

    func setURL(_ url: String, for provider: String) async -> Bool {
        let value = url.trimmingCharacters(in: .whitespacesAndNewlines)
        let ok = await providerCommand(["--fijar-url", provider, value.isEmpty ? "-" : value, "--json"])
        if ok { await reload() }
        return ok
    }

    /// Ata el código de cortesía a este Mac (clave en el Secure Enclave) y lo guarda en el Llavero.
    func activate(code: String, url: String) async -> Bool {
        let clean = code.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !clean.isEmpty, let launcher else { return false }
        if !url.isEmpty, url != providers.first(where: { $0.id == "alfalfa" })?.url {
            guard await setURL(url, for: "alfalfa") else { return false }
        }
        do {
            let messages = try await AlfalfaProcess.query(launcher: launcher, arguments: ["--activar-codigo", "--json"],
                                                          directory: home, input: clean)
            for message in messages {
                if case let .error(text) = message { providersError = text; return false }
                if case let .activated(_, quotas) = message {
                    alfalfaQuotas = quotas
                    providersError = nil
                    await reload()
                    return true
                }
            }
            providersError = "El servicio Alfalfa no confirmó la activación."
        } catch {
            providersError = error.localizedDescription
        }
        return false
    }

    func loadAlfalfaQuota() async {
        guard let launcher, providers.first(where: { $0.id == "alfalfa" })?.configured == true else {
            alfalfaQuotas = []
            return
        }
        if let messages = try? await AlfalfaProcess.query(launcher: launcher, arguments: ["--cuota", "--json"],
                                                          directory: home) {
            for case let .quota(rows) in messages { alfalfaQuotas = rows }
        }
    }

    func setDefaultProvider(_ provider: String) async {
        _ = await providerCommand(["--proveedor-por-defecto", provider, "--json"])
    }

    @discardableResult
    private func providerCommand(_ arguments: [String], input: String? = nil) async -> Bool {
        guard let launcher else { return false }
        do {
            let messages = try await AlfalfaProcess.query(launcher: launcher, arguments: arguments,
                                                          directory: home, input: input)
            for message in messages {
                switch message {
                case let .providers(defaultProvider, rows):
                    self.defaultProvider = defaultProvider
                    providers = rows
                    providersError = nil
                    return true
                case let .error(text):
                    providersError = text
                    return false
                default: continue
                }
            }
            return false
        } catch {
            providersError = error.localizedDescription
            return false
        }
    }

    var modelChoices: [String] {
        // Los modelos de embeddings no conversan: no se ofrecen para un chat.
        var names = models.map(\.name).filter { !$0.lowercased().contains("embed") }
        if !names.contains(Presentation.defaultModel) { names.insert(Presentation.defaultModel, at: 0) }
        if !names.contains(ModelProvider.listed(model, in: models)) { names.insert(model, at: 0) }
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

    // MARK: - Actualización

    /// Compara la versión instalada con la última etiqueta del repositorio de AlfalfaCode.
    func checkForUpdate() {
        // AlfalfaCode.app se actualiza descargando el DMG firmado de la última release.
        if AppUpdater.isPackaged { AppUpdater.shared.checkManually(); return }
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

    // MARK: - Carpeta

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

    // MARK: - Sesiones

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
        watchFamily(session)
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
        watchFamily(session)
        session.onWake = { [weak self, weak session] in
            guard let self, let session else { return }
            self.dormant[summary.id] = nil
            // Una sesión que ya despertó antes vuelve a despertar al cerrarse su proceso: no se duplica.
            if !self.live.contains(where: { $0 === session }) { self.live.insert(session, at: 0) }
            // Sólo si la estás mirando: la nota de una sesión hija la despierta sin moverte.
            if self.route == .past(summary.id) { self.route = .live(session.id) }
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
            for case let .history(_, events, wrapUp, context, tokens, cost) in messages {
                session.restore(Timeline(history: events, wrapUp: wrapUp, context: context, tokens: tokens, cost: cost),
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

    // MARK: - Acciones sobre una sesión (clic derecho)

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

    // MARK: - Chats de la app de Ollama

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

    // MARK: - Barra lateral

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

// MARK: - Sesiones propuestas

extension AppModel {
    /// «Nueva sesión» de una tarjeta de `propose_session`, como en Claude Code: empieza el encargo en
    /// la misma carpeta, con el modelo, el esfuerzo y el modo de la sesión que la propuso. No cambia
    /// de pantalla: la sesión nueva sale en la barra lateral y, si pide permiso, espera a que vayas.
    /// Va aquí porque `live` sólo se cambia desde este archivo.
    @discardableResult
    func startProposed(_ proposal: SessionProposal, from parent: LiveSession) -> Bool {
        guard let launcher else { return false }
        let session = LiveSession(cwd: parent.cwd, title: proposal.title, resumeID: nil, model: parent.model,
                                  effort: parent.effort, readOnly: parent.readOnly, mode: parent.mode)
        session.parentSessionID = parent.sessionID
        session.onChange = { [weak self] in self?.objectWillChange.send() }
        watchFamily(session)
        live.insert(session, at: 0)
        session.start(launcher: launcher, task: proposal.task)
        Task {
            try? await Task.sleep(nanoseconds: 1_500_000_000)
            await reload()
        }
        return true
    }
}
