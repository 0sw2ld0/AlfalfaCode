import AlfalfaKit
import AppKit

/// Copiar datos de una sesión y su carpeta de historial (menú de clic derecho).
extension AppModel {
    func copy(_ text: String) {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(text, forType: .string)
    }

    func resumeCommand(_ row: SidebarRow) -> String {
        "cd \(CodeActionsShell.quote(row.folder ?? "~")) && alfalfa --reanudar \(row.sessionID ?? "")"
    }

    /// Carpeta de la sesión en `$ALFALFA_HOME/sesiones`.
    func sessionFolder(_ row: SidebarRow) -> URL? {
        guard let id = row.sessionID else { return nil }
        return Attachments.directory().deletingLastPathComponent().appendingPathComponent("sesiones/\(id)")
    }
}
