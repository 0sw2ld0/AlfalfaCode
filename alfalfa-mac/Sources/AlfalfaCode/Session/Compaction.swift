import AlfalfaKit
import SwiftUI

/// «Conversación compactada», como en Claude Code: una línea con antes → después y el
/// resumen que ahora lee el modelo, plegado.
struct CompactionMarker: View {

    // MARK: - Propiedades

    let compaction: Compaction

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Button { withAnimation(.easeOut(duration: 0.15)) { open.toggle() } } label: {
                HStack(spacing: 10) {
                    line
                    HStack(spacing: 6) {
                        Image(systemName: "arrow.down.right.and.arrow.up.left").font(.system(size: 11))
                        Text(title).font(.system(size: 12.5, weight: .medium))
                        Image(systemName: "chevron.down").font(.system(size: 9, weight: .bold))
                            .rotationEffect(.degrees(open ? 0 : -90))
                    }
                    .foregroundStyle(Theme.secondary).fixedSize()
                    line
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .help(open ? "Ocultar el resumen" : "Ver el resumen que ahora lee el modelo")
            .accessibilityLabel(title).accessibilityValue(open ? "resumen abierto" : "resumen plegado")
            if open { summary }
        }
    }

    // MARK: - Propiedades privadas

    @State private var open = false

    private var line: some View { Rectangle().fill(Theme.border).frame(height: 1) }

    private var title: String {
        let kind = compaction.reason == "manual" ? "Conversación compactada" : "Compactada automáticamente"
        guard compaction.before > 0 else { return kind }
        return kind + " · " + ContextFormat.tokens(compaction.before) + " → " + ContextFormat.tokens(compaction.after)
    }

    private var summary: some View {
        VStack(alignment: .leading, spacing: 8) {
            if let instructions = compaction.instructions {
                Text("Pediste: " + instructions).font(.system(size: 12.5)).foregroundStyle(Theme.muted)
            }
            Text(compaction.summary).font(.system(size: 13)).foregroundStyle(Theme.secondary)
                .textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
            Text("El historial completo sigue guardado; el modelo sigue desde este resumen.")
                .font(.system(size: 11.5)).foregroundStyle(Theme.muted)
        }
        .padding(14).frame(maxWidth: .infinity, alignment: .leading)
        .background(Theme.card, in: RoundedRectangle(cornerRadius: 10))
        .overlay(RoundedRectangle(cornerRadius: 10).stroke(Theme.cardBorder))
    }
}

/// Mientras el motor resume la conversación.
struct CompactingLine: View {
    var body: some View {
        HStack(spacing: 8) {
            ProgressView().controlSize(.small)
            Text("Compactando la conversación…").font(.system(size: 13, weight: .medium))
                .foregroundStyle(Theme.secondary)
            Text("Puedes seguir escribiendo; tu mensaje irá después del resumen.")
                .font(.system(size: 12)).foregroundStyle(Theme.muted)
        }
        .accessibilityElement(children: .combine)
    }
}

/// «566.5k», «1M», «950»: tokens como los enseña el panel de Claude Code.
enum ContextFormat {
    static func tokens(_ value: Int) -> String {
        if value >= 1_000_000 {
            let millions = Double(value) / 1_000_000
            return millions == millions.rounded() ? "\(Int(millions))M" : String(format: "%.1fM", millions)
        }
        guard value >= 1000 else { return "\(value)" }
        let thousands = Double(value) / 1000
        return thousands == thousands.rounded() ? "\(Int(thousands))k" : String(format: "%.1fk", thousands)
    }
}
