import Foundation

extension LiveSession {
    var sessionID: String? { timeline.session?.id ?? resumeID }

    /// Se puede escribir mientras hay proceso o la sesión puede despertarse con otro mensaje; no mientras otro
    /// proceso la tiene en marcha (serían dos sobre la misma sesión).
    var canSend: Bool { timeline.status.isActive || (isDormant && !runningElsewhere) }

    static func newQueueID() -> String { UUID().uuidString.lowercased() }
}
