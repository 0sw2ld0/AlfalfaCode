import AlfalfaKit
import Foundation

/// Ajustes ▸ Almacenamiento: `alfalfa --espacio-sesiones` y `alfalfa --liberar-sesiones`.
extension AppModel {

    // MARK: - Propiedades

    /// Las sesiones abiertas en la app: nunca se ofrecen para liberar, aunque ahora no tengan
    /// el cerrojo (una dormida lo suelta entre turnos y su próximo mensaje fallaría).
    var openSessionIDs: Set<String> { Set(live.compactMap(\.sessionID)) }

    // MARK: - Métodos públicos

    func sessionStorage() async -> (storage: SessionStorage?, error: String?) {
        switch await storageQuery(["--espacio-sesiones"]) {
        case let .success(messages):
            for case let .sessionStorage(storage) in messages { return (storage, nil) }
            for case let .error(text) in messages { return (nil, text) }
            return (nil, "alfalfa no devolvió las sesiones guardadas.")
        case let .failure(error): return (nil, error.localizedDescription)
        }
    }

    /// Mueve `ids` a la Papelera (el usuario ya lo confirmó en la app). Las abiertas se quitan antes;
    /// las que estén en uso las salta el motor.
    func freeSessions(_ ids: Set<String>) async -> (freed: SessionsFreed?, error: String?) {
        let wanted = ids.subtracting(openSessionIDs).sorted()
        guard !wanted.isEmpty else { return (nil, "No hay ninguna sesión que se pueda mover.") }
        let result = await storageQuery(["--liberar-sesiones"] + wanted + ["--si"])
        await reload()
        switch result {
        case let .success(messages):
            for case let .sessionsFreed(freed) in messages { return (freed, nil) }
            for case let .error(text) in messages { return (nil, text) }
            return (nil, "alfalfa no dijo qué sesiones movió.")
        case let .failure(error): return (nil, error.localizedDescription)
        }
    }

    // MARK: - Métodos privados

    private func storageQuery(_ arguments: [String]) async -> Result<[AlfalfaMessage], Error> {
        guard let launcher else { return .failure(AlfalfaError.failed("No se encuentra la orden alfalfa.")) }
        do {
            return .success(try await AlfalfaProcess.query(launcher: launcher, arguments: arguments + ["--json"],
                                                           directory: home))
        } catch {
            return .failure(error)
        }
    }
}
