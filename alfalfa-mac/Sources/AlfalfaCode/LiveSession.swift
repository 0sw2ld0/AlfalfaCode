import AlfalfaKit
import Foundation

/// Una sesión con proceso hijo `alfalfa --stdio` propio.
@MainActor
final class LiveSession: ObservableObject, Identifiable {
    nonisolated let id = UUID()
    let cwd: URL
    let title: String
    /// Sesión que se reanuda; tras terminar su proceso pasa a ser la propia, para seguirla.
    private(set) var resumeID: String?
    @Published private(set) var model: String
    @Published private(set) var effort: String
    /// El usuario cambió modelo o esfuerzo antes de arrancar el proceso: se pasan al reanudar.
    private var settingsChanged = false
    let readOnly: Bool

    @Published private(set) var timeline = Timeline()
    @Published private(set) var snapshot: GitSnapshot?
    @Published private(set) var ollama: Bool?
    @Published private(set) var answered: Set<String> = []
    @Published private(set) var stopping = false
    /// Imágenes elegidas para la próxima indicación.
    @Published var draftImages: [URL] = []
    /// Texto que la vista pone en el cuadro al abrir la sesión (volver a un mensaje).
    @Published var pendingDraft: String?

    var onChange: (() -> Void)?
    private var process: AlfalfaProcess?
    /// El usuario se fue de la sesión: al quedar libre se cierra stdin del proceso.
    private(set) var releaseWhenParked = false
    private var lastRefresh = Date.distantPast

    var sessionID: String? { timeline.session?.id ?? resumeID }

    /// Sesión guardada abierta sin proceso: el primer mensaje la reanuda con `--reanudar`.
    private var dormantLauncher: URL?
    /// Se llama al despertar una sesión dormida para pasarla a las sesiones vivas.
    var onWake: (() -> Void)?
    var isDormant: Bool { dormantLauncher != nil }
    /// Se puede escribir: hay proceso abierto o la sesión duerme hasta el siguiente mensaje.
    var canSend: Bool { timeline.status.isActive || isDormant }
    /// La orden alfalfa con la que arrancó, para reanudarla después.
    private var launcher: URL?
    /// Interrumpida o bloqueada: se puede reanudar sin mensaje. Una completada necesita uno.
    @Published private(set) var resumableWithoutMessage = false

    /// Pinta la conversación guardada y espera el mensaje que la continúe.
    func restore(_ history: Timeline, launcher: URL, resumable: Bool) {
        timeline = history
        dormantLauncher = launcher
        resumableWithoutMessage = resumable
        Task { await refreshPanel() }
    }

    /// Reanuda sin mensaje (sólo sesiones que no terminaron bien: el arnés lo exige).
    func wake(with task: String?, images: [URL] = []) {
        guard let launcher = dormantLauncher else { return }
        dormantLauncher = nil
        timeline.restart()
        onWake?()
        start(launcher: launcher, task: task, images: images)
    }

    /// Abre la sesión guardada sin turno (`--esperar`): para compactarla sin escribir nada.
    private func openWaiting() {
        guard let launcher = dormantLauncher else { return }
        dormantLauncher = nil
        timeline.restart(waiting: true)
        onWake?()
        start(launcher: launcher, task: nil, wait: true)
    }

    /// Modo de permisos de la sesión (`auto`, `manual`, `ediciones`, `plan`, `omitir`).
    @Published private(set) var mode: String

    init(cwd: URL, title: String, resumeID: String?, model: String, effort: String, readOnly: Bool,
         mode: String = PermissionMode.auto.rawValue) {
        self.cwd = cwd
        self.title = title
        self.resumeID = resumeID
        self.model = model
        self.effort = effort
        self.readOnly = readOnly
        self.mode = mode
    }

    func start(launcher: URL, task: String?, images: [URL] = [], wait: Bool = false) {
        self.launcher = launcher
        let arguments = Launcher.stdioArguments(task: task, resume: resumeID,
                                                model: resumeID == nil || settingsChanged ? model : nil,
                                                effort: resumeID == nil || settingsChanged ? effort : nil, readOnly: readOnly,
                                                images: images.map(\.path), mode: mode, wait: wait)
        let child = AlfalfaProcess(launcher: launcher, arguments: arguments, directory: cwd)
        child.onMessage = { [weak self] message in
            DispatchQueue.main.async { MainActor.assumeIsolated { self?.handle(message) } }
        }
        child.onExit = { [weak self] code, tail in
            DispatchQueue.main.async { MainActor.assumeIsolated { self?.exited(code: code, stderr: tail) } }
        }
        if let task { timeline.addUser(ImageTags.join(task, images: images.map(\.path)), queued: false) }
        do {
            try child.start()
            process = child
        } catch {
            timeline.addNotice("No se pudo iniciar alfalfa: \(error.localizedDescription)")
            timeline.processExited(code: 1)
        }
        Task { await refreshPanel() }
    }

    private func handle(_ message: AlfalfaMessage) {
        timeline.apply(message)
        onChange?()
        if releaseWhenParked, process != nil, timeline.liveProcesses.isEmpty,
           let action = ReleasePolicy.onUpdate(status: timeline.status) {
            shutdown(interrupt: action == .cancelAndClose)
        }
        switch message {
        case .permissionResolved, .turnFinished, .finished:
            Task { await refreshPanel() }
        case let .event(event) where event.role == "tool":
            if Date().timeIntervalSince(lastRefresh) > 2 { Task { await refreshPanel() } }
        default:
            break
        }
    }

    private func exited(code: Int32, stderr: String) {
        timeline.processExited(code: code)
        let detail = stderr.split(separator: "\n").suffix(3).joined(separator: "\n")
        if !detail.isEmpty && !timeline.finished { timeline.addNotice(detail) }
        process = nil
        // Como una sesión guardada: terminada o cerrada al salir, el siguiente mensaje
        // la reanuda (`--reanudar ID "mensaje"`) en vez de dejar el cuadro bloqueado.
        if let id = sessionID, let launcher {
            resumeID = id
            dormantLauncher = launcher
            resumableWithoutMessage = timeline.status != .completed
        }
        onChange?()
        Task { await refreshPanel() }
    }

    func refreshPanel() async {
        lastRefresh = Date()
        async let git = Workspace.snapshot(of: cwd)
        async let reachable = Workspace.ollamaReachable()
        snapshot = await git
        ollama = await reachable
    }

    /// Sólo se llama desde un botón: la app nunca aprueba por su cuenta.
    func answer(_ id: String, approved: Bool, session: Bool = false) {
        guard !answered.contains(id) else { return }
        answered.insert(id)
        process?.send(.permission(id: id, approved: approved, session: session))
    }

    func send(_ text: String) {
        let clean = text.trimmingCharacters(in: .whitespacesAndNewlines)
        let images = draftImages.map(\.path)
        if isDormant, !clean.isEmpty || !images.isEmpty {
            let urls = draftImages
            draftImages = []
            wake(with: clean.isEmpty ? "Mira la imagen adjunta." : clean, images: urls)
            return
        }
        guard !clean.isEmpty || !images.isEmpty, process != nil else { return }
        timeline.addUser(ImageTags.join(clean, images: images), queued: true)
        process?.send(.message(clean, images: images))
        draftImages = []
    }

    /// «Enviar ahora» de un mensaje en cola, como en Claude Code: el turno en marcha se corta y
    /// este mensaje abre el siguiente. La sesión sigue abierta.
    func sendNow(_ text: String) {
        guard process != nil, timeline.status.isRunning else { return }
        process?.send(.sendNow(text))
    }

    /// «Redirigir», como steer de Codex: el modelo deja lo que estaba pensando y lee ya los
    /// mensajes en cola, sin cortar el turno. Una orden que ya corre termina antes.
    func redirect() {
        guard process != nil, timeline.status.isRunning else { return }
        process?.send(.redirect)
    }

    /// Botón de la caja de cierre: su texto va como respuesta del usuario, sin los adjuntos del borrador.
    func reply(_ text: String) {
        if isDormant { wake(with: text); return }
        guard process != nil, timeline.status.isActive, !timeline.status.isRunning else { return }
        timeline.addUser(text, queued: true)
        process?.send(.message(text))
    }

    func dismissWrapUp(_ id: Int) { timeline.dismissWrapUp(id: id) }

    /// Como /model en Codex: vale para el siguiente turno; el que está en marcha sigue igual.
    func change(model newModel: String? = nil, effort newEffort: String? = nil, mode newMode: String? = nil) {
        let model = newModel.flatMap { $0 == self.model ? nil : $0 }
        let effort = newEffort.flatMap { $0 == self.effort ? nil : $0 }
        let mode = newMode.flatMap { $0 == self.mode ? nil : $0 }
        guard model != nil || effort != nil || mode != nil else { return }
        if let model { self.model = model }
        if let effort { self.effort = effort }
        if let mode { self.mode = mode }
        if let process { process.send(.settings(model: model, effort: effort, mode: mode)) } else { settingsChanged = true }
        onChange?()
    }

    /// «Compactar sesión» y `/compactar [instrucciones]`, como /compact de Claude Code: entre
    /// turnos se compacta ya; con un turno en marcha, antes de la siguiente respuesta. Una sesión
    /// guardada se abre sin turno para compactarla.
    func compact(instructions: String? = nil) {
        guard !timeline.compacting else { return }
        if isDormant { openWaiting() }
        process?.send(.compact(instructions: instructions))
    }

    /// Se puede compactar: hay conversación y no se está compactando ya.
    var canCompact: Bool {
        !timeline.compacting && sessionID != nil && (isDormant || timeline.status.isActive)
            && timeline.status != .starting
    }

    /// Detener una orden en segundo plano, o todas con «todos» (`/cerrar-procesos`).
    func stopProcess(_ id: String) { process?.send(.stopProcess(id)) }

    /// `/procesos`, como /ps de Codex.
    func showProcesses() {
        let rows = timeline.processes.map { item in
            InfoRow(item.id, item.command + " · " + (item.alive ? "abierto"
                : item.stopped ? "detenido" : "terminó con código \(item.exitCode.map(String.init) ?? "?")"))
        }
        timeline.addInfo("Procesos en segundo plano", rows.isEmpty ? [InfoRow("Ninguno", "no hay órdenes en segundo plano")] : rows)
        onChange?()
    }

    /// `/estado`, como /status de Codex.
    func showStatus() {
        var rows = [InfoRow("Sesión", sessionID ?? "sin id todavía"), InfoRow("Carpeta", cwd.path),
                    InfoRow("Modelo", model), InfoRow("Esfuerzo", Presentation.effortLabel(effort))]
        if timeline.contextLimit > 0 {
            let percent = Int((timeline.contextFraction * 100).rounded())
            rows.append(InfoRow("Contexto", "\(percent) % · \(timeline.contextTokens) de \(timeline.contextLimit) tokens"))
        } else {
            rows.append(InfoRow("Contexto", "aún sin medir"))
        }
        rows.append(InfoRow("Tokens gastados", "\(timeline.totalTokens)"))
        rows.append(InfoRow("Estado", isDormant ? "guardada; escribe para continuarla" : timeline.status.label))
        if readOnly { rows.append(InfoRow("Permisos", "solo lectura")) }
        timeline.addInfo("Estado de la sesión", rows)
        onChange?()
    }

    func addNotice(_ text: String) {
        timeline.addNotice(text)
        onChange?()
    }

    func stop() {
        guard let process, !stopping else { return }
        stopping = true
        process.send(.cancel)
        DispatchQueue.main.asyncAfter(deadline: .now() + 8) { [weak self] in
            MainActor.assumeIsolated { self?.process?.terminate() }
        }
    }

    /// Cierra stdin del proceso hijo para que no quede huérfano. Con `interrupt` cancela antes el turno.
    /// Cierra el proceso si está parado entre turnos y espera a que suelte la sesión (su cerrojo).
    /// Bifurcar o volver a un mensaje lo necesitan; con un turno en marcha no se toca.
    func releaseProcess() async -> Bool {
        guard process != nil else { return true }
        guard !timeline.status.isRunning else { return false }
        shutdown(interrupt: false)
        for _ in 0..<100 where process != nil {
            try? await Task.sleep(nanoseconds: 100_000_000)
        }
        return process == nil
    }

    func shutdown(interrupt: Bool = true) {
        if interrupt { process?.send(.cancel) }
        process?.closeInputAndWait()
    }

    /// Se llama al salir de la sesión: cierra stdin ya si está libre; con permiso pendiente cancela y cierra;
    /// si trabaja, lo cierra cuando termine el turno.
    func leave() {
        guard process != nil else { return }
        // Un juego o un servidor en segundo plano se cerraría con el proceso: se queda abierto.
        guard timeline.liveProcesses.isEmpty else { return }
        switch ReleasePolicy.onLeave(status: timeline.status) {
        case .closeNow: shutdown(interrupt: false)
        case .cancelAndClose: shutdown()
        case .closeWhenParked: releaseWhenParked = true
        }
    }

    /// El usuario volvió a la sesión antes de que terminara el turno.
    func returned() { releaseWhenParked = false }

    /// Hay un proceso vivo esperando órdenes, sin turno ni permiso en curso.
    var isParked: Bool {
        process != nil && ReleasePolicy.isParked(timeline.status)
    }
}
