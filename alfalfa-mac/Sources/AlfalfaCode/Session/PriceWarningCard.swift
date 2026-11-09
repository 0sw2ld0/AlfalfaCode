import AlfalfaKit
import SwiftUI

/// Aviso de tarifa por contexto largo: «A partir de 100K tokens, Claude Haiku 5.5 cobra 5×.
/// Compactaré en unos 10K», con «Compactar ahora», «Seguir con contexto completo» (no vuelve a
/// preguntar en esta sesión) y «Seguir en una sesión nueva» (empieza con el resumen).
/// En Contexto completo sólo informa.
struct PriceWarningCard: View {

    // MARK: - Propiedades

    let warning: PriceWarning
    let answered: Bool
    @ObservedObject var session: LiveSession

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Image(systemName: "dollarsign.circle").font(.system(size: 13)).foregroundStyle(Theme.warning)
                VStack(alignment: .leading, spacing: 4) {
                    Text(warning.headline).font(.system(size: 13.5, weight: .medium)).foregroundStyle(Theme.text)
                    Text(detail).font(.system(size: 13)).foregroundStyle(Theme.secondary)
                }
                .textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
            }
            if warning.mode == .saving && !answered {
                HStack(spacing: 8) {
                    choice("Compactar ahora", primary: true) { session.compact() }
                        .disabled(!session.canCompact)
                    choice("Seguir con contexto completo") { session.setContextMode(.full) }
                        .help("Usa la ventana entera; pasado el umbral cada petición cuesta "
                              + warning.tier.multiplier + ". No vuelve a preguntar en esta sesión.")
                    choice("Seguir en una sesión nueva") { session.continueInNewSession() }
                        .help("Compacta y abre otra sesión que empieza sólo con el resumen")
                }
            }
        }
        .padding(16).frame(maxWidth: .infinity, alignment: .leading)
        .overlay(RoundedRectangle(cornerRadius: 14).stroke(Theme.border))
        .accessibilityElement(children: .contain)
    }

    // MARK: - Propiedades privadas

    private var detail: String {
        if warning.mode == .full {
            return "Contexto completo: vas por unos \(CatalogFormat.window(warning.estimate)) tokens. "
                + "Compacta para volver a la tarifa barata."
        }
        return warning.detail ?? ""
    }

    // MARK: - Métodos privados

    private func choice(_ title: String, primary: Bool = false, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Text(title).font(.system(size: 13, weight: .medium))
                .foregroundStyle(primary ? Theme.dark : Theme.text)
                .padding(.horizontal, 12).padding(.vertical, 7)
                .background(primary ? Theme.light : Theme.selection, in: RoundedRectangle(cornerRadius: 8))
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain).accessibilityLabel(title)
    }
}

/// En el panel de contexto: el tramo caro del modelo y el modo de la sesión, para cambiarlo.
struct PriceTierRow: View {
    let tier: PriceTier
    let mode: ContextMode
    var onMode: ((ContextMode) -> Void)?

    var body: some View {
        HStack(spacing: 10) {
            Text("Cobra \(tier.multiplier) desde \(CatalogFormat.window(tier.from))")
                .font(.system(size: 12.5)).foregroundStyle(Theme.muted).lineLimit(2)
            Spacer(minLength: 4)
            if let onMode {
                Menu {
                    ForEach(ContextMode.allCases, id: \.self) { option in
                        Button { onMode(option) } label: {
                            if option == mode {
                                Label(option.label, systemImage: "checkmark")
                            } else {
                                Text(option.label)
                            }
                        }
                        .help(option.explanation)
                    }
                } label: {
                    Text(mode.label).font(.system(size: 12.5, weight: .medium))
                }
                .menuStyle(.borderlessButton).fixedSize()
                .help(mode.explanation)
            } else {
                Text(mode.label).font(.system(size: 12.5, weight: .medium)).foregroundStyle(Theme.secondary)
            }
        }
    }
}
