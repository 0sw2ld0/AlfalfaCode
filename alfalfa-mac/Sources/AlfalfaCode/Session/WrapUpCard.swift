import AlfalfaKit
import SwiftUI

/// Caja «siguiente paso» del final del turno, como la de Claude Code: resumen, pregunta y
/// hasta dos respuestas. La primera es la recomendada y responde también con ⌘↩. Debajo van
/// las sesiones que propuso el agente.
struct WrapUpCard: View {

    // MARK: - Propiedades

    let wrapUp: WrapUp
    let answered: Bool
    @ObservedObject var session: LiveSession
    let dismiss: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack(alignment: .top, spacing: 12) {
                Text(MarkdownText.inline(wrapUp.recap.isEmpty ? "Sesiones propuestas" : wrapUp.recap))
                    .font(.system(size: 13.5)).foregroundStyle(Theme.secondary)
                    .textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
                    .frame(maxWidth: .infinity, alignment: .leading)
                Button(action: dismiss) {
                    Image(systemName: "xmark").font(.system(size: 12, weight: .medium)).foregroundStyle(Theme.muted)
                        .frame(width: 22, height: 22).contentShape(Rectangle())
                }
                .buttonStyle(.plain).help("Cerrar").accessibilityLabel("Cerrar resumen")
            }
            if let ask = wrapUp.ask {
                Text(ask).font(.system(size: 13.5)).foregroundStyle(Theme.text).textSelection(.enabled)
                if !wrapUp.options.isEmpty {
                    HStack(spacing: 8) {
                        ForEach(Array(wrapUp.options.enumerated()), id: \.offset) { index, option in
                            optionButton(option, primary: index == 0)
                        }
                    }
                    .disabled(!canReply).opacity(canReply ? 1 : 0.5)
                }
            }
            ForEach(wrapUp.sessions) { proposal in
                SessionProposalCard(proposal: proposal, canReply: canReply, session: session)
            }
            if let review = wrapUp.review {
                CodeReviewCard(review: review, canReply: canReply, session: session)
            }
        }
        .padding(20).frame(maxWidth: .infinity, alignment: .leading)
        .overlay(RoundedRectangle(cornerRadius: 14).stroke(Theme.border))
    }

    // MARK: - Propiedades privadas

    private var canReply: Bool {
        !answered && session.canSend && !session.timeline.status.isRunning
    }

    // MARK: - Métodos privados

    @ViewBuilder
    private func optionButton(_ option: String, primary: Bool) -> some View {
        let button = Button { session.reply(option) } label: {
            HStack(spacing: 8) {
                Text(option).font(.system(size: 13, weight: .medium))
                if primary && canReply {
                    Text("⌘↩").font(.system(size: 11)).foregroundStyle(Theme.dark.opacity(0.55))
                }
            }
            .foregroundStyle(primary ? Theme.dark : Theme.text)
            .padding(.horizontal, 12).padding(.vertical, 7)
            .background(primary ? Theme.light : Theme.selection, in: RoundedRectangle(cornerRadius: 8))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain).accessibilityLabel(option)
        if primary && canReply {
            button.keyboardShortcut(.return, modifiers: .command)
        } else {
            button
        }
    }
}
