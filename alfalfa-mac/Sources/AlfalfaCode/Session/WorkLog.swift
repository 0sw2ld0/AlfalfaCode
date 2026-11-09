import AlfalfaKit
import SwiftUI

/// Lo que hizo el agente entre mensajes, como en Codex: «Trabajó durante 54 s ›» con una
/// línea debajo; al abrirlo, cada acción en una fila.
struct WorkLog: View {

    // MARK: - Propiedades

    let events: [ToolEvent]
    let running: Bool
    @Binding var expanded: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Button { withAnimation(.easeOut(duration: 0.15)) { expanded.toggle() } } label: {
                HStack(spacing: 6) {
                    TimelineView(.periodic(from: .now, by: 1)) { context in
                        Text(title(now: running ? context.date.timeIntervalSince1970 : nil))
                            .font(.system(size: 14)).foregroundStyle(Theme.muted).monospacedDigit()
                    }
                    Image(systemName: "chevron.right").font(.system(size: 11, weight: .semibold))
                        .foregroundStyle(Theme.muted).rotationEffect(.degrees(expanded ? 90 : 0))
                }
                .padding(.vertical, 6).contentShape(Rectangle())
            }
            .buttonStyle(.plain).accessibilityLabel(title(now: nil))
            Divider().overlay(Theme.border)
            if expanded {
                VStack(alignment: .leading, spacing: 10) {
                    ForEach(events.filter { !$0.text.isEmpty }) { event in
                        EventRow(event: event)
                    }
                }
                .padding(.top, 12)
            }
        }
    }

    // MARK: - Métodos privados

    /// Con `now`, mientras trabaja, el tiempo va hasta ahora y no hasta el último evento.
    private func title(now: Double?) -> String {
        let actions = events.filter { $0.text.hasPrefix("Inicio ") }.count
        let actionsText = "\(actions) \(actions == 1 ? "acción" : "acciones")"
        let count = actions == 0 ? "" : " · " + actionsText
        if let now, let first = events.map(\.time).filter({ $0 > 0 }).min(), now > first {
            return "Trabajando " + ToolEvent.durationText(now - first) + count
        }
        if let seconds = ToolEvent.duration(of: events) {
            return "\(running ? "Trabajando" : "Trabajó durante") \(ToolEvent.durationText(seconds))\(count)"
        }
        if running { return "Trabajando…" }
        return actions == 0 ? "\(events.count) eventos" : actionsText
    }
}

/// Una acción del registro: su título y, al pulsarla, la salida completa.
struct EventRow: View {

    // MARK: - Propiedades

    let event: ToolEvent

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Button { open.toggle() } label: {
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Image(systemName: icon).font(.system(size: 11)).foregroundStyle(Theme.muted).frame(width: 14)
                    Text(event.title).font(.system(size: 13)).foregroundStyle(Theme.secondary).lineLimit(1)
                    Spacer(minLength: 0)
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            if open {
                Text(event.text.count > 4000 ? String(event.text.prefix(4000)) + "…" : event.text)
                    .font(.system(size: 12, design: .monospaced)).foregroundStyle(Theme.secondary)
                    .textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
                    .padding(12).frame(maxWidth: .infinity, alignment: .leading)
                    .background(Theme.field, in: RoundedRectangle(cornerRadius: 10))
            }
        }
    }

    // MARK: - Propiedades privadas

    @State private var open = false

    private var icon: String {
        let title = event.title
        if title.hasPrefix("run_") { return "terminal" }
        if title.hasPrefix("read_file") || title.hasPrefix("list_files") { return "doc.text" }
        if title.hasPrefix("search") || title.hasPrefix("web_search") { return "magnifyingglass" }
        if ["edit_file", "write_file", "apply_patch"].contains(where: title.hasPrefix) { return "pencil" }
        if title.hasPrefix("wrap_up") { return "checkmark.circle" }
        return event.role == "system" ? "info.circle" : "circle"
    }
}
