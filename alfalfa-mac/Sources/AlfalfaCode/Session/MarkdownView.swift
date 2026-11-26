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
            // Párrafos, títulos, listas y citas seguidos van en un solo texto: se seleccionan de una vez.
            ForEach(Array(MarkdownProse.segments(MarkdownText.blocks(text, folder: folder)).enumerated()),
                    id: \.offset) { _, segment in
                switch segment {
                case let .prose(blocks): ProseView(blocks: blocks)
                case let .block(block): view(for: block)
                }
            }
        }
        .font(Theme.body).lineSpacing(Theme.bodyLineSpacing).tint(Theme.link)
        .fixedSize(horizontal: false, vertical: true)
    }

    // MARK: - Métodos privados

    /// Lo que no es texto corrido: código con sus botones, tablas, separadores e imágenes.
    @ViewBuilder private func view(for block: MarkdownBlock) -> some View {
        switch block {
        case let .code(value, language):
            CodeBlockView(code: value, language: language, folder: folder)
        case let .table(header, rows):
            MarkdownTable(header: header, rows: rows)
        case .rule:
            Divider().overlay(Theme.border).padding(.vertical, 4)
        case let .media(items):
            MediaGallery(items: items)
        case .paragraph, .heading, .bullet, .numbered, .task, .quote:
            ProseView(blocks: [block])
        }
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
