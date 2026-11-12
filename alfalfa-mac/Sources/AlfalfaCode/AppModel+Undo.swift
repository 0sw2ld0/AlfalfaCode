import AlfalfaKit
import Foundation

/// Volver a un mensaje con la conversación, el código o los dos, y rehacer el código
/// (`alfalfa --volver` y `--rehacer`, con su vista previa).
extension AppModel {

    /// Qué haría volver al mensaje del turno `turn` (o rehacer, con `redo`), sin tocar nada.
    /// Puede hacerse con el proceso parado entre turnos: sólo lee la sesión.
    func undoPreview(sessionID id: String, turn: String?,
                     redo: String? = nil) async -> Result<UndoPreview, UndoFailure> {
        guard let launcher else { return .failure(UndoFailure("No se encuentra la orden alfalfa.")) }
        var arguments = redo == nil ? ["--volver", id, "--turno", turn ?? ""] : ["--rehacer", id]
        if let redo, !redo.isEmpty { arguments += ["--deshecho", redo] }
        do {
            let messages = try await AlfalfaProcess.query(launcher: launcher,
                                                          arguments: arguments + ["--vista-previa", "--json"],
                                                          directory: home)
            for case let .undoPreview(preview) in messages { return .success(preview) }
            for case let .error(text) in messages { return .failure(UndoFailure(text)) }
            return .failure(UndoFailure("alfalfa no dijo qué se restauraría."))
        } catch {
            return .failure(UndoFailure(error.localizedDescription))
        }
    }

    /// Volver al mensaje del turno `turn` con `scope`. `previous` es el turno anterior (nil si es
    /// el primero) y `draft` el texto del mensaje, que queda en el cuadro para cambiarlo. `force`:
    /// archivos que cambiaron después y se restauran igualmente porque lo pediste.
    func goBack(session: LiveSession, turn: String, previous: String?, draft: String, scope: UndoScope,
                force: [String]) {
        guard let id = session.sessionID else { return }
        guard scope.touchesCode else {
            if let previous {
                fork(sessionID: id, turn: previous, draft: draft)
            } else {
                restart(in: session.cwd, with: draft)
            }
            return
        }
        // Cerrar el proceso cerraría también un servidor o un juego que sigue abierto.
        guard session.timeline.liveProcesses.isEmpty else {
            sessionError = "Hay procesos en segundo plano abiertos en esta sesión; "
                + "detenlos antes de volver con el código."
            return
        }
        Task {
            // Como al bifurcar: el proceso abierto entre turnos guarda el cerrojo y se cierra antes.
            if !(await session.releaseProcess()) {
                sessionError = "La sesión está trabajando; espera a que termine el turno para volver."
                return
            }
            var arguments = ["--volver", id, "--turno", turn, "--alcance", scope.rawValue, "--json"]
            for path in force { arguments += ["--forzar", path] }
            guard let messages = await manage(arguments) else { return }
            for case let .undone(step) in messages {
                if step.session == id || step.session == nil { session.addUndo(step) }
                if step.newConversation { restart(in: session.cwd, with: draft) }
            }
            for case let .sessionChanged(newID, _) in messages {
                pendingDrafts[newID] = draft
                route = .past(newID)
            }
        }
    }

    /// Devuelve lo que quitó el deshacer `step` de la sesión. Lo que cambiaste después sólo se
    /// pisa si está en `force`.
    func redo(session: LiveSession, step: String, force: [String]) async {
        guard let id = session.sessionID else { return }
        guard session.timeline.liveProcesses.isEmpty else {
            sessionError = "Hay procesos en segundo plano abiertos en esta sesión; detenlos antes de rehacer."
            return
        }
        if !(await session.releaseProcess()) {
            sessionError = "La sesión está trabajando; espera a que termine el turno para rehacer."
            return
        }
        var arguments = ["--rehacer", id, "--deshecho", step, "--json"]
        for path in force { arguments += ["--forzar", path] }
        guard let messages = await manage(arguments) else { return }
        for case let .undone(done) in messages { session.addUndo(done) }
    }
}
