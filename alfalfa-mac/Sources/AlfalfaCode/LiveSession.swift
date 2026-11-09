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
    /// «Este modelo no ve imágenes» al pegar o soltar una con un modelo sin visión.
    let imageNotice = ImageNotice()

    var onChange: (() -> Void)?
    /// El navegador propio de la sesión (herramientas browser_* del agente), con su almacén de datos aislado.
    /// Se crea con la primera petición del agente o al pulsar el globo (`openBrowser`).
    @Published private(set) var browser: BrowserEngine?
    private var process: AlfalfaProcess?
    /// El usuario se fue de la sesión: al quedar libre se cierra stdin del proceso.
    private(set) var releaseWhenParked = false
    private var lastRefresh = Date.distantPast

    var sessionID: String? { timeline.session?.id ?? resumeID }

    /// Sesión guardada abierta sin proceso: el primer mensaje la reanuda con `--reanudar`.
    private var dormantLauncher: URL?
    /// Se llama al despertar una sesión dormida para pasarla a las sesiones vivas.
    var onWake: (() -> Void)?
    /// La sesión que propuso esta (`propose_session`), para la barra lateral y sus notas.
    var parentSessionID: String?
    /// Al terminar cada turno: la app manda la nota a la sesión padre.
    var onTurnFinished: ((Outcome) -> Void)?
    /// «Seguir en una sesión nueva»: el motor creó la sesión con el resumen; la app la abre.
    var onContinued: ((String) -> Void)?
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
        let arguments = Launcher.stdioArguments(
            task: task, resume: resumeID, model: resumeID == nil || settingsChanged ? model : nil,
            effort: resumeID == nil || settingsChanged ? effort : nil, readOnly: readOnly,
            images: images.map(\.path), mode: mode, wait: wait, parent: parentSessionID,
            title: parentSessionID == nil ? nil : title, keepAwake: Preferences().keepAwake, browser: true,
            diagnostics: Preferences().diagnostics)
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
        // El motor no abrió turno con ese mensaje: vuelve al cuadro, con sus imágenes.
        if case let .messageRejected(rejection) = message {
            // El cuadro añade el texto a lo que ya se haya escrito (Composer); las imágenes se suman.
            if !rejection.text.isEmpty, rejection.text != "Mira la imagen adjunta." { pendingDraft = rejection.text }
            draftImages = ImageSupport.restoredImages(current: draftImages, returned: rejection.images)
        }
        if case let .turnFinished(outcome) = message { onTurnFinished?(outcome) }
        if case let .browser(request) = message { answerBrowser(request) }
        if releaseWhenParked, process != nil, timeline.liveProcesses.isEmpty,
           let action = ReleasePolicy.onUpdate(status: timeline.status) {
            shutdown(interrupt: action == .cancelAndClose)
        }
        switch message {
        case .permissionResolved, .turnFinished, .finished:
            Task { await refreshPanel() }
        case let .event(event) where event.role == "tool":
            if Date().timeIntervalSince(lastRefresh) > 2 { Task { await refreshPanel() } }
        case let .continued(newID, _):
            onContinued?(newID)
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

    /// `/contexto` y el aviso de tarifa: Ahorro o Contexto completo. Con proceso vale ya; una
    /// sesión guardada se abre sin turno para cambiarlo.
    func setContextMode(_ mode: ContextMode) {
        if isDormant { openWaiting() }
        process?.send(.contextMode(mode))
        if mode == .full { timeline.answerPriceWarnings() }
        onChange?()
    }

    /// «Seguir en una sesión nueva» del aviso de tarifa: corta el turno, compacta y abre otra
    /// sesión que empieza con el resumen.
    func continueInNewSession() {
        guard !timeline.compacting else { return }
        if isDormant { openWaiting() }
        process?.send(.continueInNewSession)
        timeline.answerPriceWarnings()
        timeline.addNotice("Preparando una sesión nueva con el resumen de esta…")
        onChange?()
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

    /// `/contexto` sin argumento: el modo de la sesión, el tramo caro del modelo y cómo cambiarlo.
    func showContextMode() {
        let mode = timeline.contextMode ?? .saving
        var rows = [InfoRow("Modo", mode.label + " · " + mode.explanation)]
        if let tier = timeline.priceTier {
            rows.append(InfoRow("Tarifa", "a partir de \(CatalogFormat.window(tier.from)) tokens, toda la petición cuesta "
                                + tier.multiplier))
        } else {
            rows.append(InfoRow("Tarifa", "este modelo no cobra más por contexto largo (o aún no se ha medido)"))
        }
        rows.append(InfoRow("Cambiar", "/contexto ahorro · /contexto completo"))
        timeline.addInfo("Contexto de la sesión", rows)
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
        if let tier = timeline.priceTier {
            rows.append(InfoRow("Tarifa", (timeline.contextMode ?? .saving).label + " · cobra " + tier.multiplier
                                + " desde " + CatalogFormat.window(tier.from)))
        }
        rows.append(InfoRow("Tokens gastados", "\(timeline.totalTokens)"))
        if let cost = timeline.totalCost {
            rows.append(InfoRow("Coste estimado", CatalogFormat.cost(cost) + " (precios de models.dev)"))
        }
        if let tokens = timeline.lastTurnTokens {
            rows.append(InfoRow("Último turno", "\(tokens) tokens"
                                + (timeline.lastTurnCost.map { " · " + CatalogFormat.cost($0) } ?? "")))
        }
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
        closeBrowser()
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

// MARK: - Sesiones hijas

extension LiveSession {
    /// La última respuesta del agente, para la nota que recibe la sesión padre.
    var lastAnswer: String? {
        for item in timeline.items.reversed() {
            if case let .agent(_, text) = item { return text }
        }
        return nil
    }

    /// La nota de una sesión hija. Sin tocar el cuadro de texto ni sus imágenes: si la sesión
    /// duerme, la despierta; si trabaja, entra después del turno en marcha.
    func deliver(_ note: String) {
        if isDormant {
            wake(with: note)
            return
        }
        guard process != nil else { return }
        timeline.addUser(note, queued: true)
        process?.send(.message(note))
    }
}

/// Ajustes ▸ General ▸ Mantener el Mac despierto.
extension LiveSession {

    // MARK: - Métodos públicos

    /// El interruptor de Ajustes: el proceso abierto lo aplica ya, también a mitad de turno.
    func setKeepAwake(_ on: Bool) {
        process?.send(.keepAwake(on))
    }

    /// Ajustes ▸ General ▸ Ver los errores al editar: vale desde la siguiente edición.
    func setDiagnostics(_ on: Bool) {
        process?.send(.diagnostics(on))
    }
}

// MARK: - Navegador propio

extension LiveSession {
    /// El navegador de la sesión, creándolo la primera vez.
    @discardableResult
    func openBrowser() -> BrowserEngine {
        if let browser { return browser }
        let engine = BrowserEngine()
        browser = engine
        return engine
    }

    /// Lo suelta todo: el WKWebView, su ventana fuera de la pantalla y su almacén de datos.
    func closeBrowser() {
        browser?.close()
        browser = nil
    }

    /// El agente usa el navegador: el panel se abre y la respuesta vuelve al motor. El motor las
    /// manda de una en una; BrowserEngine.perform las ejecuta en orden aunque una llegue tarde.
    private func answerBrowser(_ request: BrowserRequest) {
        let engine = openBrowser()
        Task { [weak self] in
            let reply = await engine.perform(request)
            self?.process?.send(.browserReply(reply))
        }
    }
}
