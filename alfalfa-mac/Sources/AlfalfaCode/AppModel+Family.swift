import AlfalfaKit
import Foundation

/// Sesiones padre e hija, como en Claude Code: la hija sale debajo de su padre y, al terminar
/// cada turno, le manda una nota con el principio de su informe.
extension AppModel {

    // MARK: - Métodos públicos

    /// La sesión padre de una fila de la barra lateral, si la tiene.
    func parentID(of row: SidebarRow) -> String? {
        guard let id = row.sessionID else { return nil }
        return live.first { $0.sessionID == id }?.parentSessionID ?? summaries.first { $0.id == id }?.parent
    }

    /// Si la propuesta de esta sesión ya se empezó aparte, mirando las hijas abiertas y las guardadas.
    func hasChild(for proposal: SessionProposal, of session: LiveSession) -> Bool {
        let opened = live.map { (parent: $0.parentSessionID, title: Optional($0.title), task: String?.none) }
        let saved = summaries.map { (parent: $0.parent, title: $0.title, task: $0.task) }
        return SessionFamily.isStarted(proposal, parent: session.sessionID, children: opened + saved)
    }

    /// Engancha la nota al padre. Vale para las hijas recién empezadas y para las que se reabren.
    func watchFamily(_ session: LiveSession) {
        session.onTurnFinished = { [weak self, weak session] outcome in
            guard let self, let session else { return }
            self.childFinished(session, outcome)
        }
        // «Seguir en una sesión nueva» (aviso de tarifa): se abre la que sigue con el resumen.
        session.onContinued = { [weak self] id in
            guard let self else { return }
            Task {
                await self.reload()
                self.route = .past(id)
            }
        }
    }

    // MARK: - Métodos privados

    private func childFinished(_ child: LiveSession, _ outcome: Outcome) {
        // Un turno que cortó el usuario no avisa: lo está mirando él.
        guard !["cortado", "interrupted"].contains(outcome.state),
              let parentID = child.parentSessionID
                ?? child.sessionID.flatMap({ id in summaries.first { $0.id == id }?.parent }) else { return }
        let note = SessionFamily.note(title: child.title, state: outcome.state, recap: outcome.wrapUp?.recap,
                                      answer: child.lastAnswer)
        if let parent = live.first(where: { $0.sessionID == parentID }) ?? dormant[parentID] {
            parent.deliver(note)
            return
        }
        // El padre no está abierto: se carga su historial sin cambiar de pantalla y se le entrega.
        guard let summary = summaries.first(where: { $0.id == parentID }) else { return }
        openPast(summary)
        Task {
            for _ in 0..<40 {
                if let parent = dormant[parentID], parent.isDormant {
                    parent.deliver(note)
                    return
                }
                try? await Task.sleep(nanoseconds: 250_000_000)
            }
        }
    }
}
