import AlfalfaKit
import SwiftUI

/// Tarjeta de una solicitud de permiso: motivo, qué quiere hacer y los botones para
/// aprobar, permitir en la sesión o negar. Ya respondida, sólo dice cómo acabó.
struct PermissionCard: View {

    // MARK: - Propiedades

    let request: PermissionRequest
    let state: PermissionState
    @ObservedObject var session: LiveSession
    let isFirstPending: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            title
            if !request.reasons.isEmpty { reasons }
            if let mcp = request.mcp {
                McpCallDetail(call: mcp)
            } else if let app = request.app {
                AppTargetDetail(app: app)
            } else if let edit = editDiff {
                // Una edición: archivo, líneas +/− y su diff, no la llamada en JSON.
                EditDiffView(diff: edit)
            } else {
                // Acotada: una llamada enorme (un parche crudo) hacía la tarjeta altísima y colgaba la app.
                BoundedCommand(text: request.command)
            }
            if !request.purpose.isEmpty, request.mcp == nil {
                Text(request.purpose).font(.system(size: 12.5)).foregroundStyle(Theme.muted)
            }
            if editDiff == nil, let diff = request.diff, !diff.isEmpty {
                // Memoria o habilidad aprendida: lo que se escribiría, antes de aprobarlo.
                DiffBlock(diff: diff)
            }
            if let justification = request.justification {
                detailLine("Justificación", justification)
            }
            if let rule = request.rule {
                detailLine("Regla", rule)
            }
            if let detail = request.detail, detail != request.justification,
               request.reasons.contains(where: \.leavesSandbox) {
                detailLine(request.retryOf != nil ? "Intento anterior" : "Motivo", detail)
            }
            if let running = session.timeline.runningPermission, running.id == request.id {
                RunningLine(since: running.since)
            }
            if case .pending = state {
                if request.site != nil {
                    BrowserSitePermissionButtons(request: request, session: session)
                } else {
                    HStack(spacing: 10) {
                        approve
                        if request.forSession { allowForSession }
                        deny
                    }
                }
            }
        }
        .padding(.horizontal, 18).padding(.vertical, 16)
        .frame(maxWidth: 660, alignment: .leading)
        .background(Theme.card, in: RoundedRectangle(cornerRadius: 12))
        .overlay(RoundedRectangle(cornerRadius: 12).stroke(Theme.cardBorder))
        .accessibilityElement(children: .contain)
        .accessibilityLabel("Permiso")
    }

    // MARK: - Propiedades privadas

    private var editDiff: EditDiff? {
        guard request.mcp == nil, request.app == nil, let diff = request.diff, !diff.isEmpty else { return nil }
        return EditDiff(diff)
    }

    @ViewBuilder private var title: some View {
        switch state {
        case .pending:
            Text(request.mcp != nil ? "Pide permiso · Herramienta MCP"
                 : request.reasons.first.map { "Pide permiso · \($0.title)" } ?? "Quiere ejecutar un comando")
                .foregroundStyle(Theme.warning).fontWeight(.semibold)
        case .approved:
            Text("Aprobado").foregroundStyle(Theme.alfalfa).fontWeight(.semibold)
        case .approvedForSession:
            Text("Aprobado en esta sesión").foregroundStyle(Theme.alfalfa).fontWeight(.semibold)
        case let .denied(reason):
            Text(reason.isEmpty || reason == "Respuesta del cliente"
                 ? "Negado · no se ejecutó" : "Negado · \(reason.lowercased())")
                .foregroundStyle(Theme.danger).fontWeight(.semibold)
        }
    }

    @ViewBuilder private var approve: some View {
        let label = HStack(spacing: 6) {
            Text("Aprobar").fontWeight(.semibold)
            Text("⌘↩").font(.system(size: 11))
                .padding(.horizontal, 5).padding(.vertical, 1)
                .overlay(RoundedRectangle(cornerRadius: 4).stroke(Color(hex: 0xC9C6BE)))
        }
        .foregroundStyle(Theme.dark)
        .padding(.horizontal, 16).frame(minHeight: 36)
        .background(Theme.light, in: RoundedRectangle(cornerRadius: 8))
        let button = Button { session.answer(request.id, approved: true) } label: { label }
            .buttonStyle(.plain)
            .disabled(session.answered.contains(request.id))
        if isFirstPending {
            // Sólo la solicitud más antigua responde a ⌘↩, siempre por acción del usuario.
            button.keyboardShortcut(.return, modifiers: .command)
        } else {
            button
        }
    }

    private var allowForSession: some View {
        Button { session.answer(request.id, approved: true, session: true) } label: {
            Text("Permitir en esta sesión").fontWeight(.semibold).padding(.horizontal, 14)
                .frame(minHeight: 36)
                .overlay(RoundedRectangle(cornerRadius: 8).stroke(Theme.cardBorder))
        }
        .buttonStyle(.plain)
        .disabled(session.answered.contains(request.id))
        .help(request.app != nil
              ? "Aprueba y no vuelve a preguntar por esta app en esta sesión."
              : request.reasons.contains(.externalContent) && editDiff != nil
              ? "Aprueba y no vuelve a preguntar por estos archivos en esta sesión."
              : request.reasons.contains(.externalContent) && request.reasons.count == 1
              ? "Aprueba y no vuelve a preguntar por esta acción en esta sesión, aunque el agente haya leído contenido externo."
              : request.reasons.contains(where: \.leavesSandbox)
              ? "Aprueba y, hasta que acabe la sesión, esta misma orden saldrá del sandbox sin preguntar."
              : "Aprueba y no vuelve a preguntar por esta misma orden en esta sesión.")
    }

    private var deny: some View {
        Button { session.answer(request.id, approved: false) } label: {
            Text("Negar").fontWeight(.semibold).padding(.horizontal, 16).frame(minHeight: 36)
                .overlay(RoundedRectangle(cornerRadius: 8).stroke(Theme.cardBorder))
        }
        .buttonStyle(.plain)
        .disabled(session.answered.contains(request.id))
    }

    /// Un distintivo por motivo y un único alcance: el de lo que concede aprobar la decisión entera.
    private var reasons: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 6) {
                ForEach(request.reasons, id: \.self) { reason in
                    let color = reason.leavesSandbox ? Theme.danger : Theme.warning
                    Text(reason.title).font(.system(size: 11.5, weight: .semibold)).foregroundStyle(color)
                        .padding(.horizontal, 8).padding(.vertical, 2)
                        .overlay(RoundedRectangle(cornerRadius: 5).stroke(color.opacity(0.6)))
                }
            }
            if let scope = request.scope {
                Text(scope).font(.system(size: 12)).foregroundStyle(Theme.secondary)
            }
        }
        .accessibilityElement(children: .combine)
        .accessibilityLabel("Motivo: " + request.reasons.map(\.title).joined(separator: ", "))
    }

    // MARK: - Métodos privados

    private func detailLine(_ label: String, _ value: String) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 4) {
            Text(label + ":").fontWeight(.semibold)
            Text(value).textSelection(.enabled)
        }
        .font(.system(size: 12.5)).foregroundStyle(Theme.secondary)
    }
}


/// La orden o la llamada de un permiso, como mucho 30 líneas; lo entero, con «Ver todo».
struct BoundedCommand: View {
    let text: String
    @State private var showingAll = false

    var body: some View {
        let preview = TextPreview(text, maxLines: 30)
        VStack(alignment: .leading, spacing: 4) {
            Text(preview.shown).font(Theme.mono).textSelection(.enabled).lineLimit(preview.shownLines)
            if preview.truncated {
                Button(preview.hiddenLines > 0 ? "Ver todo (\(preview.totalLines) líneas)" : "Ver todo") {
                    showingAll = true
                }
                .buttonStyle(.plain).font(.system(size: 12, weight: .medium)).foregroundStyle(Theme.link)
                .sheet(isPresented: $showingAll) { LargeTextSheet(title: "Lo que se aprobaría", text: text) }
            }
        }
    }
}
