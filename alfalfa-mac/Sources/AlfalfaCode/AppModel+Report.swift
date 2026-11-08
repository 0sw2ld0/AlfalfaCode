import AlfalfaKit
import Foundation

/// «Reportar un error» (ReportView).
extension AppModel {

    // MARK: - Métodos

    /// Manda un reporte de error con `alfalfa --reportar`. El motor lo envía al servicio Alfalfa
    /// (firmado si hay código); si no puede, la respuesta dice a qué correo escribir.
    func report(text: String, contact: String, diagnostics: Bool) async -> ReportResult {
        guard let launcher else { return ReportResult(sent: false, reason: "AlfalfaCode no encontró su motor.") }
        var request: [String: Any] = ["texto": text, "diagnostico": diagnostics]
        if !contact.isEmpty { request["contacto"] = contact }
        if diagnostics, let lastError = providersError, !lastError.isEmpty {
            request["registro"] = "Último error: " + lastError
        }
        guard let data = try? JSONSerialization.data(withJSONObject: request),
              let line = String(data: data, encoding: .utf8) else { return ReportResult(sent: false) }
        do {
            let messages = try await AlfalfaProcess.query(launcher: launcher, arguments: ["--reportar", "--json"],
                                                          directory: FileManager.default.homeDirectoryForCurrentUser,
                                                          input: line)
            for message in messages {
                if case let .reported(result) = message { return result }
                if case let .error(text) = message { return ReportResult(sent: false, reason: text) }
            }
            return ReportResult(sent: false, reason: "El motor no dijo si el reporte salió.")
        } catch {
            return ReportResult(sent: false, reason: error.localizedDescription)
        }
    }
}
