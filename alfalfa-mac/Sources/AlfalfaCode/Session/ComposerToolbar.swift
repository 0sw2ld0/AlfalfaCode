import AlfalfaKit
import SwiftUI

/// Barra bajo el cuadro de composición: modelo, esfuerzo y modo de la sesión, avisos de
/// subagentes, procesos y compactación, y el estado del turno.
struct ComposerToolbar: View {

    // MARK: - Propiedades

    @ObservedObject var session: LiveSession

    var body: some View {
        HStack(spacing: 16) {
            modelMenu
            effortMenu
            ModeMenu(mode: session.mode, help: changeHelp("Modo de permisos")) {
                session.change(mode: $0)
            }
            if session.readOnly {
                Label("Solo lectura", systemImage: "lock.fill").foregroundStyle(Theme.alfalfaLight)
            }
            Spacer()
            subagentsHint
            processesHint
            compactionHint
            statusLabel
        }
        .font(.system(size: 12.5)).foregroundStyle(Theme.secondary).padding(.horizontal, 6)
    }

    // MARK: - Propiedades privadas

    @EnvironmentObject private var app: AppModel

    /// Modelo y esfuerzo de la sesión: se pueden cambiar en cualquier momento y valen desde el
    /// siguiente turno, como /model en Codex.
    private var modelMenu: some View {
        Menu {
            ModelMenuItems(groups: groups, selected: session.model, label: app.label(for:),
                           providerName: app.providerName) { session.change(model: $0) }
        } label: {
            Text(app.label(for: session.model)).font(.system(size: 12.5, design: .monospaced))
        }
        .menuStyle(.borderlessButton).fixedSize().accessibilityLabel("Modelo")
        .help(changeHelp("Modelo de la sesión"))
    }

    private var effortMenu: some View {
        Menu {
            ForEach(Presentation.efforts, id: \.value) { effort in
                Button { session.change(effort: effort.value) } label: {
                    if effort.value == session.effort {
                        Label(effort.label, systemImage: "checkmark")
                    } else {
                        Text(effort.label)
                    }
                }
            }
        } label: {
            Text(Presentation.effortLabel(session.effort)).font(.system(size: 12.5))
        }
        .menuStyle(.borderlessButton).fixedSize().accessibilityLabel("Esfuerzo")
        .help(changeHelp("Esfuerzo de la sesión"))
    }

    private var groups: [(provider: String, names: [String])] {
        let all = app.modelGroups
        if all.contains(where: { $0.names.contains(session.model) }) { return all }
        return [(Presentation.provider(of: session.model), [session.model])] + all
    }

    /// «2 subagentes trabajando»; el panel de la derecha enseña sus pasos.
    @ViewBuilder private var subagentsHint: some View {
        let count = session.timeline.workingSubagents.count
        if count > 0 {
            Label(count == 1 ? "1 subagente trabajando" : "\(count) subagentes trabajando",
                  systemImage: "person.2")
                .foregroundStyle(Theme.secondary)
                .help(session.timeline.workingSubagents.map { $0.id + " · " + $0.name }.joined(separator: "\n"))
        }
    }

    /// «1 proceso en segundo plano», como el aviso de Codex; un clic los lista.
    @ViewBuilder private var processesHint: some View {
        let count = session.timeline.liveProcesses.count
        if count > 0 {
            Button { session.showProcesses() } label: {
                Label(count == 1 ? "1 proceso en segundo plano" : "\(count) procesos en segundo plano",
                      systemImage: "terminal")
                    .foregroundStyle(Theme.secondary)
            }
            .buttonStyle(.plain)
            .help("Siguen abiertos aunque acabe el turno. /procesos los lista y /cerrar-procesos los cierra; "
                  + "también desde el panel.")
        }
    }

    /// Desde el 80 % del umbral, cuánto falta para la compactación automática; un clic compacta.
    @ViewBuilder private var compactionHint: some View {
        let timeline = session.timeline
        if let progress = timeline.compactionProgress, progress >= 0.8, let left = timeline.tokensUntilCompaction {
            Button { session.compact() } label: {
                Label(left == 0 ? "Se compactará en la siguiente respuesta"
                      : "\(Int(((1 - progress) * 100).rounded())) % hasta compactar",
                      systemImage: "arrow.down.right.and.arrow.up.left")
                    .foregroundStyle(progress >= 0.95 ? Theme.warning : Theme.secondary)
            }
            .buttonStyle(.plain).disabled(!session.canCompact)
            .help("Quedan \(ContextFormat.tokens(left)) tokens hasta la compactación automática. "
                  + "Pulsa para compactar ya.")
        }
    }

    @ViewBuilder private var statusLabel: some View {
        let status = session.timeline.status
        switch status {
        case .interrupted:
            Text("● Interrumpida · se puede reanudar").foregroundStyle(Theme.warning)
        case .failed:
            Text("● fallida").foregroundStyle(Theme.danger)
        case .completed:
            Text("● completada").foregroundStyle(Theme.alfalfa)
        case .waitingPermission:
            Text("● esperando permiso").foregroundStyle(Theme.warning)
        case .blocked:
            Text("● bloqueada").foregroundStyle(Theme.warning)
        case .idle:
            Text("● listo").foregroundStyle(Theme.alfalfa)
        default:
            Text("● \(status.label)").foregroundStyle(Theme.alfalfa)
        }
    }

    // MARK: - Métodos privados

    /// Con un turno en marcha, el cambio vale desde el siguiente; si no, la ayuda normal.
    private func changeHelp(_ idle: String) -> String {
        session.timeline.status.isRunning ? "El cambio vale desde el siguiente turno" : idle
    }
}
