import AlfalfaKit
import SwiftUI

/// Leer y cambiar lo que una carpeta trae de OpenCode, Codex y Claude Code (`alfalfa --importar --json`). Lo usan
/// Ajustes ▸ Importar y la hoja «Revisar y autorizar…» del aviso de una sesión: los dos autorizan `clave@huella` de
/// lo que enseñan, con el mismo interruptor de internet.
@MainActor
final class ImportAuthorizer: ObservableObject {

    // MARK: - Propiedades

    @Published private(set) var config: ImportedConfig?
    @Published private(set) var error: String?
    @Published private(set) var busy = false
    /// Los servidores MCP que se autoricen tienen internet (encendido de serie, como en `alfalfa --importar`).
    @Published var network = true

    // MARK: - Métodos

    /// Vuelve a leer la lista de `folder`; con `authorize` o `revoke` (claves completas, `clave@huella` para
    /// autorizar) cambia antes esas autorizaciones. Devuelve la lista nueva, o nil si no se pudo leer.
    @discardableResult
    func change(in folder: URL?, using model: AppModel, authorize: [String] = [],
                revoke: [String] = []) async -> ImportedConfig? {
        guard let folder else {
            config = nil
            return nil
        }
        busy = true
        let result = await model.importedConfig(in: folder, authorize: authorize, revoke: revoke, network: network)
        busy = false
        if let fresh = result.config { config = fresh }
        error = result.error
        return result.config
    }
}

/// Lo que se ve de un elemento importado antes de autorizarlo: nombre y estado, de dónde viene, sus notas y su
/// definición (las líneas del servidor MCP o el principio del texto de la orden o el agente). En la hoja del aviso
/// la definición va plegada para que la lista se lea de un vistazo.
struct ImportedItemDetails: View {
    let item: ImportedItem
    var collapsible = false

    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            HStack(spacing: 8) {
                Text((item.kind == .comando ? "/" : "") + item.name).font(.system(size: 13, design: .monospaced))
                Text(item.stateTitle).font(.system(size: 11, weight: .medium))
                    .foregroundStyle(item.needsAuthorization || item.state == "invalido" ? Theme.warning
                                     : Theme.secondary)
            }
            Text(item.tool + " · " + item.place).font(.system(size: 11)).foregroundStyle(Theme.secondary)
            ForEach(notes, id: \.self) { note in
                Text(note).font(.system(size: 11)).foregroundStyle(Theme.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if collapsible && hasDefinition {
                DisclosureGroup(isExpanded: $showsDefinition) {
                    definition.padding(.top, 4)
                } label: {
                    Text("Definición").font(.system(size: 11, weight: .medium)).foregroundStyle(Theme.secondary)
                }
                .accessibilityHint("Ver qué ejecuta o qué instrucciones lleva")
            } else {
                definition
            }
        }
    }

    // MARK: - Propiedades privadas

    @State private var showsDefinition = false

    private var hasDefinition: Bool { !item.definition.isEmpty || item.preview != nil }

    private var notes: [String] {
        var lines = [item.detail, item.problem ?? ""] + item.warnings
        if let coveredBy = item.coveredBy { lines.append("Lo tapa " + coveredBy) }
        return lines.filter { !$0.isEmpty }
    }

    @ViewBuilder private var definition: some View {
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
}
