import AlfalfaKit
import AppKit
import SwiftUI

/// Fila bajo un mensaje en cola, como en Claude: copiar, devolverlo al cuadro para editarlo,
/// redirigir, quitarlo de la cola y «Enviar ahora». Redirigir y Enviar ahora sólo con un turno
/// en marcha. Mide lo mismo que la fila de un mensaje ya entregado (UserActions): al
/// entregarse, la conversación no salta.
struct QueuedActions: View {

    // MARK: - Constantes

    private static let editHelp = "Editar: quitarlo de la cola y devolverlo al cuadro, con sus imágenes"
    private static let redirectHelp = "Redirigir: que lo lea ya, sin cortar lo que está haciendo "
        + "(descarta lo que estaba pensando)"
    private static let removeHelp = "Quitar de la cola sin enviarlo"
    private static let sendNowHelp = "Detener el turno en marcha y enviar este mensaje"

    // MARK: - Propiedades

    let text: String
    let itemID: Int
    @ObservedObject var session: LiveSession

    var body: some View {
        HStack(spacing: 12) {
            Text(unqueuing ? "quitando…" : "en cola").font(.system(size: 12.5)).foregroundStyle(Theme.muted)
            icon(copied ? "checkmark" : "doc.on.doc", copied ? "Copiado" : "Copiar mensaje", enabled: true) {
                NSPasteboard.general.clearContents()
                NSPasteboard.general.setString(ImageTags.split(text).text, forType: .string)
                copied = true
                DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) { copied = false }
            }
            icon("arrow.counterclockwise", Self.editHelp, enabled: !unqueuing) {
                session.unqueue(item: itemID, action: .edit)
            }
            if running {
                icon("arrow.turn.down.right", Self.redirectHelp, enabled: !unqueuing) { session.redirect() }
            }
            icon("xmark", Self.removeHelp, enabled: !unqueuing) { session.unqueue(item: itemID, action: .remove) }
            if running {
                Button("Enviar ahora") { session.sendNow(text) }
                    .font(.system(size: 12.5)).foregroundStyle(Theme.secondary)
                    .buttonStyle(.plain).help(Self.sendNowHelp).disabled(unqueuing)
            }
        }
        .frame(height: 20)
    }

    // MARK: - Propiedades privadas

    @State private var copied = false

    private var running: Bool { session.timeline.status.isRunning }
    private var unqueuing: Bool { session.timeline.isUnqueuing(item: itemID) }

    // MARK: - Métodos privados

    private func icon(_ symbol: String, _ help: String, enabled: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: symbol).font(.system(size: 12.5)).foregroundStyle(Theme.muted)
                .frame(width: 20, height: 20).contentShape(Rectangle())
        }
        .buttonStyle(.plain).help(help).accessibilityLabel(help)
        .disabled(!enabled).opacity(enabled ? 1 : 0.4)
    }
}
