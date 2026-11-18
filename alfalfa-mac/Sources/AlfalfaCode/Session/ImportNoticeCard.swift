import AlfalfaKit
import SwiftUI

/// El aviso de lo que trae la carpeta sin autorizar: «5 servidores MCP de Codex sin autorizar», sus nombres y
/// «Revisar y autorizar…», que abre la hoja con una casilla por cada uno. Lo que se autoriza desde aquí no lo carga
/// esta sesión: la tarjeta lo dice y ofrece empezar una sesión nueva en la misma carpeta.
struct ImportNoticeCard: View {

    // MARK: - Propiedades

    let notice: ImportNotice
    /// Lo que ya se autorizó desde el aviso, como se enseña («chiyo», «/probar»).
    let authorized: [String]
    @ObservedObject var session: LiveSession

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Image(systemName: "puzzlepiece.extension").font(.system(size: 13)).foregroundStyle(Theme.warning)
                VStack(alignment: .leading, spacing: 4) {
                    Text(notice.headline).font(.system(size: 13.5, weight: .medium)).foregroundStyle(Theme.text)
                    Text(detail).font(.system(size: 13)).foregroundStyle(Theme.secondary)
                }
                .textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
            }
            HStack(spacing: 8) {
                if authorized.isEmpty {
                    choice("Revisar y autorizar…", primary: true) { reviewing = true }
                } else {
                    choice("Empezar una sesión nueva aquí", primary: true) { app.newChat(in: session.cwd) }
                        .help("Abre «Nuevo encargo» con la carpeta de esta sesión")
                    choice("Revisar y autorizar…") { reviewing = true }
                }
            }
        }
        .padding(16).frame(maxWidth: .infinity, alignment: .leading)
        .overlay(RoundedRectangle(cornerRadius: 14).stroke(Theme.border))
        .accessibilityElement(children: .contain)
        .sheet(isPresented: $reviewing) {
            ImportReviewSheet(folder: session.cwd, notice: notice).environmentObject(app)
        }
    }

    // MARK: - Propiedades privadas

    @EnvironmentObject private var app: AppModel
    @State private var reviewing = false

    private var detail: String {
        if authorized.isEmpty { return notice.names + ". No se activan hasta que los autorices." }
        return "Autorizaste " + ImportNotice.list(authorized) + ". " + ImportReviewSheet.note(inSession: true)
    }

    // MARK: - Métodos privados

    private func choice(_ title: String, primary: Bool = false, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Text(title).font(.system(size: 13, weight: .medium))
                .foregroundStyle(primary ? Theme.dark : Theme.text)
                .padding(.horizontal, 12).padding(.vertical, 7)
                .background(primary ? Theme.light : Theme.selection, in: RoundedRectangle(cornerRadius: 8))
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain).accessibilityLabel(title)
    }
}

/// «Revisar y autorizar…»: lo que `folder` trae sin autorizar, leído otra vez al abrirla, con una casilla por cada
/// uno (marcadas de serie), su definición plegada y el interruptor de internet. Autoriza `clave@huella` de lo marcado
/// con la misma lógica que Ajustes ▸ Importar (`ImportAuthorizer`). La abren el aviso de una sesión (`notice`, que
/// pone delante lo que nombraba), la barra lateral y Archivo ▸ Revisar lo importado sin autorizar… (⇧⌘I).
struct ImportReviewSheet: View {

    // MARK: - Constantes

    /// Por qué lo autorizado no se usa ya: `Importadas` se lee al abrir la sesión y los servidores MCP se conectan
    /// entonces, una vez por proceso.
    static func note(inSession: Bool) -> String {
        (inSession ? "Esta sesión no los usa" : "Las sesiones abiertas en esta carpeta no los usan")
            + ": lo importado se carga al empezar una sesión y los servidores MCP se conectan entonces, una sola vez. "
            + "Para usarlos, empieza una sesión nueva en esta carpeta."
    }

    // MARK: - Propiedades

    let folder: URL
    var notice: ImportNotice?

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Revisar y autorizar").font(.system(size: 16, weight: .semibold))
            Text("Lo que trae " + folder.lastPathComponent + " de OpenCode, Codex o Claude Code. No se activa "
                 + "hasta que lo autorizas y, si su definición cambia, vuelve a preguntar. Ninguna clave de esos "
                 + "archivos se copia.")
                .font(.system(size: 12)).foregroundStyle(Theme.secondary).fixedSize(horizontal: false, vertical: true)
            if let done {
                result(done)
            } else {
                review
            }
            if let error = authorizer.error {
                Text(error).font(.system(size: 12)).foregroundStyle(Theme.danger)
                    .fixedSize(horizontal: false, vertical: true)
            }
            footer
        }
        .padding(20)
        .frame(width: 580)
        .task { await load() }
    }

    // MARK: - Propiedades privadas

    @EnvironmentObject private var app: AppModel
    @Environment(\.dismiss) private var dismiss
    @StateObject private var authorizer = ImportAuthorizer()
    @State private var selected: Set<String> = []
    @State private var loaded = false
    /// Lo que quedó autorizado desde esta hoja; nil mientras se revisa.
    @State private var done: [ImportedItem]?

    private var items: [ImportedItem] {
        authorizer.config.map { ImportReview.items($0, notice: notice) } ?? []
    }

    private var note: String { Self.note(inSession: notice != nil) }

    private var finished: Bool { done != nil || (loaded && items.isEmpty && authorizer.config != nil) }

    @ViewBuilder private var review: some View {
        if !loaded {
            ProgressView().controlSize(.small)
        } else if authorizer.config != nil && items.isEmpty {
            Text("Ya no queda nada sin autorizar en esta carpeta. " + note)
                .font(.system(size: 13)).foregroundStyle(Theme.secondary).fixedSize(horizontal: false, vertical: true)
        } else if !items.isEmpty {
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    ForEach(ImportedItem.Kind.allCases, id: \.self) { kind in
                        let rows = items.filter { $0.kind == kind }
                        if !rows.isEmpty {
                            VStack(alignment: .leading, spacing: 6) {
                                Text(kind.title).font(.system(size: 13, weight: .semibold))
                                ForEach(rows) { row($0) }
                            }
                        }
                    }
                }
            }
            .frame(maxHeight: 400)
            if items.contains(where: { $0.kind == .mcp }) {
                Toggle("Los servidores MCP que autorices tienen internet", isOn: $authorizer.network)
                    .toggleStyle(.checkbox).font(.system(size: 12))
            }
        }
    }

    private var footer: some View {
        HStack(spacing: 10) {
            Spacer()
            if authorizer.busy && loaded { ProgressView().controlSize(.small) }
            if finished {
                Button("Seguir en esta sesión") { dismiss() }.keyboardShortcut(.cancelAction)
                Button("Empezar una sesión nueva aquí") {
                    dismiss()
                    app.newChat(in: folder)
                }
                .keyboardShortcut(.defaultAction)
            } else {
                Button("Cancelar") { dismiss() }.keyboardShortcut(.cancelAction)
                Button(selected.count > 1 ? "Autorizar seleccionados (\(selected.count))" : "Autorizar seleccionados") {
                    Task { await authorize() }
                }
                .keyboardShortcut(.defaultAction)
                .disabled(authorizer.busy || selected.isEmpty)
            }
        }
    }

    // MARK: - Métodos privados

    private func row(_ item: ImportedItem) -> some View {
        HStack(alignment: .top, spacing: 10) {
            Toggle("", isOn: Binding(get: { selected.contains(item.key) },
                                     set: { on in
                                         if on { selected.insert(item.key) } else { selected.remove(item.key) }
                                     }))
                .toggleStyle(.checkbox).labelsHidden()
                .accessibilityLabel("Autorizar " + (item.kind == .comando ? "/" : "") + item.name)
            ImportedItemDetails(item: item, collapsible: true)
            Spacer(minLength: 0)
        }
        .padding(10)
        .background(Theme.field, in: RoundedRectangle(cornerRadius: 8))
    }

    private func result(_ authorized: [ImportedItem]) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Image(systemName: "checkmark.circle").font(.system(size: 13)).foregroundStyle(Theme.alfalfa)
            VStack(alignment: .leading, spacing: 4) {
                Text("Autorizaste " + ImportReview.names(authorized) + ".")
                    .font(.system(size: 13.5, weight: .medium)).foregroundStyle(Theme.text)
                Text(note).font(.system(size: 13)).foregroundStyle(Theme.secondary)
            }
            .fixedSize(horizontal: false, vertical: true)
        }
    }

    /// La lista de la carpeta, recién leída; todo lo pendiente queda marcado.
    private func load() async {
        let config = await authorizer.change(in: folder, using: app)
        selected = Set((config.map { ImportReview.items($0, notice: notice) } ?? []).map(\.key))
        loaded = true
    }

    private func authorize() async {
        let shown = items
        let wanted = Set(shown.map(\.key)).intersection(selected)
        let keys = ImportReview.authorizationKeys(shown, selected: selected)
        guard let fresh = await authorizer.change(in: folder, using: app, authorize: keys) else { return }
        // Lo que cambió entre verlo y pulsar no se autoriza: sale en el error y sigue en la lista.
        selected = selected.intersection(Set(ImportReview.items(fresh, notice: notice).map(\.key)))
        let authorized = ImportReview.authorized(wanted, in: fresh)
        guard !authorized.isEmpty else { return }
        app.importsAuthorized(authorized.map { ($0.kind == .comando ? "/" : "") + $0.name }, in: folder)
        done = authorized
    }
}
