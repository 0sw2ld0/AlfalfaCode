import AlfalfaKit
import SwiftUI

/// Ajustes ▸ Almacenamiento: lo que ocupa cada sesión guardada, para liberar disco.
///
/// Se eligen a mano o con «Sin usar hace más de N días» y van a la Papelera de macOS. Las que están
/// en uso o abiertas en la app no se pueden elegir (y el motor las salta igual). El motor también
/// las quita del índice de búsqueda y borra sus instantáneas de deshacer.
struct StorageSettings: View {

    // MARK: - Propiedades

    private static let bytes: ByteCountFormatter = {
        let format = ByteCountFormatter()
        format.countStyle = .file
        return format
    }()

    @EnvironmentObject private var model: AppModel

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            SettingsTitle(text: "Almacenamiento")
            if let error {
                Text(error).font(.system(size: 12)).foregroundStyle(Theme.danger)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if let notice {
                Text(notice).font(.system(size: 12)).foregroundStyle(Theme.alfalfa)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if let storage {
                totals(storage)
                byAge(storage)
                list(storage)
            } else if error == nil {
                ProgressView().controlSize(.small)
            }
        }
        .task { await load() }
        .alert(confirmTitle, isPresented: $confirming) {
            Button("Mover a la Papelera", role: .destructive) { Task { await free() } }
            Button("Cancelar", role: .cancel) {}
        } message: {
            Text(storage?.notice ?? "")
        }
    }

    // MARK: - Propiedades privadas

    @State private var storage: SessionStorage?
    @State private var selected: Set<String> = []
    @State private var order = SessionStorage.Order.size
    @State private var days = 30
    @State private var error: String?
    @State private var notice: String?
    @State private var busy = false
    @State private var confirming = false

    private var confirmTitle: String {
        let count = selected.count
        let what = count == 1 ? "la sesión elegida" : "las \(count) sesiones elegidas"
        return "¿Mover " + what + " a la Papelera (" + size(storage?.bytes(of: selected) ?? 0) + ")?"
    }

    private func totals(_ storage: SessionStorage) -> some View {
        SettingsBlock(title: "Sesiones guardadas",
                      detail: "Cada conversación guarda su historial, las fotos del espacio de trabajo y las imágenes "
                      + "que adjuntaste o generaste; las de imágenes son las que más ocupan.") {
            Text("\(storage.sessions.count) sesiones: \(size(storage.sessionsBytes)) · índice de búsqueda: "
                 + "\(size(storage.indexBytes)) · instantáneas de deshacer: \(size(storage.snapshotsBytes))")
                .font(.system(size: 12)).foregroundStyle(Theme.secondary)
        }
    }

    private func byAge(_ storage: SessionStorage) -> some View {
        SettingsBlock(title: "Liberar por antigüedad",
                      detail: "Elige las que llevan tiempo sin usarse. Las fijadas, las abiertas y las que están "
                      + "trabajando no entran.") {
            HStack(spacing: 10) {
                Stepper("Sin usar hace más de \(days) días", value: $days, in: 1...365, step: days < 30 ? 1 : 15)
                Button("Elegirlas") {
                    selected = storage.unused(moreThan: days, open: model.openSessionIDs)
                    notice = selected.isEmpty ? "No hay sesiones sin usar hace más de \(days) días." : nil
                }
                .disabled(busy)
            }
        }
    }

    private func list(_ storage: SessionStorage) -> some View {
        let open = model.openSessionIDs
        return VStack(alignment: .leading, spacing: 8) {
            HStack {
                Picker("Ordenar", selection: $order) {
                    Text("Por tamaño").tag(SessionStorage.Order.size)
                    Text("Por último uso").tag(SessionStorage.Order.lastUse)
                }
                .pickerStyle(.segmented).frame(width: 260).labelsHidden()
                Spacer()
                let chosen = "\(selected.count) elegidas · " + size(storage.bytes(of: selected))
                Text(selected.isEmpty ? "Ninguna elegida" : chosen)
                    .font(.system(size: 12)).foregroundStyle(Theme.secondary)
                Button("Mover a la Papelera…", role: .destructive) { confirming = true }
                    .disabled(busy || selected.isEmpty)
            }
            VStack(spacing: 0) {
                ForEach(storage.sorted(by: order)) { session in
                    row(session, freeable: storage.freeable(session, open: open), open: open.contains(session.id))
                    Divider().overlay(Theme.border)
                }
            }
            .background(Theme.card, in: RoundedRectangle(cornerRadius: 8))
        }
    }

    private func row(_ session: StoredSessionUsage, freeable: Bool, open: Bool) -> some View {
        HStack(alignment: .top, spacing: 10) {
            Toggle(isOn: Binding(get: { selected.contains(session.id) }, set: { on in
                if on { selected.insert(session.id) } else { selected.remove(session.id) }
            })) { EmptyView() }
                .toggleStyle(.checkbox).labelsHidden().disabled(!freeable || busy)
                .accessibilityLabel("Elegir " + session.displayTitle)
            VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: 6) {
                    Text(session.displayTitle).font(.system(size: 13, weight: .medium)).lineLimit(1)
                    if session.pinned { badge("Fijada", Theme.muted) }
                    if open { badge("Abierta", Theme.warning) } else if session.inUse { badge("En uso", Theme.warning) }
                }
                Text(session.cwd.map(Presentation.abbreviate) ?? "Sin carpeta")
                    .font(.system(size: 11, design: .monospaced)).foregroundStyle(Theme.muted)
                    .lineLimit(1).truncationMode(.middle)
            }
            Spacer()
            VStack(alignment: .trailing, spacing: 3) {
                Text(size(session.bytes)).font(.system(size: 12, design: .monospaced))
                Text(lastUse(session)).font(.system(size: 11)).foregroundStyle(Theme.muted)
            }
        }
        .padding(.horizontal, 10).padding(.vertical, 8)
        .opacity(freeable ? 1 : 0.6)
    }

    private func badge(_ text: String, _ color: Color) -> some View {
        Text(text).font(.system(size: 10, weight: .semibold)).foregroundStyle(color)
            .padding(.horizontal, 5).padding(.vertical, 1)
            .overlay(RoundedRectangle(cornerRadius: 4).stroke(color.opacity(0.6)))
    }

    private func lastUse(_ session: StoredSessionUsage) -> String {
        switch session.daysUnused {
        case nil: return "—"
        case 0?: return "Hoy"
        case 1?: return "Ayer"
        case let days?: return "Hace \(days) días"
        }
    }

    private func size(_ value: Int64) -> String { Self.bytes.string(fromByteCount: value) }

    private func load() async {
        let (storage, error) = await model.sessionStorage()
        self.storage = storage
        self.error = error
        // Lo elegido que ya no está (o que ahora está abierto) se suelta.
        if let storage {
            let open = model.openSessionIDs
            let freeable = Set(storage.sessions.filter { storage.freeable($0, open: open) }.map(\.id))
            selected = selected.intersection(freeable)
        }
    }

    private func free() async {
        busy = true
        defer { busy = false }
        let (freed, error) = await model.freeSessions(selected)
        selected = []
        notice = freed?.summary
        await load()
        if let error { self.error = error }
    }
}
