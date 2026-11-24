import Foundation

public struct BrainstormParticipant: Codable, Equatable, Sendable {
    public var model: String
    public var effort: String

    public var isClaudeCode: Bool { model.hasPrefix("claude-code/") }
    public var replacingClaudeCode: Self {
        let replacement = switch model {
        case "claude-code/haiku": "anthropic/claude-haiku-5-5"
        case "claude-code/sonnet": "anthropic/claude-sonnet-5-5"
        case "claude-code/opus": "anthropic/claude-opus-5-5"
        default: model
        }
        return Self(model: replacement, effort: effort)
    }

    public init(model: String, effort: String) {
        self.model = model
        self.effort = effort
    }
}

public struct BrainstormConfiguration: Codable, Equatable, Sendable {
    public static let participantRange = 2...6
    public static let maximumTurns = 12
    public static let defaultTurns = 6

    public let limit: Int
    public let participants: [BrainstormParticipant]

    public init(limit: Int, participants: [BrainstormParticipant]) {
        self.limit = limit
        self.participants = participants
    }

    public static func participantName(_ index: Int) -> String {
        switch index {
        case 0: return "Propuesta"
        case 1: return "Contraste"
        default: return "Participante \(index + 1)"
        }
    }

    var launchArguments: [String] {
        let encoded = try? JSONEncoder().encode(participants)
        let arguments = ["--lluvia-de-ideas", String(limit)]
        guard let encoded, !participants.isEmpty else { return arguments }
        return arguments + ["--lluvia-participantes", String(decoding: encoded, as: UTF8.self)]
    }
}
