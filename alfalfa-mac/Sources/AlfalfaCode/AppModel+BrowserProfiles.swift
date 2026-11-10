import AlfalfaKit
import Foundation

extension AppModel {

    // MARK: - Propiedades

    var browserProfiles: BrowserProfiles { .shared }

    /// En una sesión se configura su carpeta, aunque «Nuevo encargo» tenga seleccionada otra.
    var browserProject: URL? {
        switch route {
        case .new: return folder.map { URL(fileURLWithPath: $0) }
        case let .live(id): return session(id)?.cwd
        case let .past(id):
            if let stored = dormant[id] { return stored.cwd }
            return summaries.first(where: { $0.id == id }).flatMap(\.cwd).map { URL(fileURLWithPath: $0) }
        }
    }

    // MARK: - Propiedades privadas

    private var browserBusyMessage: String {
        "Espera a que termine o detén el agente de este proyecto antes de cambiar sus datos del navegador."
    }

    // MARK: - Métodos

    func canChangeBrowserProfile(for project: URL?) -> Bool {
        guard let project, !browserProfiles.isClearing(project) else { return false }
        return !browserSessions(in: project).contains {
            $0.timeline.status.isRunning || $0.browser?.activity != nil || $0.browser?.handoff != nil
        }
    }

    /// Un ajuste de la persona, sin copiar cookies desde el navegador aislado ni desde otras apps.
    func setBrowserProfile(_ mode: BrowserProfileMode, for project: URL) -> String? {
        guard canChangeBrowserProfile(for: project) else { return browserBusyMessage }
        guard browserProfiles.mode(forProject: project) != mode else { return nil }
        do {
            try browserProfiles.setMode(mode, forProject: project)
            closeProjectBrowsers(in: project)
            return nil
        } catch {
            return error.localizedDescription
        }
    }

    func clearBrowserProfile(in project: URL) async -> String? {
        guard canChangeBrowserProfile(for: project) else { return browserBusyMessage }
        closeProjectBrowsers(in: project)
        do {
            try await browserProfiles.clearData(forProject: project)
            // Una vista que la persona abriera mientras se borraba era aislada: se crea de nuevo al usarla.
            closeProjectBrowsers(in: project)
            return nil
        } catch {
            return error.localizedDescription
        }
    }

    // MARK: - Métodos privados

    private func browserSessions(in project: URL) -> [LiveSession] {
        guard let key = BrowserProfiles.projectKey(for: project) else { return [] }
        return (live + Array(dormant.values)).filter { BrowserProfiles.projectKey(for: $0.cwd) == key }
    }

    private func closeProjectBrowsers(in project: URL) {
        for session in browserSessions(in: project) { session.closeBrowser() }
        objectWillChange.send()
    }
}
