import AlfalfaKit
import SwiftUI

/// Pantalla de sesión: conversación, composición y panel derecho.
struct SessionView: View {

    // MARK: - Propiedades

    @ObservedObject var session: LiveSession
    var notice: String?

    var body: some View {
        HStack(spacing: 0) {
            VStack(spacing: 0) {
                header
                conversation
                Composer(session: session)
            }
            .frame(maxWidth: .infinity)
            Divider().overlay(Theme.border)
            SidePanel(folder: session.cwd.path, snapshot: session.snapshot, ollama: session.ollama,
                      timeline: session.timeline, canCompact: session.canCompact,
                      onCompact: { session.compact() }, onStopProcess: { session.stopProcess($0) })
                .frame(width: 300)
        }
    }

    // MARK: - Propiedades privadas

    private var header: some View {
        HStack(spacing: 10) {
            LeafIcon()
            Text(session.title).font(.system(size: 15, weight: .semibold)).lineLimit(1)
            Spacer()
            if let notice { Text(notice).font(.system(size: 12)).foregroundStyle(Theme.warning).lineLimit(1) }
            if session.isDormant { dormantHint }
            folderButton
        }
        .padding(.horizontal, 24).frame(minHeight: 48).padding(.top, 4)
        .overlay(alignment: .bottom) { Divider().overlay(Theme.border) }
    }

    /// Abre en Finder la carpeta del proyecto, no la del historial de la sesión.
    private var folderButton: some View {
        Button { NSWorkspace.shared.open(session.cwd) } label: {
            Image(systemName: "folder").foregroundStyle(Theme.secondary)
                .frame(width: 26, height: 26).contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .help("Abrir la carpeta del proyecto en Finder (\(Presentation.abbreviate(session.cwd.path)))")
        .accessibilityLabel("Abrir la carpeta del proyecto en Finder")
    }

    @ViewBuilder private var dormantHint: some View {
        if session.resumableWithoutMessage {
            Button { session.wake(with: nil) } label: {
                Label("Reanudar", systemImage: "arrow.clockwise").font(.system(size: 12.5, weight: .semibold))
                    .padding(.horizontal, 10).frame(minHeight: 26)
                    .background(Theme.light, in: RoundedRectangle(cornerRadius: 7))
                    .foregroundStyle(Theme.dark)
            }
            .buttonStyle(.plain).help("Continuar el encargo donde se quedó")
        } else {
            Text("Escribe para continuar la sesión").font(.system(size: 12)).foregroundStyle(Theme.muted)
        }
    }

    private var conversation: some View {
        let firstPending = session.timeline.pendingPermissions.first?.id
        return ScrollViewReader { proxy in
            ScrollView {
                let items = session.timeline.items
                // Sólo el último grupo de acciones es el que está trabajando ahora.
                let liveWork = session.timeline.status.isRunning
                    ? items.lastIndex { if case .events = $0 { return true } else { return false } } : nil
                LazyVStack(alignment: .leading, spacing: 22) {
                    ForEach(Array(items.enumerated()), id: \.element.id) { index, item in
                        ItemView(item: item, session: session,
                                 isFirstPending: firstPending.map { isPermission(item, $0) } ?? false,
                                 closesRun: closesRun(items, index), liveWork: index == liveWork)
                            .id(item.id)
                    }
                    if let plan = session.timeline.visiblePlan {
                        PlanCard(plan: plan, fromBefore: session.timeline.planIsFromBeforeLastMessage).id("plan")
                    }
                    if session.timeline.compacting { CompactingLine().id("compactando") }
                }
                .frame(maxWidth: Theme.column, alignment: .leading)
                .padding(.horizontal, 40).padding(.top, 28).padding(.bottom, 32)
                .frame(maxWidth: .infinity)
            }
            .onChange(of: session.timeline.items.count) {
                if let last = session.timeline.items.last {
                    withAnimation { proxy.scrollTo(last.id, anchor: .bottom) }
                }
            }
            .onChange(of: session.timeline.compacting) { _, compacting in
                if compacting { withAnimation { proxy.scrollTo("compactando", anchor: .bottom) } }
            }
        }
    }

    // MARK: - Métodos privados

    /// Una respuesta del agente que no va seguida de más trabajo: lleva copiar y la hora.
    private func closesRun(_ items: [TimelineItem], _ index: Int) -> Bool {
        guard case .agent = items[index] else { return false }
        guard index + 1 < items.count else { return !session.timeline.status.isRunning }
        switch items[index + 1] {
        case .events, .agent, .permission: return false
        default: return true
        }
    }

    private func isPermission(_ item: TimelineItem, _ id: String) -> Bool {
        if case let .permission(_, request, _) = item { return request.id == id }
        return false
    }
}
