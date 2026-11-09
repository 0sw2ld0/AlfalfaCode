import AlfalfaKit
import SwiftUI

/// Ajustes ▸ Importar: lo que trae la carpeta de trabajo de OpenCode, Codex y Claude Code.
/// Los servidores MCP, los agentes y las órdenes «/» no se activan hasta autorizarlos aquí (o con
/// `alfalfa --importar --autorizar`), viendo antes su definición; las instrucciones ya funcionan.
/// Se autoriza `clave@huella`: si el archivo cambió desde que se vio, el motor lo rechaza.
struct ImportSettings: View {

    // MARK: - Propiedades

    @EnvironmentObject private var model: AppModel

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            SettingsTitle(text: "Importar")
            SettingsBlock(title: "OpenCode, Codex y Claude Code",
                          detail: "AlfalfaCode lee AGENTS.md y CLAUDE.md, opencode.json, .opencode/, "
                              + ".codex/config.toml y .mcp.json de la carpeta de trabajo, y tus configuraciones de "
                              + "~/.config/opencode y ~/.codex/config.toml. Los servidores MCP, los agentes y las "
                              + "órdenes «/» que vienen de ahí no se activan hasta que los autorizas una vez, viendo "
                              + "qué hacen; si su definición cambia, vuelven a preguntar. Ninguna clave de esos "
                              + "archivos se copia.") {
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
        .confirmationDialog(confirmTitle, isPresented: $confirmingAll) {
            Button("Autorizar " + String(config?.pending.count ?? 0)) {
                Task { await change(authorize: (config?.pending ?? []).map(\.authorizationKey)) }
            }
            Button("Cancelar", role: .cancel) {}
        } message: {
            Text("Cada uno es código o instrucciones que trae la carpeta: revisa antes su definición en la lista. "
                 + (network ? "Los servidores MCP tendrán internet." : "Los servidores MCP no tendrán internet."))
        }
    }

    // MARK: - Propiedades privadas

    @State private var config: ImportedConfig?
    @State private var error: String?
    @State private var busy = false
    @State private var network = true
    @State private var confirmingAll = false

    private var confirmTitle: String {
        let names = (config?.pending ?? []).map { ($0.kind == .comando ? "/" : "") + $0.name }
        return "¿Autorizar " + names.joined(separator: ", ") + "?"
    }

    private var folder: URL? { model.folder.map { URL(fileURLWithPath: $0) } }

    private var header: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(folder.map { "Carpeta: " + $0.path } ?? "Elige una carpeta en «Nuevo encargo» para ver qué trae.")
                .font(.system(size: 12, design: .monospaced)).foregroundStyle(Theme.secondary)
                .lineLimit(1).truncationMode(.middle)
            HStack(spacing: 10) {
                Button("Autorizar todo…") { confirmingAll = true }
                    .disabled(busy || (config?.pending.isEmpty ?? true))
                Button("Volver a leer") { Task { await load() } }.disabled(busy || folder == nil)
                Toggle("Los servidores MCP que autorices tienen internet", isOn: $network)
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
                ForEach(item.definition, id: \.self) { line in
                    Text(line).font(.system(size: 11, design: .monospaced)).textSelection(.enabled)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if let preview = item.preview {
                    Text(preview).font(.system(size: 11, design: .monospaced)).foregroundStyle(Theme.secondary)
                        .textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
                        .padding(6).background(Theme.background, in: RoundedRectangle(cornerRadius: 6))
                }
            }
            Spacer()
            if item.needsAuthorization {
                Button("Autorizar") { Task { await change(authorize: [item.authorizationKey]) } }.disabled(busy)
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
