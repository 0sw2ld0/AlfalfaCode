import Foundation

public struct BrainstormParticipant: Codable, Equatable {
    public var model: String
    public var effort: String

    public init(model: String, effort: String) {
        self.model = model
        self.effort = effort
    }
}

public struct BrainstormConfiguration {
    public static let participantRange = 2...6
    public static let maximumTurns = 12
    public static let defaultTurns = 6

    public let limit: Int
    public let participants: [BrainstormParticipant]

    public init(limit: Int, participants: [BrainstormParticipant]) {
        self.limit = limit
        self.participants = participants
    }

    var launchArguments: [String] {
        let encoded = try? JSONEncoder().encode(participants)
        let arguments = ["--lluvia-de-ideas", String(limit)]
        guard let encoded, !participants.isEmpty else { return arguments }
        return arguments + ["--lluvia-participantes", String(decoding: encoded, as: UTF8.self)]
    }
}
