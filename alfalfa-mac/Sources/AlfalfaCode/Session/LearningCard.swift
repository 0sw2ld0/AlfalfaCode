import AlfalfaKit
import SwiftUI

/// Lo que la revisión al cerrar el turno propone guardar: memoria o una habilidad, con su diff y
/// «Guardar» o «Descartar». Nada se guarda sin ese clic (salvo la memoria tuya y del Mac si lo
/// permitiste en Ajustes ▸ Memoria).
struct LearningCard: View {

    // MARK: - Propiedades

    let proposal: LearningProposal
    let state: LearningProposalState
    @ObservedObject var session: LiveSession

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Image(systemName: proposal.kind == "memoria" ? "brain" : "graduationcap")
                    .font(.system(size: 13)).foregroundStyle(Theme.alfalfa)
                VStack(alignment: .leading, spacing: 3) {
                    Text("Aprendizaje · " + proposal.title).font(.system(size: 13.5, weight: .medium))
                    if !proposal.reason.isEmpty {
                        Text(proposal.reason).font(.system(size: 12.5)).foregroundStyle(Theme.secondary)
                    }
                }
                .fixedSize(horizontal: false, vertical: true)
            }
            if !proposal.diff.isEmpty { DiffBlock(diff: proposal.diff) }
            switch state {
            case .pending:
                HStack(spacing: 8) {
                    Button { session.resolveLearning(proposal.id, apply: true) } label: {
                        Text("Guardar").fontWeight(.semibold).foregroundStyle(Theme.dark)
                            .padding(.horizontal, 14).frame(minHeight: 32)
                            .background(Theme.light, in: RoundedRectangle(cornerRadius: 8))
                    }
                    .buttonStyle(.plain)
                    Button { session.resolveLearning(proposal.id, apply: false) } label: {
                        Text("Descartar").fontWeight(.semibold).padding(.horizontal, 14).frame(minHeight: 32)
                            .overlay(RoundedRectangle(cornerRadius: 8).stroke(Theme.cardBorder))
                    }
                    .buttonStyle(.plain)
                }
                .disabled(session.answered.contains("aprendizaje:" + proposal.id))
            case let .applied(text):
                Text(text).font(.system(size: 12.5)).foregroundStyle(Theme.alfalfa)
            case .discarded:
                Text("Descartado").font(.system(size: 12.5)).foregroundStyle(Theme.muted)
            case let .failed(text):
                Text(text).font(.system(size: 12.5)).foregroundStyle(Theme.danger)
            }
        }
        .padding(16).frame(maxWidth: 660, alignment: .leading)
        .background(Theme.card, in: RoundedRectangle(cornerRadius: 12))
        .overlay(RoundedRectangle(cornerRadius: 12).stroke(Theme.cardBorder))
        .accessibilityElement(children: .contain)
        .accessibilityLabel("Aprendizaje propuesto")
    }
}

/// Un diff unificado entero (el motor rechaza antes lo que no cabe: nunca se enseña recortado),
/// con las líneas añadidas y quitadas en color.
struct DiffBlock: View {

    // MARK: - Propiedades

    let diff: String
    var maxHeight: CGFloat = 220

    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 0) {
                ForEach(Array(lines.enumerated()), id: \.offset) { _, line in
                    Text(line.isEmpty ? " " : line).font(.system(size: 11.5, design: .monospaced))
                        .foregroundStyle(color(line)).frame(maxWidth: .infinity, alignment: .leading)
                }
            }
            .textSelection(.enabled).padding(8)
        }
        .frame(maxHeight: maxHeight)
        .background(Theme.field, in: RoundedRectangle(cornerRadius: 6))
        .accessibilityLabel("Cambios")
    }

    // MARK: - Propiedades privadas

    private var lines: [String] {
        diff.split(separator: "\n", omittingEmptySubsequences: false).map(String.init)
    }

    // MARK: - Métodos privados

    private func color(_ line: String) -> Color {
        if line.hasPrefix("+++") || line.hasPrefix("---") { return Theme.muted }
        if line.hasPrefix("+") { return Theme.alfalfa }
        if line.hasPrefix("-") { return Theme.danger }
        if line.hasPrefix("@@") { return Theme.link }
        return Theme.secondary
    }
}
