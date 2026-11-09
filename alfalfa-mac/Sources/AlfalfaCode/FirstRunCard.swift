import AlfalfaKit
import SwiftUI

/// «Empieza gratis»: sale en Nuevo encargo mientras no hay ningún proveedor configurado. Los
/// modelos gratis ahora, su aviso de datos y OpenRouter con un clic, sin pegar ninguna clave.
struct FirstRunCard: View {
    @EnvironmentObject private var model: AppModel
    let firstRun: FirstRun
    let openProviders: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Empieza gratis, sin claves").font(.system(size: 15, weight: .semibold))
            Text("Todavía no hay ningún proveedor configurado. Estos modelos son gratis ahora:")
                .font(.system(size: 12.5)).foregroundStyle(Theme.secondary)
            Label(firstRun.notice, systemImage: "exclamationmark.triangle")
                .font(.system(size: 12)).foregroundStyle(Theme.warning)
                .fixedSize(horizontal: false, vertical: true)
            ForEach(firstRun.providers.prefix(3)) { provider in
                row(provider)
            }
            if let error = model.providersError {
                Text(error).font(.system(size: 12)).foregroundStyle(Theme.danger)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Button("Otras formas: Ollama, tu clave de OpenAI o de Claude, un código de cortesía…",
                   action: openProviders)
                .buttonStyle(.plain).font(.system(size: 12)).foregroundStyle(Theme.alfalfaLight)
        }
        .padding(16)
        .frame(width: 460, alignment: .leading)
        .background(Theme.field, in: RoundedRectangle(cornerRadius: 12))
        .overlay(RoundedRectangle(cornerRadius: 12).stroke(Theme.border))
        .accessibilityElement(children: .contain)
        .accessibilityLabel("Empieza gratis")
    }

    @ViewBuilder
    private func row(_ provider: FirstRunProvider) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .firstTextBaseline) {
                Text(provider.name).font(.system(size: 13, weight: .medium))
                Text(summary(provider)).font(.system(size: 12)).foregroundStyle(Theme.secondary).lineLimit(1)
            }
            if provider.connects {
                if let waiting = model.connecting, waiting.provider == provider.id {
                    HStack(spacing: 8) {
                        ProgressView().controlSize(.small)
                        Text("Autoriza AlfalfaCode en el navegador…").font(.system(size: 12))
                        if let url = waiting.url {
                            Link("Abrir otra vez", destination: url).font(.system(size: 12))
                        }
                        Spacer()
                        Button("Cancelar") { model.cancelConnect() }.controlSize(.small)
                    }
                } else {
                    Button { Task { await model.connect(provider.id) } } label: {
                        Label("Conectar con \(provider.name)", systemImage: "arrow.up.forward.app")
                    }
                    .buttonStyle(.borderedProminent).tint(Theme.alfalfa).controlSize(.regular)
                    .disabled(model.connecting != nil)
                    .help("Se abre \(provider.name) en el navegador para entrar o crear tu cuenta gratis. "
                          + "La clave va al Llavero sin que la veas ni la pegues.")
                    Text("Se abre el navegador para entrar o crear tu cuenta gratis; la clave va sola al Llavero.")
                        .font(.system(size: 11.5)).foregroundStyle(Theme.muted)
                }
            } else {
                Button("Usar con su clave gratis…") { model.startFree(with: provider) }
                    .controlSize(.small).disabled(model.connecting != nil)
            }
        }
    }

    private func summary(_ provider: FirstRunProvider) -> String {
        let names = provider.labels.prefix(2).joined(separator: ", ")
        let more = provider.labels.count > 2 ? " y \(provider.labels.count - 2) más" : ""
        return names + more
    }
}
