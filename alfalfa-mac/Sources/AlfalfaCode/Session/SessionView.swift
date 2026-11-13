import AlfalfaKit
import SwiftUI

/// Pantalla de sesión: conversación, composición y, a la derecha, el navegador, la terminal o el panel de detalles.
struct SessionView: View {

    // MARK: - Propiedades

    @ObservedObject var session: LiveSession
    @EnvironmentObject private var app: AppModel
    var notice: String?
    @State private var planningQueue = false
    @State private var showingBrowserActivity = false

    var body: some View {
        let terminals = TerminalHub.shared.terminals(for: session)
        HStack(spacing: 0) {
            VStack(spacing: 0) {
                header(terminals)
                conversation.environment(\.runInTerminal) { terminals.run($0) }
                if let queue = session.timeline.taskQueue { TaskQueueCard(queue: queue, session: session) }
                Composer(session: session)
            }
            .frame(maxWidth: .infinity)
            Divider().overlay(Theme.border)
            SessionBrowserSlot(browser: session.browser, terminals: terminals) {
                SessionSidePane(terminals: terminals) {
                    let provider = ModelProvider.id(of: session.model, in: app.models)
                    SidePanel(folder: session.cwd.path, snapshot: session.snapshot, ollama: session.ollama,
                              timeline: session.timeline, providerID: provider, providerName: app.providerName(provider),
                              canCompact: session.canCompact,
                              onCompact: { session.compact() }, onStopProcess: { session.stopProcess($0) },
                              onContextMode: { session.setContextMode($0) },
                              sessionCap: session.sessionCap,
                              onSessionCap: session.sessionID == nil ? nil : { session.setSessionCap($0) })
                }
            }
        }
    }

    // MARK: - Propiedades privadas

    private var activityStore: BrowserActivityStore? {
        guard let id = session.sessionID else { return nil }
        return BrowserActivityStore(home: Attachments.directory().deletingLastPathComponent(), sessionID: id)
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

    @State private var atBottom = true
    private static let bottomID = "final-de-la-conversacion"
    private static var hasScrollGeometry: Bool {
        if #available(macOS 15, *) { return true }
        return false
    }

    private var conversation: some View {
        let firstPending = session.timeline.pendingPermissions.first?.id
        return ScrollViewReader { proxy in
            ScrollView {
                let items = session.timeline.items
                // Sólo el último grupo de acciones es el que está trabajando ahora, y sólo si es de
                // este turno: el de antes del último mensaje ya no corre. Si el agente escribió
                // después, el grupo terminó y lo que sigue es pensar (la fila de abajo).
                let lastUser = items.lastIndex { if case .user = $0 { return true } else { return false } }
                let lastAgent = items.lastIndex { if case .agent = $0 { return true } else { return false } }
                let lastEvents = items.lastIndex { if case .events = $0 { return true } else { return false } }
                let status = session.timeline.status
                let liveWork = status.isRunning && (lastEvents ?? -1) > max(lastUser ?? -1, lastAgent ?? -1)
                    ? lastEvents : nil
                // VStack y no LazyVStack: la lista perezosa, con filas de alto variable, el seguimiento
                // del final y algo animado, entraba en un bucle de layout que colgaba la app al 100 %.
                VStack(alignment: .leading, spacing: 22) {
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
                    // Sin herramienta en curso: el modelo piensa desde el último mensaje.
                    if liveWork == nil, status.isRunning, status != .waitingPermission, !session.timeline.compacting,
                       session.timeline.taskQueue?.running != true {
                        LiveActivityRow(activity: .thinking(since: session.timeline.thinkingSince,
                                                         preparing: session.timeline.preparingUndo)).id("pensando")
                    }
                    // El pie: a dónde va «Ir al final» y, en macOS 14, cómo se sabe si estás abajo.
                    Color.clear.frame(height: 1).id(Self.bottomID)
                        .onAppear { if !Self.hasScrollGeometry { atBottom = true } }
                        .onDisappear { if !Self.hasScrollGeometry { atBottom = false } }
                }
                .frame(maxWidth: Theme.column, alignment: .leading)
                .padding(.horizontal, 40).padding(.top, 28).padding(.bottom, 32)
                .frame(maxWidth: .infinity)
            }
            .trackingBottom($atBottom)
            .onChange(of: session.timeline.items.count) {
                // Abierta desde la búsqueda de la barra lateral: al mensaje encontrado, no al final.
                if scrollToSearch(proxy) { return }
                // Como Claude Code y Codex: sólo sigue al final si ya estabas abajo o acabas de escribir.
                // Si subiste a leer, te quedas donde estás y aparece «Ir al final».
                let wrote: Bool
                if case .user = session.timeline.items.last { wrote = true } else { wrote = false }
                if AutoScroll.follows(atBottom: atBottom, userJustWrote: wrote) {
                    withAnimation { proxy.scrollTo(Self.bottomID, anchor: .bottom) }
                }
            }
            .overlay(alignment: .bottom) {
                if !atBottom {
                    Button {
                        withAnimation { proxy.scrollTo(Self.bottomID, anchor: .bottom) }
                        atBottom = true
                    } label: {
                        Label("Ir al final", systemImage: "arrow.down").font(.system(size: 12.5, weight: .semibold))
                            .padding(.horizontal, 12).frame(minHeight: 30)
                            .background(Theme.card, in: Capsule())
                            .overlay(Capsule().stroke(Theme.cardBorder))
                    }
                    .buttonStyle(.plain)
                    .padding(.bottom, 12)
                    .transition(.opacity)
                    .help("Vuelve al último mensaje y sigue la conversación")
                }
            }
            .onChange(of: session.searchAnchor) { _ = scrollToSearch(proxy) }
            .onAppear { _ = scrollToSearch(proxy) }
            .onChange(of: session.timeline.compacting) { _, compacting in
                if compacting { withAnimation { proxy.scrollTo("compactando", anchor: .bottom) } }
            }
        }
    }

    // MARK: - Métodos privados

    /// Lleva la conversación al mensaje de un resultado de búsqueda. Si la conversación ya está
    /// pintada y no lo tiene (un resultado de una herramienta), se olvida y se queda donde estaba.
    private func scrollToSearch(_ proxy: ScrollViewProxy) -> Bool {
        guard let anchor = session.searchAnchor, !session.timeline.items.isEmpty else { return false }
        session.searchAnchor = nil
        guard let id = session.timeline.itemID(matching: anchor) else { return false }
        DispatchQueue.main.async { withAnimation { proxy.scrollTo(id, anchor: .top) } }
        return true
    }

    private func header(_ terminals: SessionTerminals) -> some View {
        HStack(spacing: 10) {
            LeafIcon()
            Text(session.title).font(.system(size: 15, weight: .semibold)).lineLimit(1)
            Spacer()
            Button { planningQueue = true } label: { Image(systemName: "list.number") }
                .help("Preparar una cola de encargos").accessibilityLabel("Cola de encargos")
                .disabled(!session.canSend || session.timeline.status.isRunning || session.reviewingCode
                    || session.workingQueue || session.timeline.taskQueue.map { !$0.finished } == true)
                .sheet(isPresented: $planningQueue) {
                    TaskQueueEditor(folder: session.cwd.path) { tasks, mode, parallelism in
                        session.queue(.prepare, tasks: tasks, mode: mode, parallelism: parallelism)
                    }
                }
            if let notice { Text(notice).font(.system(size: 12)).foregroundStyle(Theme.warning).lineLimit(1) }
            if session.isDormant { dormantHint }
            if session.browserBackend == "cuybrowser" {
                Label("CuyBrowser", systemImage: "globe").font(.caption).foregroundStyle(Theme.secondary)
                    .help("Chromium se abre en su propia ventana, con un perfil de este proyecto.")
            } else if session.browserBackend != "off" {
                BrowserToggle(browser: session.browser, terminals: terminals) { session.openBrowser() }
            }
            TerminalToggle(terminals: terminals)
            Button("Qué hizo el agente", systemImage: "list.bullet.rectangle") { showingBrowserActivity = true }
                .labelStyle(.iconOnly)
                .help("Qué hizo el agente: actividad del navegador en esta sesión")
                .sheet(isPresented: $showingBrowserActivity) { BrowserActivityView(store: activityStore) }
            folderButton
        }
        .padding(.horizontal, 24).frame(minHeight: 48).padding(.top, 4)
        .overlay(alignment: .bottom) { Divider().overlay(Theme.border) }
    }

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


// MARK: - Seguir el final

private extension View {
    /// `atBottom`: el final de la conversación está a la vista (con 60 pt de margen). En macOS 15
    /// lo dice la geometría del scroll; en macOS 14, la marca del pie al aparecer y desaparecer.
    @ViewBuilder func trackingBottom(_ atBottom: Binding<Bool>) -> some View {
        if #available(macOS 15, *) {
            onScrollGeometryChange(for: Bool.self) { geometry in
                AutoScroll.isAtBottom(offset: geometry.contentOffset.y, visible: geometry.containerSize.height,
                                      content: geometry.contentSize.height)
            } action: { _, now in
                if atBottom.wrappedValue != now { atBottom.wrappedValue = now }
            }
        } else {
            self
        }
    }
}
