import AlfalfaKit
import SwiftUI

/// Una sesión que propone el agente, como la tarjeta «Nueva sesión» de Claude Code: «Nueva sesión»
/// la empieza aparte en la misma carpeta y «Hacerlo aquí» se lo pide a esta sesión.
struct SessionProposalCard: View {

    // MARK: - Propiedades

    let proposal: SessionProposal
    let canReply: Bool
    @ObservedObject var session: LiveSession
    @EnvironmentObject private var model: AppModel
    @State private var started = false
    @State private var expanded = false

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 8) {
                Image(systemName: "plus.rectangle.on.rectangle").foregroundStyle(Theme.alfalfa)
                    .accessibilityHidden(true)
                Text(proposal.title).font(.system(size: 13.5, weight: .semibold)).lineLimit(2)
                Spacer(minLength: 0)
            }
            Text(proposal.task).font(.system(size: 12.5)).foregroundStyle(Theme.secondary)
                .lineLimit(expanded ? nil : 3).textSelection(.enabled)
                .fixedSize(horizontal: false, vertical: true)
                .onTapGesture { expanded.toggle() }
                .help(expanded ? "Pulsa para recoger el encargo" : "Pulsa para ver el encargo entero")
            if started || model.hasChild(for: proposal, of: session) {
                Label("Empezada en otra sesión", systemImage: "checkmark.circle.fill")
                    .font(.system(size: 12.5)).foregroundStyle(Theme.alfalfa)
            } else {
                buttons
            }
        }
        .padding(14).frame(maxWidth: .infinity, alignment: .leading)
        .background(Theme.card, in: RoundedRectangle(cornerRadius: 10))
        .overlay(RoundedRectangle(cornerRadius: 10).stroke(Theme.cardBorder))
        .accessibilityElement(children: .contain)
        .accessibilityLabel("Sesión propuesta: \(proposal.title)")
    }

    // MARK: - Propiedades privadas

    private var buttons: some View {
        HStack(spacing: 8) {
            Button { started = model.startProposed(proposal, from: session) } label: {
                Text("Nueva sesión").font(.system(size: 13, weight: .medium)).foregroundStyle(Theme.dark)
                    .padding(.horizontal, 12).padding(.vertical, 7)
                    .background(Theme.light, in: RoundedRectangle(cornerRadius: 8))
            }
            .buttonStyle(.plain).disabled(model.launcher == nil)
            .help("Empieza el encargo en una sesión aparte, en la misma carpeta, sin salir de esta")
            Button { session.reply(SessionProposal.doItHere(proposal)) } label: {
                Text("Hacerlo aquí").font(.system(size: 13, weight: .medium)).foregroundStyle(Theme.text)
                    .padding(.horizontal, 12).padding(.vertical, 7)
                    .background(Theme.selection, in: RoundedRectangle(cornerRadius: 8))
            }
            .buttonStyle(.plain).disabled(!canReply).opacity(canReply ? 1 : 0.5)
            .help("Pide a esta sesión que lo haga ella")
        }
    }
}
