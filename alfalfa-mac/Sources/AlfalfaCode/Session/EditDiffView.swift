import AlfalfaKit
import SwiftUI

/// Lo que cambia una edición en la tarjeta de permiso: cada archivo con sus líneas +/− y, al
/// desplegarlo, su diff en colores. Con un solo archivo se abre desplegado.
struct EditDiffView: View {
    let diff: EditDiff
    @State private var open: Set<String> = []

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            ForEach(diff.files) { file in
                VStack(alignment: .leading, spacing: 4) {
                    Button { toggle(file.path) } label: { header(file) }
                        .buttonStyle(.plain)
                        .accessibilityLabel(label(file))
                        .accessibilityHint(open.contains(file.path) ? "Pliega el diff" : "Despliega el diff")
                    if open.contains(file.path) {
                        DiffBlock(diff: file.lines.joined(separator: "\n"))
                    }
                }
            }
        }
        .onAppear { if diff.files.count == 1, let only = diff.files.first { open = [only.path] } }
    }

    // MARK: - Métodos privados

    private func header(_ file: EditDiff.File) -> some View {
        HStack(spacing: 8) {
            Image(systemName: open.contains(file.path) ? "chevron.down" : "chevron.right")
                .font(.system(size: 10, weight: .semibold)).foregroundStyle(Theme.muted).frame(width: 12)
            Image(systemName: symbol(file.change)).font(.system(size: 11.5)).foregroundStyle(Theme.secondary)
            Text(file.path).font(Theme.mono).lineLimit(1).truncationMode(.middle)
            if case let .renamed(from) = file.change {
                Text("desde " + from).font(.system(size: 11.5)).foregroundStyle(Theme.muted).lineLimit(1)
            }
            Spacer(minLength: 6)
            Text("+\(file.added)").foregroundStyle(Theme.alfalfa).monospacedDigit()
            Text("−\(file.removed)").foregroundStyle(Theme.danger).monospacedDigit()
        }
        .font(.system(size: 12.5, weight: .medium))
        .padding(.horizontal, 8).frame(minHeight: 28)
        .background(Theme.field, in: RoundedRectangle(cornerRadius: 6))
        .contentShape(Rectangle())
    }

    private func symbol(_ change: EditDiff.File.Change) -> String {
        switch change {
        case .added: return "doc.badge.plus"
        case .deleted: return "trash"
        case .renamed: return "arrow.right.doc.on.clipboard"
        case .modified: return "doc.text"
        }
    }

    private func label(_ file: EditDiff.File) -> String {
        let kind: String
        switch file.change {
        case .added: kind = "Archivo nuevo"
        case .deleted: kind = "Archivo borrado"
        case .renamed: kind = "Archivo movido"
        case .modified: kind = "Archivo"
        }
        return "\(kind) \(file.path): \(file.added) líneas añadidas, \(file.removed) quitadas"
    }

    private func toggle(_ path: String) {
        if open.contains(path) { open.remove(path) } else { open.insert(path) }
    }
}
