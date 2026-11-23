import AlfalfaKit
import AppKit
import SwiftUI

struct ClaudeCodeSheet: View {
    @Environment(\.dismiss) private var dismiss
    @State private var executable = ClaudeCodeLaunch.executable()
    @State private var folder: URL?
    @State private var model: ClaudeCodeLaunch.Model?
    @State private var effort: ClaudeCodeLaunch.Effort?
    @State private var errorMessage: String?
    @State private var opening = false

    init(folder: URL?) {
        _folder = State(initialValue: folder)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("Claude Code en Terminal").font(.headline)
            Text("Elige la carpeta, el modelo y el esfuerzo. Escribe el encargo en la sesión interactiva de Claude Code.")
                .foregroundStyle(Theme.secondary)

            HStack {
                Text(executable?.path ?? "No se encontró Claude Code")
                    .lineLimit(1).truncationMode(.middle)
                Spacer()
                Button("Elegir ejecutable…") { chooseExecutable() }
            }
            HStack {
                Label(folder?.path ?? "Elige una carpeta", systemImage: "folder")
                    .lineLimit(1).truncationMode(.middle)
                Spacer()
                Button("Elegir carpeta…") { chooseFolder() }
            }
            Picker("Modelo", selection: $model) {
                Text("Predeterminado de Claude Code").tag(nil as ClaudeCodeLaunch.Model?)
                Text("Haiku").tag(Optional(ClaudeCodeLaunch.Model.haiku))
                Text("Sonnet").tag(Optional(ClaudeCodeLaunch.Model.sonnet))
                Text("Opus").tag(Optional(ClaudeCodeLaunch.Model.opus))
            }
            Picker("Esfuerzo", selection: $effort) {
                Text("Predeterminado del modelo").tag(nil as ClaudeCodeLaunch.Effort?)
                Text("Bajo").tag(Optional(ClaudeCodeLaunch.Effort.low))
                Text("Medio").tag(Optional(ClaudeCodeLaunch.Effort.medium))
                Text("Alto").tag(Optional(ClaudeCodeLaunch.Effort.high))
                Text("Muy alto").tag(Optional(ClaudeCodeLaunch.Effort.xhigh))
                Text("Máximo").tag(Optional(ClaudeCodeLaunch.Effort.max))
            }
            Text("Esta sesión omite los MCP personales y del proyecto y desactiva Chrome. " +
                 "Claude Code gestiona la cuenta, el consumo y los permisos.")
                .font(.callout).foregroundStyle(Theme.secondary)
            if let errorMessage {
                Text(errorMessage).font(.callout).foregroundStyle(Theme.danger)
            }

            HStack {
                if let executable {
                    Button("Iniciar sesión o renovar…") {
                        openInTerminal(ClaudeCodeLaunch.signIn(executable: executable))
                    }
                    .disabled(opening)
                } else if let setup = URL(string: "https://code.claude.com/docs/en/setup") {
                    Link("Instalar Claude Code", destination: setup)
                }
                Spacer()
                Button("Cerrar", role: .cancel) { dismiss() }.keyboardShortcut(.cancelAction)
                Button("Abrir Terminal") {
                    guard let executable, let folder else { return }
                    openInTerminal(ClaudeCodeLaunch.session(
                        executable: executable, folder: folder, model: model, effort: effort
                    ))
                }
                .buttonStyle(.borderedProminent)
                .disabled(executable == nil || folder == nil || opening)
            }
        }
        .padding(24)
        .frame(width: 570)
    }

    private func chooseExecutable() {
        let panel = NSOpenPanel()
        panel.message = "Elige la CLI de Claude Code instalada en este Mac."
        if panel.runModal() == .OK, let url = panel.url {
            guard FileManager.default.isExecutableFile(atPath: url.path) else {
                errorMessage = "El archivo elegido no es ejecutable."
                return
            }
            executable = url
            errorMessage = nil
        }
    }

    private func chooseFolder() {
        let panel = NSOpenPanel()
        panel.canChooseFiles = false
        panel.canChooseDirectories = true
        panel.directoryURL = folder
        if panel.runModal() == .OK { folder = panel.url }
    }

    private func openInTerminal(_ content: String) {
        guard !opening else { return }
        let files = FileManager.default
        let script = files.temporaryDirectory.appendingPathComponent("Claude Code \(UUID().uuidString).command")
        do {
            try Data(content.utf8).write(to: script, options: .withoutOverwriting)
            try files.setAttributes([.posixPermissions: 0o700], ofItemAtPath: script.path)
        } catch {
            try? files.removeItem(at: script)
            errorMessage = "No se pudo preparar el comando para Terminal."
            return
        }

        errorMessage = nil
        opening = true
        let terminal = URL(fileURLWithPath: "/System/Applications/Utilities/Terminal.app")
        NSWorkspace.shared.open([script], withApplicationAt: terminal, configuration: .init()) { _, error in
            let failed = error != nil
            if failed { try? files.removeItem(at: script) }
            Task { @MainActor in
                opening = false
                if failed { errorMessage = "Terminal no pudo abrir la sesión de Claude Code." }
            }
        }
    }
}
