import AlfalfaKit
import SwiftUI

/// Ajustes ▸ Importar: lo que trae la carpeta de trabajo de OpenCode, Codex y Claude Code.
/// Los servidores MCP y los agentes no se activan hasta autorizarlos aquí (o con
/// `alfalfa --importar --autorizar`); las órdenes «/» y las instrucciones ya funcionan.
struct ImportSettings: View {

    // MARK: - Propiedades

    @EnvironmentObject private var model: AppModel

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            SettingsTitle(text: "Importar")
            SettingsBlock(title: "OpenCode, Codex y Claude Code",
                          detail: "AlfalfaCode lee AGENTS.md y CLAUDE.md, opencode.json, .opencode/, "
                              + ".codex/config.toml y .mcp.json de la carpeta de trabajo, y tus configuraciones de "
                              + "~/.config/opencode y ~/.codex/config.toml. Los servidores MCP y los agentes que "
                              + "vienen de ahí no se activan hasta que los autorizas una vez; si su definición cambia, "
                              + "vuelven a preguntar. Ninguna clave de esos archivos se copia.") {
                header
            }
            if let error {
                Text(error).font(.system(size: 12)).foregroundStyle(Theme.danger)
            }
            if let config {
                content(config)
            }
        }
        .task(id: model.folder) { await load() }
    }

    // MARK: - Propiedades privadas

    @State private var config: ImportedConfig?
    @State private var error: String?
    @State private var busy = false
    @State private var network = true

    private var folder: URL? { model.folder.map { URL(fileURLWithPath: $0) } }

    private var header: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(folder.map { "Carpeta: " + $0.path } ?? "Elige una carpeta en «Nuevo encargo» para ver qué trae.")
                .font(.system(size: 12, design: .monospaced)).foregroundStyle(Theme.secondary)
                .lineLimit(1).truncationMode(.middle)
            HStack(spacing: 10) {
                Button("Autorizar todo") { Task { await change(authorize: (config?.pending ?? []).map(\.key)) } }
                    .disabled(busy || (config?.pending.isEmpty ?? true))
                Button("Volver a leer") { Task { await load() } }.disabled(busy || folder == nil)
                Toggle("Los servidores MCP que autorices tienen red", isOn: $network)
                    .toggleStyle(.checkbox).font(.system(size: 12))
                if busy { ProgressView().controlSize(.small) }
            }
        }
    }

    private func content(_ config: ImportedConfig) -> some View {
        VStack(alignment: .leading, spacing: 16) {
            if config.items.isEmpty {
                Text("Esta carpeta no trae nada de OpenCode, Codex ni Claude Code.")
                    .font(.system(size: 12)).foregroundStyle(Theme.secondary)
            }
            ForEach(ImportedItem.Kind.allCases, id: \.self) { kind in
                let items = config.items(of: kind)
                if !items.isEmpty {
                    VStack(alignment: .leading, spacing: 6) {
                        Text(kind.title).font(.system(size: 13, weight: .semibold))
                        ForEach(items) { row($0) }
                    }
                }
            }
            ForEach(config.warnings, id: \.self) { warning in
                Text(warning).font(.system(size: 11)).foregroundStyle(Theme.secondary)
            }
        }
    }

    private func row(_ item: ImportedItem) -> some View {
        HStack(alignment: .top, spacing: 10) {
            VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: 8) {
                    Text((item.kind == .comando ? "/" : "") + item.name).font(.system(size: 13, design: .monospaced))
                    Text(item.stateTitle).font(.system(size: 11, weight: .medium))
                        .foregroundStyle(item.needsAuthorization || item.state == "invalido" ? Theme.warning
                                         : Theme.secondary)
                }
                Text(item.tool + " · " + item.place).font(.system(size: 11)).foregroundStyle(Theme.secondary)
                ForEach(notes(item), id: \.self) { note in
                    Text(note).font(.system(size: 11)).foregroundStyle(Theme.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            Spacer()
            if item.needsAuthorization {
                Button("Autorizar") { Task { await change(authorize: [item.key]) } }.disabled(busy)
            } else if item.authorizable, item.state == "autorizado" {
                Button("Retirar") { Task { await change(revoke: [item.key]) } }.disabled(busy)
            }
        }
        .padding(10)
        .background(Theme.field, in: RoundedRectangle(cornerRadius: 8))
    }

    private func notes(_ item: ImportedItem) -> [String] {
        var lines = [item.detail, item.problem ?? ""] + item.warnings
        if let coveredBy = item.coveredBy { lines.append("Lo tapa " + coveredBy) }
        return lines.filter { !$0.isEmpty }
    }

    // MARK: - Métodos privados

    private func load() async {
        await change()
    }

    private func change(authorize: [String] = [], revoke: [String] = []) async {
        guard let folder else {
            config = nil
            return
        }
        busy = true
        let result = await model.importedConfig(in: folder, authorize: authorize, revoke: revoke, network: network)
        busy = false
        if let fresh = result.config { config = fresh }
        error = result.error
    }
}
