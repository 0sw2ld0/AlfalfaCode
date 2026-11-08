import AlfalfaKit
import SwiftUI

/// La lista de tareas del agente, como la de Claude Code: hecho, en curso y pendiente.
/// Si escribiste después, es «el plan de antes de tu último mensaje» hasta que llegue otro.
struct PlanCard: View {

    // MARK: - Propiedades

    let plan: Plan
    let fromBefore: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 9) {
            if fromBefore {
                Text("Plan de antes de tu último mensaje").font(.system(size: 12.5)).foregroundStyle(Theme.muted)
            }
            if let explanation = plan.explanation, !fromBefore {
                Text(explanation).font(.system(size: 12.5)).foregroundStyle(Theme.muted)
                    .fixedSize(horizontal: false, vertical: true)
            }
            ForEach(Array(plan.steps.enumerated()), id: \.offset) { _, step in
                HStack(alignment: .firstTextBaseline, spacing: 9) {
                    icon(step.status).frame(width: 16)
                    Text(step.text).font(.system(size: 14))
                        .foregroundStyle(step.status == .completed ? Theme.muted : Theme.text)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .accessibilityElement(children: .combine)
                .accessibilityLabel(label(step))
            }
        }
        .padding(.horizontal, 16).padding(.vertical, 14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Theme.card.opacity(0.5), in: RoundedRectangle(cornerRadius: 12))
        .overlay(RoundedRectangle(cornerRadius: 12).stroke(Theme.border))
        .accessibilityElement(children: .contain)
        .accessibilityLabel(fromBefore ? "Plan de antes de tu último mensaje" : "Plan")
    }

    // MARK: - Métodos privados

    @ViewBuilder private func icon(_ status: PlanStep.Status) -> some View {
        switch status {
        case .completed:
            Image(systemName: "checkmark.circle.fill").foregroundStyle(Theme.muted)
        case .inProgress:
            Image(systemName: "ellipsis.circle").foregroundStyle(Theme.text)
        case .pending:
            Image(systemName: "circle.dashed").foregroundStyle(Theme.muted)
        }
    }

    private func label(_ step: PlanStep) -> String {
        switch step.status {
        case .completed: return step.text + ", hecho"
        case .inProgress: return step.text + ", en curso"
        case .pending: return step.text + ", pendiente"
        }
    }
}
