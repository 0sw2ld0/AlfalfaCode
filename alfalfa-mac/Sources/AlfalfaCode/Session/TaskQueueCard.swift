import AlfalfaKit
import SwiftUI

struct TaskQueueCard: View {
    let queue: TaskQueue
    @ObservedObject var session: LiveSession
    @EnvironmentObject private var app: AppModel

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Label("Cola de encargos · \(queue.stateText)", systemImage: "list.number")
                    .font(.system(size: 13, weight: .semibold))
                Spacer()
                Text(queue.mode == "serie" ? "Una tras otra" : "Worktrees en paralelo")
                    .font(.system(size: 12)).foregroundStyle(Theme.muted)
            }
            if let reason = queue.reason { Text(reason).font(.system(size: 12)).foregroundStyle(Theme.warning) }
            ScrollView {
                VStack(alignment: .leading, spacing: 8) {
                    ForEach(queue.jobs) { job in
                        DisclosureGroup {
                            if let recap = job.recap { Text(recap).textSelection(.enabled) }
                            if let folder = job.folder {
                                Text(folder).textSelection(.enabled).foregroundStyle(Theme.muted)
                                if let branch = job.branch { Text(branch).textSelection(.enabled) }
                                Button("Abrir worktree en Finder") { NSWorkspace.shared.open(URL(fileURLWithPath: folder)) }
                            }
                            if let id = job.session {
                                Button("Abrir sesión del encargo") { Task { await app.reload(); app.route = .past(id) } }
                            }
                        } label: {
                            VStack(alignment: .leading, spacing: 2) {
                                Text(job.stateText + " · " + job.title).lineLimit(2)
                                if job.state == "ejecutando", let activity = job.activity {
                                    Text(activity).lineLimit(1).foregroundStyle(Theme.muted)
                                }
                            }
                        }
                    }
                }.font(.system(size: 12))
            }.frame(maxHeight: 160)
            HStack {
                if queue.canStart {
                    Button(queue.state == "lista" ? "Iniciar cola" : "Reanudar cola") { session.queue(.start) }
                        .disabled(!session.canSend || session.workingQueue || session.timeline.status.isRunning)
                    Button("Cancelar pendientes") { session.queue(.cancel) }.disabled(!session.canSend)
                } else if queue.running {
                    Button("Pausar después de los activos") { session.queue(.pause) }.disabled(queue.state == "pausando")
                    Button("Detener ahora") { session.queue(.stop) }
                }
                if queue.finished { Text("El resumen final queda en la conversación.").foregroundStyle(Theme.muted) }
            }.font(.system(size: 12))
        }
        .padding(12).background(Theme.field, in: RoundedRectangle(cornerRadius: 10))
        .padding(.horizontal, 24).padding(.bottom, 8)
        .accessibilityElement(children: .contain).accessibilityLabel("Cola de encargos")
    }
}
