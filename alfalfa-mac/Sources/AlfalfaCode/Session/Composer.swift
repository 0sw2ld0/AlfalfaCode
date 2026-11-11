import AlfalfaKit
import AppKit
import SwiftUI
import UniformTypeIdentifiers

/// Cuadro de composición de la sesión: menú de órdenes con «/», cuotas, imágenes adjuntas,
/// el texto, enviar o detener y, debajo, la barra con modelo, esfuerzo, modo y estado.
struct Composer: View {

    // MARK: - Propiedades

    @ObservedObject var session: LiveSession

    var body: some View {
        VStack(spacing: 10) {
            let options = suggestions
            if !options.isEmpty {
                CommandPopup(suggestions: options, selection: min(selection, options.count - 1)) { choose($0) }
            }
            if Presentation.provider(of: session.model) == "alfalfa" {
                let quotas = session.timeline.quotas.isEmpty ? app.alfalfaQuotas : session.timeline.quotas
                if !quotas.isEmpty { QuotaLine(quotas: quotas).frame(maxWidth: .infinity, alignment: .leading) }
            }
            AttachmentStrip(images: $session.draftImages, blocked: !allowsImages)
                .frame(maxWidth: .infinity, alignment: .leading)
            ImageNoticeLine(notice: session.imageNotice)
            HStack(spacing: 10) {
                attachButton
                field
                if suggestion != nil { tabHint }
                if session.timeline.status.isRunning { stopButton } else { sendButton }
            }
            .padding(.horizontal, 16).padding(.vertical, 12)
            .background(Theme.field, in: RoundedRectangle(cornerRadius: 18))
            .overlay(RoundedRectangle(cornerRadius: 18).stroke(Theme.border))
            .onDrop(of: [.fileURL], isTargeted: nil) { providers in
                // Los archivos se adjuntan siempre; las imágenes, sólo si el modelo las ve.
                ImagePicker.drop(providers, into: $session.draftImages, withImages: allowsImages) {
                    session.imageNotice.show()
                }
            }
            .confirmationDialog(ImageChoiceButtons.title, isPresented: $askingAboutImages) {
                ImageChoiceButtons(alternatives: app.imageAlternatives(to: session.model)) { choice in
                    session.change(model: choice.model)
                    send()
                } dropImages: {
                    session.draftImages.removeAll(where: Attachments.isImage)
                    send()
                }
            } message: {
                Text(ImageChoiceButtons.message(model: app.label(for: session.model),
                                                alternatives: app.imageAlternatives(to: session.model)))
            }

            ComposerToolbar(session: session)
        }
        .task(id: session.cwd) { imported = await app.importedConfig(in: session.cwd).config }
        .frame(maxWidth: Theme.column)
        .padding(.horizontal, 40).padding(.bottom, 24)
        .frame(maxWidth: .infinity)
    }

    // MARK: - Propiedades privadas

    @EnvironmentObject private var app: AppModel
    @State private var text = ""
    /// Fila elegida del menú de órdenes con «/».
    @State private var selection = 0
    /// Al enviar con imágenes que el modelo no ve: cambiar de modelo o quitarlas.
    @State private var askingAboutImages = false
    /// Lo que trae la carpeta de OpenCode, Codex y Claude Code: sus órdenes «/» salen en el menú.
    @State private var imported: ImportedConfig?

    /// Con un modelo que se sabe que no ve imágenes no se adjuntan; si no se sabe, sí.
    private var allowsImages: Bool { app.allowsImages(session.model) }

    private var attachButton: some View {
        Button { ImagePicker.choose(into: &session.draftImages, withImages: allowsImages) } label: {
            Image(systemName: "plus").foregroundStyle(Theme.secondary)
                .frame(width: 22, height: 22).contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .help(ImageSupport.attachHelp(allowsImages: allowsImages))
        .accessibilityLabel(allowsImages ? "Adjuntar imágenes o archivos" : "Adjuntar archivos")
        .disabled(!session.canSend)
    }

    /// La frase sugerida del último cierre, como en la app de Codex: sale en gris con el cuadro vacío.
    private var suggestion: String? { text.isEmpty ? session.timeline.suggestion : nil }

    /// «Tab» al lado de la sugerencia: así se sabe cómo usarla.
    private var tabHint: some View {
        Text("Tab").font(.system(size: 11, weight: .medium)).foregroundStyle(Theme.muted)
            .padding(.horizontal, 6).padding(.vertical, 2)
            .overlay(RoundedRectangle(cornerRadius: 5).stroke(Theme.border))
            .help("Tab pone la sugerencia en el cuadro para editarla o enviarla")
            .accessibilityHidden(true)
    }

    private var field: some View {
        TextField(suggestion ?? "Añade una indicación", text: $text, axis: .vertical)
            .textFieldStyle(.plain).lineLimit(1...8).font(Theme.body)
            .accessibilityLabel("Indicación para el agente")
            .accessibilityHint(suggestion.map { "Sugerencia: \($0). Tab la escribe en el cuadro." } ?? "")
            // Escribir encima (o aceptarla) la retira hasta el siguiente cierre.
            .onChange(of: text) { _, new in if !new.isEmpty { session.dismissSuggestion() } }
            // Una imagen soltada sobre el texto llega como su ruta: se adjunta en su lugar.
            .onChange(of: text) { old, new in
                guard let dropped = DroppedImages.extract(old: old, new: new) else { return }
                text = dropped.text
                if !allowsImages, dropped.images.contains(where: Attachments.isImage) { session.imageNotice.show() }
                ImagePicker.add(dropped.images, to: &session.draftImages, withImages: allowsImages)
            }
            .onSubmit(submit)
            .onChange(of: text) { selection = 0 }
            // Para revisar la interfaz sin teclear: ALFALFA_BORRADOR rellena el cuadro al abrir.
            .onAppear {
                if let draft = session.pendingDraft {
                    text = ImageSupport.restoredDraft(current: text, returned: draft)
                    session.pendingDraft = nil
                } else if text.isEmpty, let draft = ProcessInfo.processInfo.environment["ALFALFA_BORRADOR"] {
                    text = draft
                }
                // Igual con las imágenes: ALFALFA_ADJUNTOS=ruta:ruta las adjunta al abrir.
                if session.draftImages.isEmpty, let paths = ProcessInfo.processInfo.environment["ALFALFA_ADJUNTOS"] {
                    ImagePicker.add(paths.split(separator: ":").map { URL(fileURLWithPath: String($0)) },
                                    to: &session.draftImages)
                }
            }
            .onChange(of: session.pendingDraft) { _, draft in
                if let draft {
                    text = ImageSupport.restoredDraft(current: text, returned: draft)
                    session.pendingDraft = nil
                }
            }
            .onKeyPress(.upArrow) { move(-1) }
            .onKeyPress(.downArrow) { move(1) }
            .onKeyPress(.tab) {
                if let option = suggestions[safe: selection] {
                    complete(option)
                    return .handled
                }
                // Con el cuadro vacío, Tab acepta la sugerencia: queda escrita, no se envía.
                guard let suggestion else { return .ignored }
                text = suggestion
                return .handled
            }
            .onKeyPress(.escape) {
                guard !suggestions.isEmpty else { return .ignored }
                text = ""
                return .handled
            }
            .disabled(!session.canSend)
    }

    private var stopButton: some View {
        Button { session.stop() } label: {
            RoundedRectangle(cornerRadius: 2).fill(Theme.dark).frame(width: 10, height: 10)
                .frame(width: 30, height: 30)
                .background(Theme.light, in: Circle())
        }
        .buttonStyle(.plain).accessibilityLabel("Detener").help("Detener")
        .disabled(session.stopping)
    }

    /// Turno terminado: el proceso sigue abierto y se puede mandar otra indicación.
    @ViewBuilder private var sendButton: some View {
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

    /// Los modelos de Ollama y, si no está en la lista, el de la sesión.
    private var choices: [String] {
        let names = app.modelChoices
        return names.contains(session.model) ? names : [session.model] + names
    }

    // MARK: - Métodos privados

    private func submit() {
        // ⌘↩ es el atajo de Aprobar: no debe enviar el mensaje.
        if NSApp.currentEvent?.modifierFlags.contains(.command) == true { return }
        if let input = SlashCommand.input(text) {
            if let option = suggestions[safe: selection] {
                choose(option)
                return
            }
            if let command = SlashCommand.named(input.name) {
                run(command, input.argument)
                return
            }
            // «/Users/…» u otra cosa que no es una orden: va como mensaje normal.
        }
        if session.draftImages.contains(where: Attachments.isImage), !allowsImages {
            askingAboutImages = true
            return
        }
        send()
    }

    private func send() {
        session.send(text)
        text = ""
    }

    private func move(_ step: Int) -> KeyPress.Result {
        let count = suggestions.count
        guard count > 0 else { return .ignored }
        selection = (min(selection, count - 1) + step + count) % count
        return .handled
    }
}

// MARK: - Órdenes con «/»

private extension Composer {

    /// Lo que enseña el menú: las órdenes que encajan o, tras `/modelo ` y `/esfuerzo `, sus valores.
    var suggestions: [CommandSuggestion] {
        guard let input = SlashCommand.input(text) else { return [] }
        if let argument = input.argument {
            guard let command = SlashCommand.named(input.name) else { return [] }
            return values(for: command, wanted: argument.trimmingCharacters(in: .whitespaces).lowercased())
        }
        let running = session.timeline.status.isRunning
        return SlashCommand.matching(input.name).map { command in
            let enabled = command.available(running: running, dormant: session.isDormant)
            var detail = command.summary
            if command == .compactar, session.timeline.contextLimit > 0 {
                detail += " (\(Int((session.timeline.contextFraction * 100).rounded())) % usado)"
            }
            if command == .compactar, session.timeline.compacting { detail += " · ya se está compactando" }
            if !enabled { detail += running ? " · espera a que acabe el turno" : " · continúa la sesión primero" }
            return CommandSuggestion(id: command.name, symbol: command.symbol, title: "/" + command.name,
                                     detail: detail, enabled: enabled, action: .command(command))
        } + projectCommands(input.name)
    }

    /// Las órdenes «/» de OpenCode y Claude Code de la carpeta (`alfalfa --importar`).
    func projectCommands(_ prefix: String) -> [CommandSuggestion] {
        (imported?.commands(matching: prefix) ?? []).map { item in
            let authorized = item.state == "autorizado"
            let detail = [item.detail, item.tool + " · " + item.place].filter { !$0.isEmpty }.joined(separator: " · ")
            return CommandSuggestion(id: "proyecto:" + item.name, symbol: "text.badge.star", title: "/" + item.name,
                                     detail: authorized ? detail : detail + " · sin autorizar: Ajustes ▸ Importar",
                                     enabled: authorized, action: .project(item.name))
        }
    }

    /// Los valores que admite una orden con argumento, filtrados por lo ya escrito.
    func values(for command: SlashCommand, wanted: String) -> [CommandSuggestion] {
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
                CommandSuggestion(id: "modo:" + mode.rawValue,
                                  symbol: mode.rawValue == session.mode ? "checkmark" : mode.symbol,
                                  title: mode.title, detail: mode.summary, enabled: mode != .omitir,
                                  action: .argument(.modo, mode.rawValue))
            }
        case .esfuerzo:
            return Presentation.efforts.filter {
                wanted.isEmpty || $0.label.lowercased().hasPrefix(wanted) || $0.value.hasPrefix(wanted)
            }.map { effort in
                let current = effort.value == session.effort
                return CommandSuggestion(id: "esfuerzo:" + effort.value,
                                         symbol: current ? "checkmark" : "gauge.with.dots.needle.50percent",
                                         title: effort.label, detail: current ? "en uso" : "",
                                         enabled: true, action: .argument(.esfuerzo, effort.value))
            }
        default:
            return []
        }
    }

    /// Tab: completa el nombre sin ejecutar (como en Codex).
    func complete(_ option: CommandSuggestion) {
        switch option.action {
        case let .command(command): text = "/" + command.name + (command.takesArgument ? " " : "")
        case let .argument(command, value): text = "/" + command.name + " " + value
        case let .project(name): text = "/" + name + " "
        }
    }

    func choose(_ option: CommandSuggestion) {
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
        case let .project(name):
            // Detrás van sus argumentos; ↩ lo manda como mensaje y el motor pone su plantilla.
            text = "/" + name + " "
        }
    }

    func run(_ command: SlashCommand, _ argument: String?) {
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
            let mode = PermissionMode.allCases.first {
                $0.rawValue == value || $0.title.lowercased() == value.lowercased()
            }
            guard let mode else { text = "/modo "; return }
            // Omitir permisos se activa desde el selector, que pide confirmación.
            guard mode != .omitir else {
                session.addNotice("Activa «Omitir permisos» desde el selector de modo.")
                return
            }
            session.change(mode: mode.rawValue)
        case .compactar: session.compact(instructions: value.isEmpty ? nil : value)
        case .contexto:
            let wanted = value.lowercased()
            let mode = ContextMode.allCases.first {
                $0.rawValue == wanted || $0.label.lowercased() == wanted || (wanted == "full" && $0 == .full)
                    || (wanted == "saving" && $0 == .saving)
            }
            guard let mode else { session.showContextMode(); return }
            session.setContextMode(mode)
        case .procesos: session.showProcesses()
        case .cerrarProcesos: session.stopProcess("todos")
        case .estado: session.showStatus()
        case .revisar, .inicializar: session.send(command.prompt ?? "")
        case .aprender:
            // El motor la expande (alfalfa/aprendizaje): reúne el material y propone la habilidad.
            guard !value.isEmpty else { text = "/aprender "; return }
            session.send("/aprender " + value)
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
            guard let last = session.timeline.lastAgentText else {
                session.addNotice("Aún no hay respuesta que copiar.")
                return
            }
            NSPasteboard.general.clearContents()
            NSPasteboard.general.setString(last, forType: .string)
        case .exportar: export()
        case .detener: session.stop()
        }
    }

    /// Las órdenes sobre el chat necesitan que la sesión ya tenga id en la barra lateral.
    func withRow(_ action: (SidebarRow) -> Void) {
        if let row = app.row(for: session) {
            action(row)
        } else {
            session.addNotice("Esta sesión aún no está guardada.")
        }
    }

    func export() {
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
