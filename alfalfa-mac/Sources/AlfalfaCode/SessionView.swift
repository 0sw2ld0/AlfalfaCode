import AppKit
import AlfalfaKit
import SwiftUI
import UniformTypeIdentifiers

/// Pantalla de sesión: conversación, composición y panel derecho.
struct SessionView: View {
    @EnvironmentObject private var app: AppModel
    @ObservedObject var session: LiveSession
    var notice: String? = nil
    @State private var text = ""
    /// Fila elegida del menú de órdenes con «/».
    @State private var selection = 0

    var body: some View {
        HStack(spacing: 0) {
            VStack(spacing: 0) {
                header
                conversation
                composer
            }
            .frame(maxWidth: .infinity)
            Divider().overlay(Theme.border)
            SidePanel(folder: session.cwd.path, snapshot: session.snapshot, ollama: session.ollama, timeline: session.timeline,
                      canCompact: session.canCompact, onCompact: { session.compact() },
                      onStopProcess: { session.stopProcess($0) })
                .frame(width: 300)
        }
    }

    private var header: some View {
        HStack(spacing: 10) {
            LeafIcon()
            Text(session.title).font(.system(size: 15, weight: .semibold)).lineLimit(1)
            Spacer()
            if let notice { Text(notice).font(.system(size: 12)).foregroundStyle(Theme.warning).lineLimit(1) }
            if session.isDormant {
                if session.resumableWithoutMessage {
                    Button { session.wake(with: nil) } label: {
                        Label("Reanudar", systemImage: "arrow.clockwise").font(.system(size: 12.5, weight: .semibold))
                            .padding(.horizontal, 10).frame(minHeight: 26)
                            .background(Theme.light, in: RoundedRectangle(cornerRadius: 7))
                            .foregroundStyle(Theme.dark)
                    }
                    .buttonStyle(.plain).help("Continuar el encargo donde se quedó")
                } else {
                    Text("Escribe para continuar la sesión").font(.system(size: 12)).foregroundStyle(Theme.muted)
                }
            }
        }
        .padding(.horizontal, 24).frame(minHeight: 48).padding(.top, 4)
        .overlay(alignment: .bottom) { Divider().overlay(Theme.border) }
    }

    private var conversation: some View {
        let firstPending = session.timeline.pendingPermissions.first?.id
        return ScrollViewReader { proxy in
            ScrollView {
                let items = session.timeline.items
                // Sólo el último grupo de acciones es el que está trabajando ahora.
                let liveWork = session.timeline.status.isRunning
                    ? items.lastIndex(where: { if case .events = $0 { return true } else { return false } }) : nil
                LazyVStack(alignment: .leading, spacing: 22) {
                    ForEach(Array(items.enumerated()), id: \.element.id) { index, item in
                        ItemView(item: item, session: session,
                                 isFirstPending: firstPending != nil && isPermission(item, firstPending!),
                                 closesRun: closesRun(items, index), liveWork: index == liveWork)
                            .id(item.id)
                    }
                    if let plan = session.timeline.visiblePlan {
                        PlanCard(plan: plan, fromBefore: session.timeline.planIsFromBeforeLastMessage).id("plan")
                    }
                    if session.timeline.compacting { CompactingLine().id("compactando") }
                }
                .frame(maxWidth: Theme.column, alignment: .leading)
                .padding(.horizontal, 40).padding(.top, 28).padding(.bottom, 32)
                .frame(maxWidth: .infinity)
            }
            .onChange(of: session.timeline.items.count) {
                if let last = session.timeline.items.last { withAnimation { proxy.scrollTo(last.id, anchor: .bottom) } }
            }
            .onChange(of: session.timeline.compacting) { _, compacting in
                if compacting { withAnimation { proxy.scrollTo("compactando", anchor: .bottom) } }
            }
        }
    }

    /// Una respuesta del agente que no va seguida de más trabajo: lleva copiar y la hora.
    private func closesRun(_ items: [TimelineItem], _ index: Int) -> Bool {
        guard case .agent = items[index] else { return false }
        guard index + 1 < items.count else { return !session.timeline.status.isRunning }
        switch items[index + 1] {
        case .events, .agent, .permission: return false
        default: return true
        }
    }

    /// Modelo y esfuerzo de la sesión: se pueden cambiar en cualquier momento y valen desde el
    /// siguiente turno, como /model en Codex.
    private var modelMenu: some View {
        Menu {
            ModelMenuItems(groups: groups, selected: session.model, label: app.label(for:),
                           providerName: app.providerName) { session.change(model: $0) }
        } label: {
            Text(app.label(for: session.model)).font(.system(size: 12.5, design: .monospaced))
        }
        .menuStyle(.borderlessButton).fixedSize().accessibilityLabel("Modelo")
        .help(session.timeline.status.isRunning ? "El cambio vale desde el siguiente turno" : "Modelo de la sesión")
    }

    private var effortMenu: some View {
        Menu {
            ForEach(Presentation.efforts, id: \.value) { effort in
                Button { session.change(effort: effort.value) } label: {
                    if effort.value == session.effort { Label(effort.label, systemImage: "checkmark") } else { Text(effort.label) }
                }
            }
        } label: {
            Text(Presentation.effortLabel(session.effort)).font(.system(size: 12.5))
        }
        .menuStyle(.borderlessButton).fixedSize().accessibilityLabel("Esfuerzo")
        .help(session.timeline.status.isRunning ? "El cambio vale desde el siguiente turno" : "Esfuerzo de la sesión")
    }

    /// Los modelos de Ollama y, si no está en la lista, el de la sesión.
    private var choices: [String] {
        let names = app.modelChoices
        return names.contains(session.model) ? names : [session.model] + names
    }

    private var groups: [(provider: String, names: [String])] {
        let all = app.modelGroups
        if all.contains(where: { $0.names.contains(session.model) }) { return all }
        return [(Presentation.provider(of: session.model), [session.model])] + all
    }

    private func isPermission(_ item: TimelineItem, _ id: String) -> Bool {
        if case let .permission(_, request, _) = item { return request.id == id }
        return false
    }

    private var composer: some View {
        VStack(spacing: 10) {
            let options = suggestions
            if !options.isEmpty {
                CommandPopup(suggestions: options, selection: min(selection, options.count - 1)) { choose($0) }
            }
            AttachmentStrip(images: $session.draftImages).frame(maxWidth: .infinity, alignment: .leading)
            HStack(spacing: 10) {
                Button { ImagePicker.choose(into: &session.draftImages) } label: {
                    Image(systemName: "plus").foregroundStyle(Theme.secondary).frame(width: 22, height: 22)
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain).help("Adjuntar imágenes (también puedes pegarlas o soltarlas aquí)")
                .accessibilityLabel("Adjuntar imágenes").disabled(!session.canSend)
                TextField("Añade una indicación", text: $text, axis: .vertical)
                    .textFieldStyle(.plain).lineLimit(1...8).font(Theme.body)
                    .accessibilityLabel("Indicación para el agente")
                    // Una imagen soltada sobre el texto llega como su ruta: se adjunta en su lugar.
                    .onChange(of: text) { old, new in
                        guard let dropped = DroppedImages.extract(old: old, new: new) else { return }
                        text = dropped.text
                        ImagePicker.add(dropped.images, to: &session.draftImages)
                    }
                    .onSubmit(submit)
                    .onChange(of: text) { selection = 0 }
                    // Para revisar la interfaz sin teclear: ALFALFA_BORRADOR rellena el cuadro al abrir.
                    .onAppear {
                        if let draft = session.pendingDraft {
                            text = draft
                            session.pendingDraft = nil
                        } else if text.isEmpty, let draft = ProcessInfo.processInfo.environment["ALFALFA_BORRADOR"] {
                            text = draft
                        }
                    }
                    .onChange(of: session.pendingDraft) { _, draft in
                        if let draft { text = draft; session.pendingDraft = nil }
                    }
                    .onKeyPress(.upArrow) { move(-1) }
                    .onKeyPress(.downArrow) { move(1) }
                    .onKeyPress(.tab) {
                        guard let option = suggestions[safe: selection] else { return .ignored }
                        complete(option)
                        return .handled
                    }
                    .onKeyPress(.escape) {
                        guard !suggestions.isEmpty else { return .ignored }
                        text = ""
                        return .handled
                    }
                    .disabled(!session.canSend)
                if session.timeline.status.isRunning {
                    Button { session.stop() } label: {
                        RoundedRectangle(cornerRadius: 2).fill(Theme.dark).frame(width: 10, height: 10)
                            .frame(width: 30, height: 30)
                            .background(Theme.light, in: Circle())
                    }
                    .buttonStyle(.plain).accessibilityLabel("Detener").help("Detener")
                    .disabled(session.stopping)
                } else {
                    // Turno terminado: el proceso sigue abierto y se puede mandar otra indicación.
                    let ready = session.canSend
                        && (!text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || !session.draftImages.isEmpty)
                    Button(action: submit) {
                        Image(systemName: "arrow.up").fontWeight(.bold).foregroundStyle(Theme.dark)
                            .frame(width: 30, height: 30)
                            .background(ready ? Theme.light : Theme.muted.opacity(0.5), in: Circle())
                    }
                    .buttonStyle(.plain).disabled(!ready)
                    .accessibilityLabel("Enviar").help("Enviar")
                }
            }
            .padding(.horizontal, 16).padding(.vertical, 12)
            .background(Theme.field, in: RoundedRectangle(cornerRadius: 18))
            .overlay(RoundedRectangle(cornerRadius: 18).stroke(Theme.border))
            .onDrop(of: [.fileURL], isTargeted: nil) { ImagePicker.drop($0, into: $session.draftImages) }

            HStack(spacing: 16) {
                modelMenu
                effortMenu
                ModeMenu(mode: session.mode,
                         help: session.timeline.status.isRunning ? "El cambio vale desde el siguiente turno" : "Modo de permisos") {
                    session.change(mode: $0)
                }
                if session.readOnly { Label("Solo lectura", systemImage: "lock.fill").foregroundStyle(Theme.alfalfaLight) }
                Spacer()
                subagentsHint
                processesHint
                compactionHint
                statusLabel
            }
            .font(.system(size: 12.5)).foregroundStyle(Theme.secondary).padding(.horizontal, 6)
        }
        .frame(maxWidth: Theme.column)
        .padding(.horizontal, 40).padding(.bottom, 24)
        .frame(maxWidth: .infinity)
    }

    /// «2 subagentes trabajando»; el panel de la derecha enseña sus pasos.
    @ViewBuilder private var subagentsHint: some View {
        let count = session.timeline.workingSubagents.count
        if count > 0 {
            Label(count == 1 ? "1 subagente trabajando" : "\(count) subagentes trabajando", systemImage: "person.2")
                .foregroundStyle(Theme.secondary)
                .help(session.timeline.workingSubagents.map { $0.id + " · " + $0.name }.joined(separator: "\n"))
        }
    }

    /// «1 proceso en segundo plano», como el aviso de Codex; un clic los lista.
    @ViewBuilder private var processesHint: some View {
        let count = session.timeline.liveProcesses.count
        if count > 0 {
            Button { session.showProcesses() } label: {
                Label(count == 1 ? "1 proceso en segundo plano" : "\(count) procesos en segundo plano", systemImage: "terminal")
                    .foregroundStyle(Theme.secondary)
            }
            .buttonStyle(.plain)
            .help("Siguen abiertos aunque acabe el turno. /procesos los lista y /cerrar-procesos los cierra; también desde el panel.")
        }
    }

    /// Desde el 80 % del umbral, cuánto falta para la compactación automática; un clic compacta.
    @ViewBuilder private var compactionHint: some View {
        let timeline = session.timeline
        if let progress = timeline.compactionProgress, progress >= 0.8, let left = timeline.tokensUntilCompaction {
            Button { session.compact() } label: {
                Label(left == 0 ? "Se compactará en la siguiente respuesta"
                      : "\(Int(((1 - progress) * 100).rounded())) % hasta compactar",
                      systemImage: "arrow.down.right.and.arrow.up.left")
                    .foregroundStyle(progress >= 0.95 ? Theme.warning : Theme.secondary)
            }
            .buttonStyle(.plain).disabled(!session.canCompact)
            .help("Quedan \(ContextFormat.tokens(left)) tokens hasta la compactación automática. Pulsa para compactar ya.")
        }
    }

    @ViewBuilder private var statusLabel: some View {
        let status = session.timeline.status
        switch status {
        case .interrupted:
            Text("● Interrumpida · se puede reanudar").foregroundStyle(Theme.warning)
        case .failed:
            Text("● fallida").foregroundStyle(Theme.danger)
        case .completed:
            Text("● completada").foregroundStyle(Theme.alfalfa)
        case .waitingPermission:
            Text("● esperando permiso").foregroundStyle(Theme.warning)
        case .blocked:
            Text("● bloqueada").foregroundStyle(Theme.warning)
        case .idle:
            Text("● listo").foregroundStyle(Theme.alfalfa)
        default:
            Text("● \(status.label)").foregroundStyle(Theme.alfalfa)
        }
    }

    private func submit() {
        // ⌘↩ es el atajo de Aprobar: no debe enviar el mensaje.
        if NSApp.currentEvent?.modifierFlags.contains(.command) == true { return }
        if let input = SlashCommand.input(text) {
            if let option = suggestions[safe: selection] { choose(option); return }
            if let command = SlashCommand.named(input.name) { run(command, input.argument); return }
            // «/Users/…» u otra cosa que no es una orden: va como mensaje normal.
        }
        session.send(text)
        text = ""
    }

    // MARK: Órdenes con «/»

    /// Lo que enseña el menú: las órdenes que encajan o, tras `/modelo ` y `/esfuerzo `, sus valores.
    private var suggestions: [CommandSuggestion] {
        guard let input = SlashCommand.input(text) else { return [] }
        let running = session.timeline.status.isRunning
        if let argument = input.argument {
            guard let command = SlashCommand.named(input.name) else { return [] }
            let wanted = argument.trimmingCharacters(in: .whitespaces).lowercased()
            switch command {
            case .modelo:
                return choices.filter { wanted.isEmpty || $0.lowercased().contains(wanted) }.map { name in
                    CommandSuggestion(id: "modelo:" + name, symbol: name == session.model ? "checkmark" : "cpu",
                                      title: app.label(for: name), detail: name == session.model ? "en uso" : "",
                                      enabled: true, action: .argument(.modelo, name))
                }
            case .modo:
                return PermissionMode.allCases.filter {
                    wanted.isEmpty || $0.title.lowercased().hasPrefix(wanted) || $0.rawValue.hasPrefix(wanted)
                }.map { mode in
                    CommandSuggestion(id: "modo:" + mode.rawValue, symbol: mode.rawValue == session.mode ? "checkmark" : mode.symbol,
                                      title: mode.title, detail: mode.summary, enabled: mode != .omitir,
                                      action: .argument(.modo, mode.rawValue))
                }
            case .esfuerzo:
                return Presentation.efforts.filter {
                    wanted.isEmpty || $0.label.lowercased().hasPrefix(wanted) || $0.value.hasPrefix(wanted)
                }.map { effort in
                    CommandSuggestion(id: "esfuerzo:" + effort.value,
                                      symbol: effort.value == session.effort ? "checkmark" : "gauge.with.dots.needle.50percent",
                                      title: effort.label, detail: effort.value == session.effort ? "en uso" : "",
                                      enabled: true, action: .argument(.esfuerzo, effort.value))
                }
            default:
                return []
            }
        }
        return SlashCommand.matching(input.name).map { command in
            let enabled = command.available(running: running, dormant: session.isDormant)
            var detail = command.summary
            if command == .compactar, session.timeline.contextLimit > 0 {
                detail += " (\(Int((session.timeline.contextFraction * 100).rounded())) % usado)"
            }
            if command == .compactar, session.timeline.compacting { detail += " · ya se está compactando" }
            if !enabled { detail += running ? " · espera a que acabe el turno" : " · continúa la sesión primero" }
            return CommandSuggestion(id: command.name, symbol: command.symbol, title: "/" + command.name, detail: detail,
                                     enabled: enabled, action: .command(command))
        }
    }

    private func move(_ step: Int) -> KeyPress.Result {
        let count = suggestions.count
        guard count > 0 else { return .ignored }
        selection = (min(selection, count - 1) + step + count) % count
        return .handled
    }

    /// Tab: completa el nombre sin ejecutar (como en Codex).
    private func complete(_ option: CommandSuggestion) {
        switch option.action {
        case let .command(command): text = "/" + command.name + (command.takesArgument ? " " : "")
        case let .argument(command, value): text = "/" + command.name + " " + value
        }
    }

    private func choose(_ option: CommandSuggestion) {
        guard option.enabled else { return }
        switch option.action {
        case let .command(command):
            if command.takesArgument {
                text = "/" + command.name + " "
            } else {
                run(command, nil)
            }
        case let .argument(command, value):
            run(command, value)
        }
    }

    private func run(_ command: SlashCommand, _ argument: String?) {
        let value = argument?.trimmingCharacters(in: .whitespaces) ?? ""
        guard command.available(running: session.timeline.status.isRunning, dormant: session.isDormant) else {
            session.addNotice("/\(command.name) no está disponible ahora.")
            return
        }
        text = ""
        switch command {
        case .nuevo: app.newChat(in: session.cwd)
        case .modelo:
            guard !value.isEmpty else { text = "/modelo "; return }
            session.change(model: value)
        case .esfuerzo:
            let effort = Presentation.efforts.first { $0.value == value || $0.label.lowercased() == value.lowercased() }
            guard let effort else { text = "/esfuerzo "; return }
            session.change(effort: effort.value)
        case .modo:
            let mode = PermissionMode.allCases.first { $0.rawValue == value || $0.title.lowercased() == value.lowercased() }
            guard let mode else { text = "/modo "; return }
            // Omitir permisos se activa desde el selector, que pide confirmación.
            guard mode != .omitir else { session.addNotice("Activa «Omitir permisos» desde el selector de modo."); return }
            session.change(mode: mode.rawValue)
        case .compactar: session.compact(instructions: value.isEmpty ? nil : value)
        case .procesos: session.showProcesses()
        case .cerrarProcesos: session.stopProcess("todos")
        case .estado: session.showStatus()
        case .revisar, .inicializar: session.send(command.prompt ?? "")
        case .renombrar:
            guard !value.isEmpty else { text = "/renombrar "; return }
            withRow { app.rename($0, to: value) }
        case .fijar: withRow { app.mark($0, "fijada", $0.pinned ? "no" : "sí") }
        case .bifurcar: withRow { app.fork($0) }
        case .archivar:
            withRow { row in
                app.mark(row, "archivada", "sí")
                app.route = .new
            }
        case .copiar:
            guard let last = session.timeline.lastAgentText else { session.addNotice("Aún no hay respuesta que copiar."); return }
            NSPasteboard.general.clearContents()
            NSPasteboard.general.setString(last, forType: .string)
        case .exportar: export()
        case .detener: session.stop()
        }
    }

    /// Las órdenes sobre el chat necesitan que la sesión ya tenga id en la barra lateral.
    private func withRow(_ action: (SidebarRow) -> Void) {
        if let row = app.row(for: session) { action(row) } else { session.addNotice("Esta sesión aún no está guardada.") }
    }

    private func export() {
        let panel = NSSavePanel()
        panel.allowedContentTypes = [UTType(filenameExtension: "md") ?? .plainText]
        let base = session.title.prefix(40).replacingOccurrences(of: "/", with: "-")
        panel.nameFieldStringValue = (base.isEmpty ? "conversacion" : base) + ".md"
        panel.directoryURL = session.cwd
        guard panel.runModal() == .OK, let url = panel.url else { return }
        do {
            try session.timeline.markdown(title: session.title).write(to: url, atomically: true, encoding: .utf8)
        } catch {
            session.addNotice("No se pudo guardar: \(error.localizedDescription)")
        }
    }
}

/// Una fila del menú de «/».
struct CommandSuggestion: Identifiable {
    enum Action {
        case command(SlashCommand)
        case argument(SlashCommand, String)
    }

    let id: String
    let symbol: String
    let title: String
    let detail: String
    let enabled: Bool
    let action: Action
}

/// Menú de órdenes sobre el compositor, como el de Codex: icono, nombre y descripción;
/// ↑↓ para moverse, ↩ para ejecutar, ⇥ para completar y Esc para cerrar.
struct CommandPopup: View {
    let suggestions: [CommandSuggestion]
    let selection: Int
    let choose: (CommandSuggestion) -> Void

    var body: some View {
        ScrollViewReader { proxy in
            ScrollView {
                VStack(alignment: .leading, spacing: 2) {
                    ForEach(Array(suggestions.enumerated()), id: \.element.id) { index, option in
                        Button { choose(option) } label: {
                            HStack(spacing: 12) {
                                Image(systemName: option.symbol).font(.system(size: 14))
                                    .foregroundStyle(Theme.secondary).frame(width: 20)
                                Text(option.title).font(.system(size: 14, weight: .medium))
                                    .foregroundStyle(option.enabled ? Theme.text : Theme.muted)
                                Text(option.detail).font(.system(size: 13.5)).foregroundStyle(Theme.muted)
                                    .lineLimit(1).truncationMode(.tail)
                                Spacer(minLength: 0)
                            }
                            .padding(.horizontal, 12).frame(height: 34)
                            .background(index == selection ? Theme.selection : .clear, in: RoundedRectangle(cornerRadius: 10))
                            .contentShape(Rectangle())
                        }
                        .buttonStyle(.plain).id(option.id)
                        .opacity(option.enabled ? 1 : 0.6)
                    }
                }
                .padding(6)
            }
            .frame(height: min(CGFloat(suggestions.count) * 36 + 12, 8 * 36 + 12))
            .onChange(of: selection) { _, now in
                if let option = suggestions[safe: now] { proxy.scrollTo(option.id) }
            }
        }
        .background(Theme.card, in: RoundedRectangle(cornerRadius: 18))
        .overlay(RoundedRectangle(cornerRadius: 18).stroke(Theme.border))
        .accessibilityLabel("Órdenes")
    }
}

extension Array {
    subscript(safe index: Int) -> Element? { indices.contains(index) ? self[index] : nil }
}

struct ItemView: View {
    let item: TimelineItem
    @ObservedObject var session: LiveSession
    let isFirstPending: Bool
    var closesRun = false
    var liveWork = false
    @State private var expanded = false

    var body: some View {
        switch item {
        case let .user(itemID, text, queued):
            HStack {
                Spacer(minLength: 120)
                VStack(alignment: .trailing, spacing: 4) {
                    let parts = ImageTags.split(text)
                    if !parts.images.isEmpty {
                        HStack(spacing: 6) {
                            ForEach(parts.images, id: \.self) { path in
                                Thumbnail(url: URL(fileURLWithPath: path), size: 96).help(path)
                            }
                        }
                    }
                    if !parts.text.isEmpty {
                        Text(parts.text).font(Theme.body).lineSpacing(Theme.bodyLineSpacing).textSelection(.enabled)
                            .fixedSize(horizontal: false, vertical: true)
                            .padding(.horizontal, 18).padding(.vertical, 13)
                            .background(Theme.bubble, in: RoundedRectangle(cornerRadius: 20))
                    }
                    if queued {
                        HStack(spacing: 12) {
                            Text("en cola").foregroundStyle(Theme.muted)
                            if session.timeline.status.isRunning {
                                Button("Redirigir") { session.redirect() }
                                    .help("Que lo lea ya, sin cortar lo que está haciendo (descarta lo que estaba pensando)")
                                Button("Enviar ahora") { session.sendNow(text) }
                                    .help("Detener el turno en marcha y enviar este mensaje")
                            }
                        }
                        .font(.system(size: 11.5)).buttonStyle(.plain).foregroundStyle(Theme.secondary)
                    } else {
                        UserActions(text: ImageTags.split(text).text, itemID: itemID, session: session)
                    }
                }
                .opacity(queued ? 0.7 : 1)
            }
        case let .agent(id, text):
            VStack(alignment: .leading, spacing: 14) {
                MarkdownView(text: text, folder: session.cwd)
                if closesRun { AgentActions(text: text, time: session.timeline.agentTimes[id]) }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        case let .events(_, events):
            WorkLog(events: events, running: liveWork, expanded: $expanded)
        case let .permission(_, request, state):
            PermissionCard(request: request, state: state, session: session, isFirstPending: isFirstPending)
        case let .notice(_, text):
            Text(text).font(.system(size: 13)).foregroundStyle(Theme.warning).frame(maxWidth: .infinity, alignment: .leading)
        case let .wrapUp(id, wrapUp, answered):
            WrapUpCard(wrapUp: wrapUp, answered: answered, session: session) { session.dismissWrapUp(id) }
        case let .info(_, title, rows):
            VStack(alignment: .leading, spacing: 8) {
                Text(title).font(.system(size: 14, weight: .semibold))
                Grid(alignment: .leading, horizontalSpacing: 16, verticalSpacing: 5) {
                    ForEach(Array(rows.enumerated()), id: \.offset) { _, row in
                        GridRow {
                            Text(row.name).foregroundStyle(Theme.muted)
                            Text(row.value).foregroundStyle(Theme.secondary).textSelection(.enabled)
                        }
                    }
                }
                .font(.system(size: 13))
            }
            .padding(16).frame(maxWidth: .infinity, alignment: .leading)
            .overlay(RoundedRectangle(cornerRadius: 14).stroke(Theme.border))
        case let .compaction(_, compaction):
            CompactionMarker(compaction: compaction)
        }
    }

}

/// Lo que hizo el agente entre mensajes, como en Codex: «Trabajó durante 54 s ›» con una
/// línea debajo; al abrirlo, cada acción en una fila.
struct WorkLog: View {
    let events: [ToolEvent]
    let running: Bool
    @Binding var expanded: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Button { withAnimation(.easeOut(duration: 0.15)) { expanded.toggle() } } label: {
                HStack(spacing: 6) {
                    TimelineView(.periodic(from: .now, by: 1)) { context in
                        Text(title(now: running ? context.date.timeIntervalSince1970 : nil))
                            .font(.system(size: 14)).foregroundStyle(Theme.muted).monospacedDigit()
                    }
                    Image(systemName: "chevron.right").font(.system(size: 11, weight: .semibold))
                        .foregroundStyle(Theme.muted).rotationEffect(.degrees(expanded ? 90 : 0))
                }
                .padding(.vertical, 6).contentShape(Rectangle())
            }
            .buttonStyle(.plain).accessibilityLabel(title(now: nil))
            Divider().overlay(Theme.border)
            if expanded {
                VStack(alignment: .leading, spacing: 10) {
                    ForEach(events.filter { !$0.text.isEmpty }) { event in
                        EventRow(event: event)
                    }
                }
                .padding(.top, 12)
            }
        }
    }

    /// Con `now`, mientras trabaja, el tiempo va hasta ahora y no hasta el último evento.
    private func title(now: Double?) -> String {
        let actions = events.filter { $0.text.hasPrefix("Inicio ") }.count
        let count = actions == 0 ? "" : " · \(actions) \(actions == 1 ? "acción" : "acciones")"
        if let now, let first = events.map(\.time).filter({ $0 > 0 }).min(), now > first {
            return "Trabajando " + ToolEvent.durationText(now - first) + count
        }
        if let seconds = ToolEvent.duration(of: events) {
            return "\(running ? "Trabajando" : "Trabajó durante") \(ToolEvent.durationText(seconds))\(count)"
        }
        return running ? "Trabajando…" : (actions == 0 ? "\(events.count) eventos" : "\(actions) \(actions == 1 ? "acción" : "acciones")")
    }
}

/// Una acción del registro: su título y, al pulsarla, la salida completa.
struct EventRow: View {
    let event: ToolEvent
    @State private var open = false

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Button { open.toggle() } label: {
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Image(systemName: icon).font(.system(size: 11)).foregroundStyle(Theme.muted).frame(width: 14)
                    Text(event.title).font(.system(size: 13)).foregroundStyle(Theme.secondary).lineLimit(1)
                    Spacer(minLength: 0)
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            if open {
                Text(event.text.count > 4000 ? String(event.text.prefix(4000)) + "…" : event.text)
                    .font(.system(size: 12, design: .monospaced)).foregroundStyle(Theme.secondary)
                    .textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
                    .padding(12).frame(maxWidth: .infinity, alignment: .leading)
                    .background(Theme.field, in: RoundedRectangle(cornerRadius: 10))
            }
        }
    }

    private var icon: String {
        let title = event.title
        if title.hasPrefix("run_") { return "terminal" }
        if title.hasPrefix("read_file") || title.hasPrefix("list_files") { return "doc.text" }
        if title.hasPrefix("search") || title.hasPrefix("web_search") { return "magnifyingglass" }
        if title.hasPrefix("edit_file") || title.hasPrefix("write_file") || title.hasPrefix("apply_patch") { return "pencil" }
        if title.hasPrefix("wrap_up") { return "checkmark.circle" }
        return event.role == "system" ? "info.circle" : "circle"
    }
}

/// Fila bajo cada mensaje del usuario, como en Claude: hace cuánto, copiar, volver a este
/// punto (la conversación vuelve a antes del mensaje y su texto queda en el cuadro para
/// cambiarlo) y bifurcar (una sesión nueva que llega hasta aquí). Los archivos no cambian.
struct UserActions: View {
    let text: String
    let itemID: Int
    @ObservedObject var session: LiveSession
    @EnvironmentObject private var app: AppModel
    @State private var copied = false

    private var timeline: Timeline { session.timeline }
    private var turn: String? { timeline.userTurns[itemID] }
    /// Volver y bifurcar copian la sesión: no con un turno en marcha ni antes de que se guarde.
    private var canBranch: Bool { turn != nil && session.sessionID != nil && !timeline.status.isRunning }

    var body: some View {
        HStack(spacing: 12) {
            if let time = timeline.userTimes[itemID] {
                TimelineView(.periodic(from: .now, by: 30)) { context in
                    Text(Self.relative(Date(timeIntervalSince1970: time), now: context.date))
                        .font(.system(size: 12.5)).foregroundStyle(Theme.muted)
                }
                Text("·").foregroundStyle(Theme.muted)
            }
            icon(copied ? "checkmark" : "doc.on.doc", copied ? "Copiado" : "Copiar mensaje", enabled: true) {
                NSPasteboard.general.clearContents()
                NSPasteboard.general.setString(text, forType: .string)
                copied = true
                DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) { copied = false }
            }
            icon("arrow.counterclockwise", "Volver a este mensaje: la conversación sigue desde antes de él y su texto queda en el cuadro para cambiarlo. Los archivos no cambian.",
                 enabled: canBranch) { goBack() }
            icon("arrow.triangle.branch", "Bifurcar: una sesión nueva con la conversación hasta este mensaje y su respuesta",
                 enabled: canBranch) {
                if let id = session.sessionID, let turn { app.fork(sessionID: id, turn: turn, draft: nil) }
            }
        }
    }

    private func goBack() {
        guard let id = session.sessionID, let before = timeline.turnBefore(user: itemID) else { return }
        if let previous = before {
            app.fork(sessionID: id, turn: previous, draft: text)
        } else {
            app.restart(in: session.cwd, with: text)
        }
    }

    private func icon(_ symbol: String, _ help: String, enabled: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: symbol).font(.system(size: 12.5)).foregroundStyle(Theme.muted)
                .frame(width: 20, height: 20).contentShape(Rectangle())
        }
        .buttonStyle(.plain).help(help).accessibilityLabel(help)
        .disabled(!enabled).opacity(enabled ? 1 : 0.4)
    }

    /// «Hace un momento», «Hace 12 minutos», «Hace 3 horas»; si pasó más de un día, la fecha.
    static func relative(_ date: Date, now: Date = Date()) -> String {
        let seconds = now.timeIntervalSince(date)
        if seconds < 60 { return "Hace un momento" }
        if seconds < 3600 { let m = Int(seconds / 60); return m == 1 ? "Hace 1 minuto" : "Hace \(m) minutos" }
        if seconds < 86_400 { let h = Int(seconds / 3600); return h == 1 ? "Hace 1 hora" : "Hace \(h) horas" }
        return AgentActions.format(date, now: now)
    }
}

/// Fila bajo la respuesta final: copiar el texto y la hora, como en Codex.
struct AgentActions: View {
    let text: String
    let time: Double?
    @State private var copied = false

    var body: some View {
        HStack(spacing: 14) {
            Button {
                NSPasteboard.general.clearContents()
                NSPasteboard.general.setString(text, forType: .string)
                copied = true
                DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) { copied = false }
            } label: {
                Image(systemName: copied ? "checkmark" : "doc.on.doc").font(.system(size: 13))
                    .foregroundStyle(Theme.muted).frame(width: 22, height: 22).contentShape(Rectangle())
            }
            .buttonStyle(.plain).help(copied ? "Copiado" : "Copiar respuesta").accessibilityLabel("Copiar respuesta")
            if let time {
                Text(Self.format(Date(timeIntervalSince1970: time))).font(.system(size: 13)).foregroundStyle(Theme.muted)
            }
        }
    }

    /// «1:39 p. m.» hoy; «viernes, 1:39 p. m.» esta semana; si no, la fecha.
    static func format(_ date: Date, now: Date = Date()) -> String {
        let calendar = Calendar.current
        let clock = date.formatted(date: .omitted, time: .shortened)
        if calendar.isDateInToday(date) { return clock }
        if let days = calendar.dateComponents([.day], from: date, to: now).day, days < 7 {
            return date.formatted(.dateTime.weekday(.wide)) + ", " + clock
        }
        return date.formatted(date: .abbreviated, time: .shortened)
    }
}

/// «Conversación compactada», como en Claude Code: una línea con antes → después y el
/// resumen que ahora lee el modelo, plegado.
struct CompactionMarker: View {
    let compaction: Compaction
    @State private var open = false

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Button { withAnimation(.easeOut(duration: 0.15)) { open.toggle() } } label: {
                HStack(spacing: 10) {
                    line
                    HStack(spacing: 6) {
                        Image(systemName: "arrow.down.right.and.arrow.up.left").font(.system(size: 11))
                        Text(title).font(.system(size: 12.5, weight: .medium))
                        Image(systemName: "chevron.down").font(.system(size: 9, weight: .bold))
                            .rotationEffect(.degrees(open ? 0 : -90))
                    }
                    .foregroundStyle(Theme.secondary).fixedSize()
                    line
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .help(open ? "Ocultar el resumen" : "Ver el resumen que ahora lee el modelo")
            .accessibilityLabel(title).accessibilityValue(open ? "resumen abierto" : "resumen plegado")
            if open {
                VStack(alignment: .leading, spacing: 8) {
                    if let instructions = compaction.instructions {
                        Text("Pediste: " + instructions).font(.system(size: 12.5)).foregroundStyle(Theme.muted)
                    }
                    Text(compaction.summary).font(.system(size: 13)).foregroundStyle(Theme.secondary)
                        .textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
                    Text("El historial completo sigue guardado; el modelo sigue desde este resumen.")
                        .font(.system(size: 11.5)).foregroundStyle(Theme.muted)
                }
                .padding(14).frame(maxWidth: .infinity, alignment: .leading)
                .background(Theme.card, in: RoundedRectangle(cornerRadius: 10))
                .overlay(RoundedRectangle(cornerRadius: 10).stroke(Theme.cardBorder))
            }
        }
    }

    private var line: some View { Rectangle().fill(Theme.border).frame(height: 1) }

    private var title: String {
        let kind = compaction.reason == "manual" ? "Conversación compactada" : "Compactada automáticamente"
        guard compaction.before > 0 else { return kind }
        return kind + " · " + ContextFormat.tokens(compaction.before) + " → " + ContextFormat.tokens(compaction.after)
    }
}

/// La lista de tareas del agente, como la de Claude Code: hecho, en curso y pendiente.
/// Si escribiste después, es «el plan de antes de tu último mensaje» hasta que llegue otro.
struct PlanCard: View {
    let plan: Plan
    let fromBefore: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 9) {
            if fromBefore {
                Text("Plan de antes de tu último mensaje").font(.system(size: 12.5)).foregroundStyle(Theme.muted)
            }
            if let explanation = plan.explanation, !fromBefore {
                Text(explanation).font(.system(size: 12.5)).foregroundStyle(Theme.muted)
                    .fixedSize(horizontal: false, vertical: true)
            }
            ForEach(Array(plan.steps.enumerated()), id: \.offset) { _, step in
                HStack(alignment: .firstTextBaseline, spacing: 9) {
                    icon(step.status).frame(width: 16)
                    Text(step.text).font(.system(size: 14))
                        .foregroundStyle(step.status == .completed ? Theme.muted : Theme.text)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .accessibilityElement(children: .combine)
                .accessibilityLabel(label(step))
            }
        }
        .padding(.horizontal, 16).padding(.vertical, 14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Theme.card.opacity(0.5), in: RoundedRectangle(cornerRadius: 12))
        .overlay(RoundedRectangle(cornerRadius: 12).stroke(Theme.border))
        .accessibilityElement(children: .contain)
        .accessibilityLabel(fromBefore ? "Plan de antes de tu último mensaje" : "Plan")
    }

    @ViewBuilder private func icon(_ status: PlanStep.Status) -> some View {
        switch status {
        case .completed:
            Image(systemName: "checkmark.circle.fill").foregroundStyle(Theme.muted)
        case .inProgress:
            Image(systemName: "ellipsis.circle").foregroundStyle(Theme.text)
        case .pending:
            Image(systemName: "circle.dashed").foregroundStyle(Theme.muted)
        }
    }

    private func label(_ step: PlanStep) -> String {
        switch step.status {
        case .completed: return step.text + ", hecho"
        case .inProgress: return step.text + ", en curso"
        case .pending: return step.text + ", pendiente"
        }
    }
}

/// Mientras el motor resume la conversación.
struct CompactingLine: View {
    var body: some View {
        HStack(spacing: 8) {
            ProgressView().controlSize(.small)
            Text("Compactando la conversación…").font(.system(size: 13, weight: .medium)).foregroundStyle(Theme.secondary)
            Text("Puedes seguir escribiendo; tu mensaje irá después del resumen.")
                .font(.system(size: 12)).foregroundStyle(Theme.muted)
        }
        .accessibilityElement(children: .combine)
    }
}

/// «566.5k», «1M», «950»: tokens como los enseña el panel de Claude Code.
enum ContextFormat {
    static func tokens(_ value: Int) -> String {
        if value >= 1_000_000 {
            let millions = Double(value) / 1_000_000
            return millions == millions.rounded() ? "\(Int(millions))M" : String(format: "%.1fM", millions)
        }
        guard value >= 1000 else { return "\(value)" }
        let thousands = Double(value) / 1000
        return thousands == thousands.rounded() ? "\(Int(thousands))k" : String(format: "%.1fk", thousands)
    }
}

/// «Ejecutándose · 1 min 5 s»: la orden aprobada sigue corriendo; el tiempo avanza solo.
struct RunningLine: View {
    let since: Double

    var body: some View {
        TimelineView(.periodic(from: .now, by: 1)) { context in
            HStack(spacing: 8) {
                ProgressView().controlSize(.small)
                Text("Ejecutándose · " + ToolEvent.durationText(context.date.timeIntervalSince1970 - since))
                    .font(.system(size: 13, weight: .medium)).foregroundStyle(Theme.secondary).monospacedDigit()
                Text("Compilar o abrir una app puede tardar varios minutos.")
                    .font(.system(size: 12)).foregroundStyle(Theme.muted)
            }
        }
        .accessibilityElement(children: .combine)
    }
}

struct PermissionCard: View {
    let request: PermissionRequest
    let state: PermissionState
    @ObservedObject var session: LiveSession
    let isFirstPending: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            title
            if !request.reasons.isEmpty { reasons }
            if let mcp = request.mcp {
                McpCallDetail(call: mcp)
            } else if let app = request.app {
                AppTargetDetail(app: app)
            } else {
                Text(request.command).font(Theme.mono).textSelection(.enabled)
            }
            if !request.purpose.isEmpty, request.mcp == nil {
                Text(request.purpose).font(.system(size: 12.5)).foregroundStyle(Theme.muted)
            }
            if let justification = request.justification {
                detailLine("Justificación", justification)
            }
            if let rule = request.rule {
                detailLine("Regla", rule)
            }
            if let detail = request.detail, detail != request.justification,
               request.reasons.contains(where: \.leavesSandbox) {
                detailLine(request.retryOf != nil ? "Intento anterior" : "Motivo", detail)
            }
            if let running = session.timeline.runningPermission, running.id == request.id {
                RunningLine(since: running.since)
            }
            if case .pending = state {
                HStack(spacing: 10) {
                    approve
                    if request.forSession {
                        Button { session.answer(request.id, approved: true, session: true) } label: {
                            Text("Permitir en esta sesión").fontWeight(.semibold).padding(.horizontal, 14)
                                .frame(minHeight: 36)
                                .overlay(RoundedRectangle(cornerRadius: 8).stroke(Theme.cardBorder))
                        }
                        .buttonStyle(.plain)
                        .disabled(session.answered.contains(request.id))
                        .help(request.app != nil
                              ? "Aprueba y no vuelve a preguntar por esta app en esta sesión."
                              : request.reasons.contains(where: \.leavesSandbox)
                              ? "Aprueba y, hasta que acabe la sesión, esta misma orden saldrá del sandbox sin preguntar."
                              : "Aprueba y no vuelve a preguntar por esta misma orden en esta sesión.")
                    }
                    Button { session.answer(request.id, approved: false) } label: {
                        Text("Negar").fontWeight(.semibold).padding(.horizontal, 16).frame(minHeight: 36)
                            .overlay(RoundedRectangle(cornerRadius: 8).stroke(Theme.cardBorder))
                    }
                    .buttonStyle(.plain)
                    .disabled(session.answered.contains(request.id))
                }
            }
        }
        .padding(.horizontal, 18).padding(.vertical, 16)
        .frame(maxWidth: 660, alignment: .leading)
        .background(Theme.card, in: RoundedRectangle(cornerRadius: 12))
        .overlay(RoundedRectangle(cornerRadius: 12).stroke(Theme.cardBorder))
        .accessibilityElement(children: .contain)
        .accessibilityLabel("Permiso")
    }

    @ViewBuilder private var approve: some View {
        let label = HStack(spacing: 6) {
            Text("Aprobar").fontWeight(.semibold)
            Text("⌘↩").font(.system(size: 11))
                .padding(.horizontal, 5).padding(.vertical, 1)
                .overlay(RoundedRectangle(cornerRadius: 4).stroke(Color(hex: 0xC9C6BE)))
        }
        .foregroundStyle(Theme.dark)
        .padding(.horizontal, 16).frame(minHeight: 36)
        .background(Theme.light, in: RoundedRectangle(cornerRadius: 8))
        let button = Button { session.answer(request.id, approved: true) } label: { label }
            .buttonStyle(.plain)
            .disabled(session.answered.contains(request.id))
        if isFirstPending {
            // Sólo la solicitud más antigua responde a ⌘↩, siempre por acción del usuario.
            button.keyboardShortcut(.return, modifiers: .command)
        } else {
            button
        }
    }

    /// Un distintivo por motivo y un único alcance: el de lo que concede aprobar la decisión entera.
    private var reasons: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 6) {
                ForEach(request.reasons, id: \.self) { reason in
                    let color = reason.leavesSandbox ? Theme.danger : Theme.warning
                    Text(reason.title).font(.system(size: 11.5, weight: .semibold)).foregroundStyle(color)
                        .padding(.horizontal, 8).padding(.vertical, 2)
                        .overlay(RoundedRectangle(cornerRadius: 5).stroke(color.opacity(0.6)))
                }
            }
            if let scope = request.scope {
                Text(scope).font(.system(size: 12)).foregroundStyle(Theme.secondary)
            }
        }
        .accessibilityElement(children: .combine)
        .accessibilityLabel("Motivo: " + request.reasons.map(\.title).joined(separator: ", "))
    }

    private func detailLine(_ label: String, _ value: String) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 4) {
            Text(label + ":").fontWeight(.semibold)
            Text(value).textSelection(.enabled)
        }
        .font(.system(size: 12.5)).foregroundStyle(Theme.secondary)
    }

    @ViewBuilder private var title: some View {
        switch state {
        case .pending:
            Text(request.mcp != nil ? "Pide permiso · Herramienta MCP"
                 : request.reasons.first.map { "Pide permiso · \($0.title)" } ?? "Quiere ejecutar un comando")
                .foregroundStyle(Theme.warning).fontWeight(.semibold)
        case .approved:
            Text("Aprobado").foregroundStyle(Theme.alfalfa).fontWeight(.semibold)
        case .approvedForSession:
            Text("Aprobado en esta sesión").foregroundStyle(Theme.alfalfa).fontWeight(.semibold)
        case let .denied(reason):
            Text(reason.isEmpty || reason == "Respuesta del cliente" ? "Negado · no se ejecutó" : "Negado · \(reason.lowercased())")
                .foregroundStyle(Theme.danger).fontWeight(.semibold)
        }
    }
}

/// La app que el agente quiere mirar o manejar, con su bundle id para distinguirla.
struct AppTargetDetail: View {
    let app: AppTarget

    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            HStack(spacing: 6) {
                Image(systemName: "macwindow.and.cursorarrow")
                Text(app.name).fontWeight(.semibold)
                if let action = app.action { Text("· " + action).foregroundStyle(Theme.muted) }
            }
            if let bundle = app.bundle {
                Text(bundle).font(Theme.mono).foregroundStyle(Theme.muted).textSelection(.enabled)
            }
            if let level = app.levelText {
                Label(level, systemImage: "eye").font(.system(size: 12)).foregroundStyle(Theme.secondary)
            }
        }
        .font(.system(size: 13))
    }
}

/// Servidor, herramienta y argumentos de una llamada MCP; el motor ya redactó las credenciales.
struct McpCallDetail: View {
    let call: McpCall

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 6) {
                Text(call.server).fontWeight(.semibold)
                Text("·").foregroundStyle(Theme.muted)
                Text(call.tool).font(Theme.mono)
            }
            .font(.system(size: 13))
            if call.arguments.isEmpty {
                Text("Sin argumentos").font(.system(size: 12.5)).foregroundStyle(Theme.muted)
            } else {
                VStack(alignment: .leading, spacing: 3) {
                    ForEach(call.arguments, id: \.key) { argument in
                        HStack(alignment: .firstTextBaseline, spacing: 6) {
                            Text(argument.key + ":").fontWeight(.semibold).foregroundStyle(Theme.secondary)
                            Text(argument.value).lineLimit(4).truncationMode(.tail)
                        }
                        .font(Theme.mono)
                    }
                }
                .textSelection(.enabled)
                .padding(10).frame(maxWidth: .infinity, alignment: .leading)
                .background(Theme.field, in: RoundedRectangle(cornerRadius: 8))
            }
            if !call.callID.isEmpty {
                Text("Llamada \(call.callID)").font(.system(size: 11.5)).foregroundStyle(Theme.muted).textSelection(.enabled)
            }
        }
        .accessibilityElement(children: .combine)
        .accessibilityLabel("Herramienta MCP \(call.tool) del servidor \(call.server)")
    }
}

/// Texto del agente con Markdown, como lo pinta Codex: títulos, citas, listas, tareas,
/// tablas, separadores, código y formato en línea.
struct MarkdownView: View {
    let text: String
    /// Carpeta de la sesión: ahí se ejecutan los bloques de terminal.
    var folder: URL? = nil

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            ForEach(Array(MarkdownText.blocks(text).enumerated()), id: \.offset) { _, block in
                view(for: block)
            }
        }
        .font(Theme.body).lineSpacing(Theme.bodyLineSpacing).tint(Theme.link)
        .fixedSize(horizontal: false, vertical: true)
    }

    @ViewBuilder private func view(for block: MarkdownBlock) -> some View {
        switch block {
        case let .paragraph(value):
            Text(MarkdownText.inline(value)).textSelection(.enabled)
        case let .heading(level, value):
            Text(MarkdownText.inline(value))
                .font(.system(size: [22, 18, 16, 15, 15, 15][min(level, 6) - 1],
                              weight: level <= 2 ? .bold : .semibold))
                .padding(.top, level <= 2 ? 10 : 4)
                .textSelection(.enabled)
        case let .bullet(value, level):
            item(marker: Text(level == 0 ? "•" : "◦").foregroundStyle(Theme.text), value, level)
        case let .numbered(number, value, level):
            item(marker: Text("\(number).").foregroundStyle(Theme.secondary).monospacedDigit(), value, level)
        case let .task(done, value, level):
            item(marker: Image(systemName: done ? "checkmark.square.fill" : "square")
                    .foregroundStyle(done ? Theme.alfalfa : Theme.muted), value, level)
        case let .quote(value):
            HStack(alignment: .top, spacing: 10) {
                RoundedRectangle(cornerRadius: 1.5).fill(Theme.alfalfa.opacity(0.7)).frame(width: 3)
                Text(MarkdownText.inline(value)).foregroundStyle(Theme.secondary).textSelection(.enabled)
            }
            .fixedSize(horizontal: false, vertical: true)
        case let .code(value, language):
            CodeBlockView(code: value, language: language, folder: folder)
        case let .table(header, rows):
            MarkdownTable(header: header, rows: rows)
        case .rule:
            Divider().overlay(Theme.border).padding(.vertical, 4)
        }
    }

    /// Viñeta en una columna propia, como en Codex: el texto de varias líneas queda alineado.
    private func item(marker: some View, _ value: String, _ level: Int) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 0) {
            marker.frame(width: 26, alignment: .center)
            Text(MarkdownText.inline(value)).textSelection(.enabled)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
        .padding(.leading, 4 + CGFloat(level) * 24)
    }
}

/// Caja «siguiente paso» del final del turno, como la de Claude Code: resumen, pregunta y
/// hasta dos respuestas. La primera es la recomendada y responde también con ⌘↩.
struct WrapUpCard: View {
    let wrapUp: WrapUp
    let answered: Bool
    @ObservedObject var session: LiveSession
    let dismiss: () -> Void

    private var canReply: Bool {
        !answered && session.canSend && !session.timeline.status.isRunning
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack(alignment: .top, spacing: 12) {
                Text(MarkdownText.inline(wrapUp.recap)).font(.system(size: 13.5)).foregroundStyle(Theme.secondary)
                    .textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
                    .frame(maxWidth: .infinity, alignment: .leading)
                Button(action: dismiss) {
                    Image(systemName: "xmark").font(.system(size: 12, weight: .medium)).foregroundStyle(Theme.muted)
                        .frame(width: 22, height: 22).contentShape(Rectangle())
                }
                .buttonStyle(.plain).help("Cerrar").accessibilityLabel("Cerrar resumen")
            }
            if let ask = wrapUp.ask {
                Text(ask).font(.system(size: 13.5)).foregroundStyle(Theme.text).textSelection(.enabled)
                if !wrapUp.options.isEmpty {
                    HStack(spacing: 8) {
                        ForEach(Array(wrapUp.options.enumerated()), id: \.offset) { index, option in
                            optionButton(option, primary: index == 0)
                        }
                    }
                    .disabled(!canReply).opacity(canReply ? 1 : 0.5)
                }
            }
        }
        .padding(20).frame(maxWidth: .infinity, alignment: .leading)
        .overlay(RoundedRectangle(cornerRadius: 14).stroke(Theme.border))
    }

    @ViewBuilder
    private func optionButton(_ option: String, primary: Bool) -> some View {
        let button = Button { session.reply(option) } label: {
            HStack(spacing: 8) {
                Text(option).font(.system(size: 13, weight: .medium))
                if primary && canReply {
                    Text("⌘↩").font(.system(size: 11)).foregroundStyle(Theme.dark.opacity(0.55))
                }
            }
            .foregroundStyle(primary ? Theme.dark : Theme.text)
            .padding(.horizontal, 12).padding(.vertical, 7)
            .background(primary ? Theme.light : Theme.selection, in: RoundedRectangle(cornerRadius: 8))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain).accessibilityLabel(option)
        if primary && canReply {
            button.keyboardShortcut(.return, modifiers: .command)
        } else {
            button
        }
    }
}

/// Bloque de código con Copiar y, si es de terminal, Ejecutar en Terminal.app.
struct CodeBlockView: View {
    let code: String
    let language: String?
    let folder: URL?
    @State private var copied = false
    @State private var error: String?

    var body: some View {
        ZStack(alignment: .topTrailing) {
            VStack(alignment: .leading, spacing: 6) {
                if let language { Text(language).font(.system(size: 11)).foregroundStyle(Theme.muted) }
                Text(code).font(Theme.mono).textSelection(.enabled)
                    .padding(.trailing, 64)
                if let error { Text(error).font(.system(size: 11.5)).foregroundStyle(Theme.danger) }
            }
            .padding(10).frame(maxWidth: .infinity, alignment: .leading)
            HStack(spacing: 4) {
                if let folder, CodeActions.isRunnable(language: language, code: code) {
                    iconButton("play", "Ejecutar en Terminal") { run(in: folder) }
                }
                iconButton(copied ? "checkmark" : "doc.on.doc", copied ? "Copiado" : "Copiar") { copy() }
            }
            .padding(6)
        }
        .background(Theme.field, in: RoundedRectangle(cornerRadius: 8))
    }

    private func iconButton(_ symbol: String, _ help: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: symbol).font(.system(size: 12.5)).foregroundStyle(Theme.secondary)
                .frame(width: 26, height: 26).contentShape(Rectangle())
        }
        .buttonStyle(.plain).help(help).accessibilityLabel(help)
    }

    private func copy() {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(CodeActions.command(from: code), forType: .string)
        copied = true
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) { copied = false }
    }

    private func run(in folder: URL) {
        let source = CodeActions.terminalScript(command: CodeActions.command(from: code), folder: folder.path)
        var info: NSDictionary?
        NSAppleScript(source: source)?.executeAndReturnError(&info)
        error = info.map { _ in "No se pudo abrir Terminal. Revisa en Ajustes del Sistema → Privacidad → Automatización que AlfalfaCode pueda controlarla." }
    }
}

/// Tabla GFM: cabecera en negrita y una línea entre filas.
struct MarkdownTable: View {
    let header: [String]
    let rows: [[String]]

    var body: some View {
        Grid(alignment: .topLeading, horizontalSpacing: 16, verticalSpacing: 8) {
            GridRow {
                ForEach(Array(header.enumerated()), id: \.offset) { _, cell in
                    Text(MarkdownText.inline(cell)).fontWeight(.semibold).textSelection(.enabled)
                }
            }
            ForEach(Array(rows.enumerated()), id: \.offset) { _, row in
                Divider().overlay(Theme.border).gridCellUnsizedAxes(.horizontal)
                GridRow {
                    ForEach(Array(row.enumerated()), id: \.offset) { _, cell in
                        Text(MarkdownText.inline(cell)).textSelection(.enabled)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Theme.card, in: RoundedRectangle(cornerRadius: 8))
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(Theme.cardBorder))
    }
}
