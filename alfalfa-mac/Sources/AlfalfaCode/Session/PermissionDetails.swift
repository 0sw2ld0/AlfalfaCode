import AlfalfaKit
import SwiftUI

/// «Ejecutándose · 1 min 5 s»: la orden aprobada sigue corriendo; el tiempo avanza solo.
struct RunningLine: View {

    // MARK: - Propiedades

    let since: Double
    /// Segundos a partir de los que se explica que la orden puede tardar.
    static let slowAfter: Double = 20

    var body: some View {
        TimelineView(.periodic(from: .now, by: 1)) { context in
            let elapsed = context.date.timeIntervalSince1970 - since
            HStack(spacing: 8) {
                ProgressView().controlSize(.small)
                Text("Ejecutándose · " + ToolEvent.durationText(elapsed))
                    .font(.system(size: 13, weight: .medium)).foregroundStyle(Theme.secondary).monospacedDigit()
                // Sólo si de verdad tarda: salía en un `git push` de 2 segundos.
                if elapsed >= Self.slowAfter {
                    Text("Algunas órdenes, como compilar o abrir una app, tardan varios minutos.")
                        .font(.system(size: 12)).foregroundStyle(Theme.muted)
                }
            }
        }
        .accessibilityElement(children: .combine)
    }
}

/// La app que el agente quiere mirar o manejar, con su bundle id para distinguirla.
struct AppTargetDetail: View {

    // MARK: - Propiedades

    let app: AppTarget

    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            HStack(spacing: 6) {
                Image(systemName: "macwindow.and.cursorarrow")
                Text(app.name).fontWeight(.semibold)
                if let action = app.action { Text("· " + action).foregroundStyle(Theme.muted) }
            }
            if let bundle = app.bundle {
                Text(bundle).font(Theme.mono).foregroundStyle(Theme.muted).textSelection(.enabled)
            }
            if let level = app.levelText {
                Label(level, systemImage: "eye").font(.system(size: 12)).foregroundStyle(Theme.secondary)
            }
        }
        .font(.system(size: 13))
    }
}

/// Servidor, herramienta y argumentos de una llamada MCP; el motor ya redactó las credenciales.
struct McpCallDetail: View {

    // MARK: - Propiedades

    let call: McpCall

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 6) {
                Text(call.server).fontWeight(.semibold)
                Text("·").foregroundStyle(Theme.muted)
                Text(call.tool).font(Theme.mono)
            }
            .font(.system(size: 13))
            if call.arguments.isEmpty {
                Text("Sin argumentos").font(.system(size: 12.5)).foregroundStyle(Theme.muted)
            } else {
                arguments
            }
            if !call.callID.isEmpty {
                Text("Llamada \(call.callID)").font(.system(size: 11.5)).foregroundStyle(Theme.muted)
                    .textSelection(.enabled)
            }
        }
        .accessibilityElement(children: .combine)
        .accessibilityLabel("Herramienta MCP \(call.tool) del servidor \(call.server)")
    }

    // MARK: - Propiedades privadas

    private var arguments: some View {
        VStack(alignment: .leading, spacing: 3) {
            ForEach(call.arguments, id: \.key) { argument in
                HStack(alignment: .firstTextBaseline, spacing: 6) {
                    Text(argument.key + ":").fontWeight(.semibold).foregroundStyle(Theme.secondary)
                    // Acotado: un argumento puede ser un documento entero.
                    Text(TextPreview(argument.value, maxLines: 4).shown).lineLimit(4).truncationMode(.tail)
                }
                .font(Theme.mono)
            }
            if call.arguments.contains(where: { TextPreview($0.value, maxLines: 4).truncated }) {
                SeeAllButton(label: "Ver todos los argumentos", title: call.server + " · " + call.tool,
                             text: call.arguments.map { $0.key + ": " + $0.value }.joined(separator: "\n\n"))
            }
        }
        .textSelection(.enabled)
        .padding(10).frame(maxWidth: .infinity, alignment: .leading)
        .background(Theme.field, in: RoundedRectangle(cornerRadius: 8))
    }
}
