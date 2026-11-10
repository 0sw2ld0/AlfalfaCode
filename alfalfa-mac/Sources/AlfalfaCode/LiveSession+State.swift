import Foundation

extension LiveSession {
    var sessionID: String? { timeline.session?.id ?? resumeID }

    /// Se puede escribir mientras hay proceso o la sesión puede despertarse con otro mensaje.
    var canSend: Bool { timeline.status.isActive || isDormant }

    static func newQueueID() -> String { UUID().uuidString.lowercased() }
}
