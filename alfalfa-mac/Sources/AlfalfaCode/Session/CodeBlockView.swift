import AlfalfaKit
import AppKit
import SwiftUI

/// Bloque de código con Copiar y, si es de terminal, Ejecutar: en la terminal de la sesión
/// cuando la hay (como en Claude Code) y si no, en Terminal.app.
struct CodeBlockView: View {

    // MARK: - Constantes

    private static let terminalError = "No se pudo abrir Terminal. Revisa en Ajustes del Sistema → Privacidad → "
        + "Automatización que AlfalfaCode pueda controlarla."

    // MARK: - Propiedades

    let code: String
    let language: String?
    let folder: URL?

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
                    if let runInTerminal {
                        iconButton("play", "Ejecutar en la terminal de la sesión") { runInTerminal(code) }
                            .contextMenu { Button("Ejecutar en Terminal.app") { run(in: folder) } }
                    } else {
                        iconButton("play", "Ejecutar en Terminal") { run(in: folder) }
                    }
                }
                iconButton(copied ? "checkmark" : "doc.on.doc", copied ? "Copiado" : "Copiar") { copy() }
            }
            .padding(6)
        }
        .background(Theme.field, in: RoundedRectangle(cornerRadius: 8))
    }

    // MARK: - Propiedades privadas

    @Environment(\.runInTerminal) private var runInTerminal
    @State private var copied = false
    @State private var error: String?

    // MARK: - Métodos privados

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
        error = info.map { _ in Self.terminalError }
    }
}
