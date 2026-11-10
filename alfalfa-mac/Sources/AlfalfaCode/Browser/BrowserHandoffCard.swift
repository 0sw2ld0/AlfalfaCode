import AlfalfaKit
import SwiftUI

/// El agente espera mientras la persona inicia sesión, introduce un código o resuelve un captcha.
struct BrowserHandoffCard: View {
    @ObservedObject var browser: BrowserEngine
    let handoff: BrowserHandoff

    var body: some View {
        VStack(alignment: .leading, spacing: 9) {
            Label("Te toca a ti", systemImage: "person.crop.circle.badge.exclamationmark")
                .font(.system(size: 13, weight: .semibold))
            Text("El agente necesita que hagas esto:").font(.system(size: 12))
            Text(handoff.reason).font(.system(size: 12)).textSelection(.enabled)
            if let fragment = handoff.urlContains, !browser.canContinueHandoff {
                Text("Para continuar, la dirección debe contener «\(fragment)».")
                    .font(.system(size: 11)).foregroundStyle(Theme.secondary)
            }
            HStack {
                Button("Cancelar") { browser.cancelHandoff() }.buttonStyle(.bordered)
                Spacer()
                Button("Continuar") { browser.continueHandoff() }
                    .buttonStyle(.borderedProminent).tint(Theme.alfalfa)
                    .keyboardShortcut(.return, modifiers: [.command, .shift])
                    .disabled(!browser.canContinueHandoff)
            }
            Text("El agente está detenido. Usa la página y pulsa Continuar cuando acabes (⇧⌘↩).")
                .font(.system(size: 10.5)).foregroundStyle(Theme.secondary)
        }
        .foregroundStyle(Theme.text).padding(12).frame(maxWidth: .infinity, alignment: .leading)
        .background(Theme.alfalfa.opacity(0.12))
        .accessibilityElement(children: .contain)
        .accessibilityLabel("Relevo del navegador")
    }
}
