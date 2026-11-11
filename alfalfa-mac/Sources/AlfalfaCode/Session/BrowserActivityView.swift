import AlfalfaKit
import AppKit
import SwiftUI
import UniformTypeIdentifiers

/// Historial de la sesión. La vista no navega, no concede permisos ni toma capturas.
struct BrowserActivityView: View {

    // MARK: - Tipos

    private struct Preview: Identifiable {
        let id: String
        let image: NSImage
    }

    // MARK: - Propiedades

    let store: BrowserActivityStore?

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            header
            if let notice { Text(notice).foregroundStyle(Theme.warning).font(.caption) }
            if log.recortado { Text("Se muestran las acciones más recientes.").font(.caption) }
            Divider()
            history
            Divider()
            footer
        }
        .padding(22)
        .frame(minWidth: 590, idealWidth: 710, minHeight: 420, idealHeight: 600)
        .task(id: store) {
            includeCaptures = false
            preview = nil
            reload()
        }
        .sheet(item: $preview) { item in
            VStack(spacing: 12) {
                Image(nsImage: item.image).resizable().scaledToFit().accessibilityLabel("Captura asociada")
                Button("Cerrar captura") { preview = nil }.keyboardShortcut(.cancelAction)
            }
            .padding(20).frame(width: 650, height: 480)
        }
    }

    // MARK: - Propiedades privadas

    @Environment(\.dismiss) private var dismiss
    @State private var log = BrowserActivityLog.empty
    @State private var notice: String?
    @State private var includeCaptures = false
    @State private var preview: Preview?

    private var header: some View {
        HStack(alignment: .top, spacing: 16) {
            VStack(alignment: .leading, spacing: 5) {
                Text("Qué hizo el agente").font(.title2.bold()).accessibilityAddTraits(.isHeader)
                Text(BrowserActivityLog.note).font(.caption).foregroundStyle(Theme.secondary)
            }
            Spacer()
            Button("Actualizar", action: reload)
            Button("Cerrar") { dismiss() }.keyboardShortcut(.cancelAction)
        }
    }

    private var history: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 12) {
                if log.eventos.isEmpty {
                    Text("Todavía no hay acciones de navegador registradas en esta sesión.")
                        .foregroundStyle(Theme.secondary)
                        .padding(.vertical, 18)
                }
                ForEach(log.eventos.reversed()) { event in
                    BrowserActivityRow(event: event) { showCapture(event) }
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private var footer: some View {
        HStack {
            Toggle("Incluir las capturas asociadas en el informe", isOn: $includeCaptures)
                .disabled(!log.eventos.contains { $0.hasCapture })
                .help("Hasta 12 capturas disponibles y 16 MiB. Pueden contener datos visibles de la página.")
            Spacer()
            Button("Exportar informe…", action: exportReport).disabled(log.eventos.isEmpty)
        }
    }

    // MARK: - Métodos privados

    private func reload() {
        do {
            log = try store?.load() ?? .empty
            notice = nil
        } catch {
            log = .empty
            notice = "El historial de navegador de esta sesión no está disponible."
        }
    }

    private func showCapture(_ event: BrowserActivityEntry) {
        guard let png = store?.capture(for: event), let image = NSImage(data: png) else {
            notice = "La captura asociada ya no está disponible."
            return
        }
        notice = nil
        preview = Preview(id: event.id, image: image)
    }

    private func exportReport() {
        let panel = NSSavePanel()
        panel.allowedContentTypes = [.html]
        panel.nameFieldStringValue = "actividad-navegador.html"
        guard panel.runModal() == .OK, let url = panel.url else { return }
        do {
            let data = BrowserActivityReport.render(log, store: store, includeCaptures: includeCaptures)
            try data.write(to: url, options: .atomic)
            notice = nil
        } catch {
            notice = "No se pudo guardar el informe de actividad."
        }
    }
}

private struct BrowserActivityRow: View {

    // MARK: - Propiedades

    let event: BrowserActivityEntry
    let showCapture: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .firstTextBaseline) {
                Text(event.title).font(.headline)
                Spacer()
                Text(event.resultado.title).font(.caption).foregroundStyle(Theme.secondary)
            }
            let site = event.origin ?? "Sin sitio registrado"
            Text(event.date.formatted(date: .abbreviated, time: .standard) + " · " + event.motor.title + " · " + site)
                .font(.caption).foregroundStyle(Theme.secondary).textSelection(.enabled)
            if event.permisos.isEmpty { Text(event.permissionSummary).font(.caption) }
            ForEach(Array(event.permisos.enumerated()), id: \.offset) { _, permission in
                Text(permission.title + (permission.origin.map { " · " + $0 } ?? "")).font(.caption)
            }
            if let cuy = event.cuy { cuyDetails(cuy) }
            if event.hasCapture { Button("Ver captura asociada", action: showCapture).font(.caption) }
            Divider()
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    // MARK: - Métodos privados

    @ViewBuilder private func cuyDetails(_ cuy: BrowserActivityCuy) -> some View {
        if cuy.acciones.isEmpty {
            if cuy.estado != .disponible { Text(cuy.estado.title).font(.caption).foregroundStyle(Theme.secondary) }
        } else {
            DisclosureGroup(cuy.estado.title) {
                VStack(alignment: .leading, spacing: 5) {
                    ForEach(cuy.acciones) { action in
                        Text(cuyLine(action))
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.vertical, 4)
            }
            .font(.caption)
        }
    }

    private func cuyLine(_ action: BrowserActivityCuy.Action) -> String {
        let time = action.date.formatted(date: .omitted, time: .standard)
        let result = action.ok ? "Completada" : "No completada"
        var parts = [time, action.title]
        if let site = action.origin { parts.append(site) }
        parts.append(result)
        parts.append(action.permission.title)
        return parts.joined(separator: " · ")
    }
}
