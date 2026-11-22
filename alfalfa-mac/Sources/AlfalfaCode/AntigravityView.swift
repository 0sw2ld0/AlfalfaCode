import AlfalfaKit
import AppKit
import SwiftUI
import UniformTypeIdentifiers

struct AntigravityView: View {
    @EnvironmentObject private var model: AppModel
    @ObservedObject var session: AntigravitySession
    @State private var draft = ""
    @State private var executable = AntigravitySession.executable()
    @State private var servers: [AntigravityMCPServer] = []
    @State private var selectedServers: Set<String> = []
    @State private var delegationModel: String?
    @State private var importError: String?
    @State private var confirmDisconnect = false

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            header.padding(.horizontal, 24).padding(.top, 36).padding(.bottom, 14)
            Divider()
            if session.state == .disconnected {
                connectionSettings
            } else {
                conversation
                if let approval = session.approvals.first { permission(approval) }
                composer
            }
            if let error = session.error {
                Text(error).font(.callout).foregroundStyle(Theme.danger)
                    .padding(.horizontal, 24).padding(.vertical, 10)
                    .textSelection(.enabled)
            }
        }
        .background(Theme.background)
        .confirmationDialog("¿Cerrar la sesión de Antigravity?", isPresented: $confirmDisconnect) {
            Button("Cerrar sesión local", role: .destructive) { session.disconnect() }
            Button("Cancelar", role: .cancel) {}
        } message: {
            Text("Se detendrá el proceso que abrió AlfalfaCode. La cuenta de Google seguirá conectada en Antigravity CLI.")
        }
    }

    private var header: some View {
        HStack(alignment: .center) {
            VStack(alignment: .leading, spacing: 4) {
                Text("Antigravity").font(.headline)
                if let directory = session.directory, session.state != .disconnected {
                    Text(directory.path).font(.caption).foregroundStyle(Theme.secondary).lineLimit(1)
                }
            }
            Spacer()
            if let usage = session.usage {
                Text("\(usage.used.formatted()) / \(usage.size.formatted()) tokens")
                    .font(.caption.monospacedDigit()).foregroundStyle(Theme.secondary)
                    .help("Uso de contexto informado por Antigravity")
            }
            if session.state != .disconnected {
                Button("Cerrar sesión…") { confirmDisconnect = true }
            }
        }
    }

    private var connectionSettings: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                Text("Abre una sesión con Antigravity CLI en la carpeta elegida.")
                    .foregroundStyle(Theme.secondary)
                HStack {
                    if let folder = model.folder {
                        Label(folder, systemImage: "folder").lineLimit(1).truncationMode(.middle)
                    }
                    Button("Elegir carpeta…") { model.chooseFolder() }
                }
                HStack {
                    Text(executable?.path ?? "No se encontró agy").lineLimit(1).truncationMode(.middle)
                        .foregroundStyle(Theme.secondary)
                    Button("Elegir ejecutable agy…") { chooseExecutable() }
                }
                Text("La cuenta, el sandbox y las herramientas de esta sesión se configuran en Antigravity.")
                    .font(.callout).foregroundStyle(Theme.secondary)
                HStack {
                    Button("Importar servidores MCP…") { chooseMCP() }
                    if let documentation = URL(string: "https://antigravity.google/docs/cli/acp") {
                        Link("Instalación e inicio de sesión", destination: documentation)
                    }
                }
                if let importError {
                    Text(importError).font(.callout).foregroundStyle(Theme.danger)
                }
                ForEach(servers) { server in
                    Toggle(isOn: Binding(get: { selectedServers.contains(server.id) }, set: { enabled in
                        if enabled { selectedServers.insert(server.id) } else { selectedServers.remove(server.id) }
                    })) {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(server.id)
                            Text(verbatim: server.summary).font(.caption.monospaced())
                                .foregroundStyle(Theme.secondary).textSelection(.enabled)
                        }
                    }
                    .toggleStyle(.checkbox)
                }
                if model.launcher != nil {
                    Picker("Delegar a un modelo de AlfalfaCode", selection: $delegationModel) {
                        Text("Desactivado").tag(nil as String?)
                        ForEach(model.models.filter { !$0.name.lowercased().contains("embed") }) { item in
                            Text(item.label).tag(Optional(item.name))
                        }
                    }
                    if delegationModel != nil {
                        Text("Antigravity puede enviar hasta tres consultas de texto al modelo elegido. "
                             + "Se aplican la cuota o los cargos del proveedor.")
                            .font(.callout).foregroundStyle(Theme.secondary)
                    }
                }
                Button("Conectar") {
                    guard let executable, let folder = model.folder else { return }
                    var enabled = servers.filter { selectedServers.contains($0.id) }
                    if let launcher = model.launcher, let delegationModel {
                        enabled.removeAll { $0.id == "alfalfa-delegation" }
                        enabled.append(.delegation(launcher: launcher, model: delegationModel))
                    }
                    session.connect(executable: executable, directory: URL(fileURLWithPath: folder), servers: enabled)
                }
                .buttonStyle(.borderedProminent)
                .disabled(executable == nil || model.folder == nil)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(24)
        }
    }

    private var conversation: some View {
        ScrollViewReader { reader in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 16) {
                    ForEach(session.entries) { entry in
                        VStack(alignment: .leading, spacing: 4) {
                            Text(label(for: entry)).font(.caption.weight(.semibold)).foregroundStyle(Theme.secondary)
                            Text(verbatim: entry.text).textSelection(.enabled)
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(12)
                        .background(entry.kind == .user ? Theme.selection : Theme.card,
                                    in: RoundedRectangle(cornerRadius: 8))
                    }
                    if session.state == .connecting {
                        ProgressView("Conectando con Antigravity…")
                            .controlSize(.small)
                    } else if session.state == .signInRequired {
                        Button("Iniciar sesión con Google") { session.signIn() }.buttonStyle(.borderedProminent)
                    } else if session.state == .signingIn {
                        ProgressView("Esperando la autorización en Google…")
                            .controlSize(.small)
                    } else if session.state == .cancelling {
                        ProgressView("Cancelando…")
                            .controlSize(.small)
                    }
                    if session.state == .running && session.approvals.isEmpty {
                        ProgressView("Antigravity está trabajando…").controlSize(.small)
                    }
                    Color.clear.frame(height: 1).id("antigravity-end")
                }
                .padding(24)
            }
            .onChange(of: session.entries.last) { _, _ in reader.scrollTo("antigravity-end", anchor: .bottom) }
        }
    }

    private func label(for entry: AntigravitySession.Entry) -> String {
        switch entry.kind {
        case .user: return "Tú"
        case .response, .notice: return "Antigravity"
        case .tool:
            switch entry.status {
            case "pending": return "Herramienta · pendiente"
            case "in_progress": return "Herramienta · en curso"
            case "completed": return "Herramienta · completada"
            case "failed": return "Herramienta · falló"
            default: return "Herramienta"
            }
        }
    }

    private func permission(_ approval: AntigravitySession.Approval) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Label("Antigravity solicita permiso", systemImage: "hand.raised").font(.headline)
            Text(verbatim: approval.title).textSelection(.enabled)
            if let details = approval.details, !details.isEmpty {
                ScrollView {
                    Text(verbatim: details).font(.system(.caption, design: .monospaced))
                        .frame(maxWidth: .infinity, alignment: .leading).textSelection(.enabled)
                }
                .frame(maxHeight: 130)
            }
            HStack {
                Button("Denegar") { session.respond(to: approval, allow: false) }
                if approval.canAllowOnce {
                    Button("Permitir una vez") { session.respond(to: approval, allow: true) }
                }
            }
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Theme.card, in: RoundedRectangle(cornerRadius: 8))
        .padding(.horizontal, 24)
    }

    private var composer: some View {
        HStack(spacing: 10) {
            TextField("Encargo para Antigravity", text: $draft, axis: .vertical)
                .lineLimit(1...8)
                .textFieldStyle(.roundedBorder)
                .disabled(session.state == .connecting)
            if session.state == .running {
                Button("Detener") { session.cancelTurn() }
            } else {
                Button("Enviar") {
                    session.send(draft)
                    if session.state == .running { draft = "" }
                }
                .buttonStyle(.borderedProminent)
                .keyboardShortcut(.return, modifiers: [.command])
                .disabled(session.state != .ready || draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
        }
        .padding(24)
    }

    private func chooseExecutable() {
        let panel = NSOpenPanel()
        panel.title = "Elegir el ejecutable de Antigravity CLI"
        if panel.runModal() == .OK, let url = panel.url {
            guard url.lastPathComponent == "agy" && FileManager.default.isExecutableFile(atPath: url.path) else {
                importError = "Selecciona un ejecutable llamado agy."
                return
            }
            executable = url
            importError = nil
        }
    }

    private func chooseMCP() {
        let panel = NSOpenPanel()
        panel.title = "Elegir el mcp.json de AlfalfaCode"
        panel.allowedContentTypes = [.json]
        guard panel.runModal() == .OK, let url = panel.url else { return }
        do {
            let file = try FileHandle(forReadingFrom: url)
            defer { try? file.close() }
            servers = try AntigravityMCPServer.portableServers(in: file.read(upToCount: 256 * 1024 + 1) ?? Data())
            selectedServers = []
            importError = servers.isEmpty ? "El archivo no contiene servidores MCP portables sin credenciales ni reglas propias." : nil
        } catch {
            importError = "No se pudo leer la configuración MCP."
        }
    }
}
