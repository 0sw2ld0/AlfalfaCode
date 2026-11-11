import AlfalfaKit
import SwiftUI

/// Un elemento de la conversación: mensaje, respuesta, registro de trabajo, permiso, aviso,
/// resumen de fin de turno, ficha de información o marca de compactación o de deshacer.
struct ItemView: View {

    // MARK: - Propiedades

    let item: TimelineItem
    @ObservedObject var session: LiveSession
    let isFirstPending: Bool
    var closesRun = false
    var liveWork = false

    var body: some View {
        switch item {
        case let .user(itemID, text, queued):
            userMessage(itemID: itemID, text: text, queued: queued)
        case let .agent(id, text):
            VStack(alignment: .leading, spacing: 14) {
                MarkdownView(text: text, folder: session.cwd)
                if closesRun { AgentActions(text: text, time: session.timeline.agentTimes[id]) }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        case let .events(_, events):
            WorkLog(events: events, running: liveWork,
                    waiting: liveWork && session.timeline.status == .waitingPermission,
                    preparing: session.timeline.preparingUndo, expanded: $expanded)
        case let .permission(_, request, state):
            PermissionCard(request: request, state: state, session: session, isFirstPending: isFirstPending)
        case let .notice(_, text):
            Text(text).font(.system(size: 13)).foregroundStyle(Theme.warning)
                .frame(maxWidth: .infinity, alignment: .leading)
        case let .wrapUp(id, wrapUp, answered):
            WrapUpCard(wrapUp: wrapUp, answered: answered, session: session) { session.dismissWrapUp(id) }
        case let .info(_, title, rows):
            info(title: title, rows: rows)
        case let .compaction(_, compaction):
            CompactionMarker(compaction: compaction)
        case let .priceWarning(_, warning, answered):
            PriceWarningCard(warning: warning, answered: answered, session: session)
        case let .undo(_, step, redone):
            UndoMarker(step: step, redone: redone, session: session)
        case let .learning(_, proposal, state):
            LearningCard(proposal: proposal, state: state, session: session)
        }
    }

    // MARK: - Propiedades privadas

    @State private var expanded = false

    // MARK: - Métodos privados

    private func userMessage(itemID: Int, text: String, queued: Bool) -> some View {
        HStack {
            Spacer(minLength: 120)
            VStack(alignment: .trailing, spacing: 4) {
                let parts = ImageTags.split(text)
                if !parts.images.isEmpty {
                    HStack(spacing: 6) {
                        ForEach(parts.images, id: \.self) { path in
                            Thumbnail(url: URL(fileURLWithPath: path), size: 96).help(path)
                        }
                    }
                }
                if !parts.files.isEmpty {
                    HStack(spacing: 6) {
                        ForEach(parts.files, id: \.self) { path in FileChip(url: URL(fileURLWithPath: path)) }
                    }
                }
                if !parts.text.isEmpty {
                    Text(parts.text).font(Theme.body).lineSpacing(Theme.bodyLineSpacing).textSelection(.enabled)
                        .fixedSize(horizontal: false, vertical: true)
                        .padding(.horizontal, 18).padding(.vertical, 13)
                        .background(Theme.bubble, in: RoundedRectangle(cornerRadius: 20))
                }
                if queued {
                    QueuedActions(text: text, itemID: itemID, session: session)
                } else {
                    UserActions(text: ImageTags.split(text).text, itemID: itemID, session: session)
                }
            }
            .opacity(queued ? 0.7 : 1)
        }
    }

    private func info(title: String, rows: [InfoRow]) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title).font(.system(size: 14, weight: .semibold))
            Grid(alignment: .leading, horizontalSpacing: 16, verticalSpacing: 5) {
                ForEach(Array(rows.enumerated()), id: \.offset) { _, row in
                    GridRow {
                        Text(row.name).foregroundStyle(Theme.muted)
                        Text(row.value).foregroundStyle(Theme.secondary).textSelection(.enabled)
                    }
                }
            }
            .font(.system(size: 13))
        }
        .padding(16).frame(maxWidth: .infinity, alignment: .leading)
        .overlay(RoundedRectangle(cornerRadius: 14).stroke(Theme.border))
    }
}
