import AlfalfaKit
import SwiftUI

struct CodeReviewCard: View {
    let review: CodeReview
    let canReply: Bool
    @ObservedObject var session: LiveSession
    @EnvironmentObject private var app: AppModel
    @State private var chosenModel = ""

    private var models: [String] {
        review.candidates(in: app.models).map(\.name)
    }
    private var model: String { models.contains(chosenModel) ? chosenModel : models.first ?? "" }

    /// Los candidatos agrupados por proveedor, en su orden: con modelos de Ollama, Anthropic u
    /// OpenAI a la vez, el nombre solo («minimax-m3.1-flash:cloud») no dice de dónde sale.
    private var groups: [(provider: String, names: [String])] {
        var order: [String] = []
        var grouped: [String: [String]] = [:]
        for name in models {
            let provider = ModelProvider.id(of: name, in: app.models)
            if grouped[provider] == nil { order.append(provider) }
            grouped[provider, default: []].append(name)
        }
        return order.map { ($0, grouped[$0] ?? []) }
    }

    /// «minimax-m3.1-flash:cloud · Ollama»: el elegido, con su proveedor.
    private func title(_ name: String) -> String {
        app.label(for: name) + " · " + app.providerName(ModelProvider.id(of: name, in: app.models))
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Label("Revisión con otros ojos", systemImage: "eye").font(.system(size: 13, weight: .semibold))
            if review.canRequest {
                Text("Otro modelo leerá los cambios de este turno. La revisión respeta el presupuesto de la sesión.")
                    .font(.system(size: 12)).foregroundStyle(Theme.secondary)
                HStack(spacing: 10) {
                    Menu {
                        ForEach(groups, id: \.provider) { group in
                            Section(app.providerName(group.provider)) {
                                ForEach(group.names, id: \.self) { name in
                                    Button(app.label(for: name)) { chosenModel = name }
                                }
                            }
                        }
                    } label: {
                        Text(model.isEmpty ? "Elige otro modelo" : title(model))
                    }
                    Button("Revisar cambios") { session.reviewCode(review, model: model) }
                        .disabled(!canReply || session.reviewingCode || model.isEmpty)
                }
                if model.isEmpty {
                    Text("Configura un segundo modelo para poder revisar los cambios.")
                        .font(.system(size: 12)).foregroundStyle(Theme.muted)
                }
                if let notice = app.models.first(where: { $0.name == model })?.notice {
                    Text(notice).font(.system(size: 12)).foregroundStyle(Theme.warning)
                }
            } else if review.state == "revisando" {
                HStack {
                    ProgressView().controlSize(.small)
                    Text("Revisando con \(review.model.map(title) ?? "otro modelo")…")
                    Button("Cancelar revisión") { session.cancelCodeReview() }
                }.font(.system(size: 12))
            }
            if let reason = review.reason { Text(reason).font(.system(size: 12)).foregroundStyle(Theme.muted) }
            if let recap = review.recap { Text(recap).font(.system(size: 13)).textSelection(.enabled) }
            ForEach(review.findings) { finding in
                VStack(alignment: .leading, spacing: 4) {
                    Text("P\(finding.priority) · \(finding.title)").font(.system(size: 13, weight: .medium))
                    Text("\(finding.path):\(finding.line)").font(Theme.mono).foregroundStyle(Theme.muted)
                    Text(finding.detail).font(.system(size: 12.5)).textSelection(.enabled)
                }
            }
            if !review.omitted.isEmpty {
                Text("Sin revisar (binarios, enlaces o archivos excluidos): " + review.omitted.joined(separator: ", "))
                    .font(.system(size: 12)).foregroundStyle(Theme.muted)
            }
            if let cost = review.cost {
                Text((review.estimatedCost ? "Coste máximo estimado: " : "Coste de la revisión: ")
                     + cost.formatted(.currency(code: "USD")))
                    .font(.system(size: 12)).foregroundStyle(Theme.muted)
            }
            if review.canCorrect {
                Button("Corregir hallazgos en este hilo") { session.reply(review.correction) }.disabled(!canReply)
            }
        }
        .padding(.top, 8)
        .accessibilityElement(children: .contain)
        .accessibilityLabel("Revisión con otros ojos")
    }
}
