import AlfalfaKit
import AppKit
import SwiftUI

/// Fila bajo cada mensaje del usuario, como en Claude: hace cuánto, copiar, volver a este
/// punto (la conversación vuelve a antes del mensaje y su texto queda en el cuadro para
/// cambiarlo) y bifurcar (una sesión nueva que llega hasta aquí). Los archivos no cambian.
struct UserActions: View {

    // MARK: - Constantes

    private static let goBackHelp = "Volver a este mensaje: la conversación sigue desde antes de él y su texto "
        + "queda en el cuadro para cambiarlo. Los archivos no cambian."
    private static let forkHelp = "Bifurcar: una sesión nueva con la conversación hasta este mensaje y su respuesta"

    // MARK: - Propiedades

    let text: String
    let itemID: Int
    @ObservedObject var session: LiveSession

    var body: some View {
        HStack(spacing: 12) {
            if let time = timeline.userTimes[itemID] {
                TimelineView(.periodic(from: .now, by: 30)) { context in
                    Text(Self.relative(Date(timeIntervalSince1970: time), now: context.date))
                        .font(.system(size: 12.5)).foregroundStyle(Theme.muted)
                }
                Text("·").foregroundStyle(Theme.muted)
            }
            icon(copied ? "checkmark" : "doc.on.doc", copied ? "Copiado" : "Copiar mensaje", enabled: true) {
                NSPasteboard.general.clearContents()
                NSPasteboard.general.setString(text, forType: .string)
                copied = true
                DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) { copied = false }
            }
            icon("arrow.counterclockwise", Self.goBackHelp, enabled: canBranch) { goBack() }
            icon("arrow.triangle.branch", Self.forkHelp, enabled: canBranch) {
                if let id = session.sessionID, let turn { app.fork(sessionID: id, turn: turn, draft: nil) }
            }
        }
    }

    // MARK: - Propiedades privadas

    @EnvironmentObject private var app: AppModel
    @State private var copied = false

    private var timeline: Timeline { session.timeline }
    private var turn: String? { timeline.userTurns[itemID] }
    /// Volver y bifurcar copian la sesión: no con un turno en marcha ni antes de que se guarde.
    private var canBranch: Bool { turn != nil && session.sessionID != nil && !timeline.status.isRunning }

    // MARK: - Métodos públicos

    /// «Hace un momento», «Hace 12 minutos», «Hace 3 horas»; si pasó más de un día, la fecha.
    static func relative(_ date: Date, now: Date = Date()) -> String {
        let seconds = now.timeIntervalSince(date)
        if seconds < 60 { return "Hace un momento" }
        if seconds < 3600 {
            let minutes = Int(seconds / 60)
            return minutes == 1 ? "Hace 1 minuto" : "Hace \(minutes) minutos"
        }
        if seconds < 86_400 {
            let hours = Int(seconds / 3600)
            return hours == 1 ? "Hace 1 hora" : "Hace \(hours) horas"
        }
        return AgentActions.format(date, now: now)
    }

    // MARK: - Métodos privados

    private func goBack() {
        guard let id = session.sessionID, let before = timeline.turnBefore(user: itemID) else { return }
        if let previous = before {
            app.fork(sessionID: id, turn: previous, draft: text)
        } else {
            app.restart(in: session.cwd, with: text)
        }
    }

    private func icon(_ symbol: String, _ help: String, enabled: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: symbol).font(.system(size: 12.5)).foregroundStyle(Theme.muted)
                .frame(width: 20, height: 20).contentShape(Rectangle())
        }
        .buttonStyle(.plain).help(help).accessibilityLabel(help)
        .disabled(!enabled).opacity(enabled ? 1 : 0.4)
    }
}

/// Fila bajo la respuesta final: copiar el texto y la hora, como en Codex.
struct AgentActions: View {

    // MARK: - Propiedades

    let text: String
    let time: Double?

    var body: some View {
        HStack(spacing: 14) {
            Button {
                NSPasteboard.general.clearContents()
                NSPasteboard.general.setString(text, forType: .string)
                copied = true
                DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) { copied = false }
            } label: {
                Image(systemName: copied ? "checkmark" : "doc.on.doc").font(.system(size: 13))
                    .foregroundStyle(Theme.muted).frame(width: 22, height: 22).contentShape(Rectangle())
            }
            .buttonStyle(.plain).help(copied ? "Copiado" : "Copiar respuesta").accessibilityLabel("Copiar respuesta")
            if let time {
                Text(Self.format(Date(timeIntervalSince1970: time)))
                    .font(.system(size: 13)).foregroundStyle(Theme.muted)
            }
        }
    }

    // MARK: - Propiedades privadas

    @State private var copied = false

    // MARK: - Métodos públicos

    /// «1:39 p. m.» hoy; «viernes, 1:39 p. m.» esta semana; si no, la fecha.
    static func format(_ date: Date, now: Date = Date()) -> String {
        let calendar = Calendar.current
        let clock = date.formatted(date: .omitted, time: .shortened)
        if calendar.isDateInToday(date) { return clock }
        if let days = calendar.dateComponents([.day], from: date, to: now).day, days < 7 {
            return date.formatted(.dateTime.weekday(.wide)) + ", " + clock
        }
        return date.formatted(date: .abbreviated, time: .shortened)
    }
}
