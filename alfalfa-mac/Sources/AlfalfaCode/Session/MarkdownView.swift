import AlfalfaKit
import SwiftUI

/// Texto del agente con Markdown, como lo pinta Codex: títulos, citas, listas, tareas,
/// tablas, separadores, código y formato en línea. Las imágenes y vídeos locales que cita
/// se ven en la conversación, como en la app de Codex.
struct MarkdownView: View {

    // MARK: - Propiedades

    let text: String
    /// Carpeta de la sesión: ahí se ejecutan los bloques de terminal y se buscan las rutas
    /// relativas de las imágenes.
    var folder: URL?

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            ForEach(Array(MarkdownText.blocks(text, folder: folder).enumerated()), id: \.offset) { _, block in
                view(for: block)
            }
        }
        .font(Theme.body).lineSpacing(Theme.bodyLineSpacing).tint(Theme.link)
        .fixedSize(horizontal: false, vertical: true)
    }

    // MARK: - Métodos privados

    @ViewBuilder private func view(for block: MarkdownBlock) -> some View {
        switch block {
        case let .paragraph(value):
            Text(MarkdownText.inline(value)).textSelection(.enabled)
        case let .heading(level, value):
            Text(MarkdownText.inline(value))
                .font(.system(size: [22, 18, 16, 15, 15, 15][min(level, 6) - 1],
                              weight: level <= 2 ? .bold : .semibold))
                .padding(.top, level <= 2 ? 10 : 4)
                .textSelection(.enabled)
        case let .bullet(value, level):
            item(marker: Text(level == 0 ? "•" : "◦").foregroundStyle(Theme.text), value, level)
        case let .numbered(number, value, level):
            item(marker: Text("\(number).").foregroundStyle(Theme.secondary).monospacedDigit(), value, level)
        case let .task(done, value, level):
            item(marker: Image(systemName: done ? "checkmark.square.fill" : "square")
                    .foregroundStyle(done ? Theme.alfalfa : Theme.muted), value, level)
        case let .quote(value):
            HStack(alignment: .top, spacing: 10) {
                RoundedRectangle(cornerRadius: 1.5).fill(Theme.alfalfa.opacity(0.7)).frame(width: 3)
                Text(MarkdownText.inline(value)).foregroundStyle(Theme.secondary).textSelection(.enabled)
            }
            .fixedSize(horizontal: false, vertical: true)
        case let .code(value, language):
            CodeBlockView(code: value, language: language, folder: folder)
        case let .table(header, rows):
            MarkdownTable(header: header, rows: rows)
        case .rule:
            Divider().overlay(Theme.border).padding(.vertical, 4)
        case let .media(items):
            MediaGallery(items: items)
        }
    }

    /// Viñeta en una columna propia, como en Codex: el texto de varias líneas queda alineado.
    private func item(marker: some View, _ value: String, _ level: Int) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 0) {
            marker.frame(width: 26, alignment: .center)
            Text(MarkdownText.inline(value)).textSelection(.enabled)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
        .padding(.leading, 4 + CGFloat(level) * 24)
    }
}

/// Tabla GFM: cabecera en negrita y una línea entre filas.
struct MarkdownTable: View {

    // MARK: - Propiedades

    let header: [String]
    let rows: [[String]]

    var body: some View {
        Grid(alignment: .topLeading, horizontalSpacing: 16, verticalSpacing: 8) {
            GridRow {
                ForEach(Array(header.enumerated()), id: \.offset) { _, cell in
                    Text(MarkdownText.inline(cell)).fontWeight(.semibold).textSelection(.enabled)
                }
            }
            ForEach(Array(rows.enumerated()), id: \.offset) { _, row in
                Divider().overlay(Theme.border).gridCellUnsizedAxes(.horizontal)
                GridRow {
                    ForEach(Array(row.enumerated()), id: \.offset) { _, cell in
                        Text(MarkdownText.inline(cell)).textSelection(.enabled)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Theme.card, in: RoundedRectangle(cornerRadius: 8))
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(Theme.cardBorder))
    }
}
