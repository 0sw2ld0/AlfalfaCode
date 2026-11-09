import Foundation

/// Qué hacer con el stdin de un proceso `alfalfa --stdio` cuando el usuario abandona su sesión.
public enum ReleaseAction: Equatable, Sendable {
    case closeNow
    case cancelAndClose
    case closeWhenParked
}

public enum ReleasePolicy {
    public static func onLeave(status: SessionStatus) -> ReleaseAction {
        switch status {
        case .idle, .blocked: return .closeNow
        case .waitingPermission: return .cancelAndClose
        default: return .closeWhenParked
        }
    }

    /// Con la salida ya pedida y el turno en curso: qué hacer tras cada mensaje del proceso.
    /// Un permiso que llega después de abandonar la sesión nadie lo va a responder: se cancela y se cierra.
    public static func onUpdate(status: SessionStatus) -> ReleaseAction? {
        switch status {
        case .waitingPermission: return .cancelAndClose
        case .idle, .blocked: return .closeNow
        default: return nil
        }
    }

    /// El proceso está libre: sin turno ni permiso en curso.
    public static func isParked(_ status: SessionStatus) -> Bool { status == .idle || status == .blocked }
}
