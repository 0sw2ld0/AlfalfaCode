import Foundation

/// Lee únicamente lo que escribió el motor para la sesión elegida, sin seguir enlaces.
public struct BrowserActivityStore: Hashable, Sendable {

    // MARK: - Constantes

    private static let historyLimit = 512 * 1024
    private static let imageLimit = 8 * 1024 * 1024

    // MARK: - Propiedades privadas

    private let files: SessionActivityFiles

    public init?(home: URL, sessionID: String) {
        guard let files = SessionActivityFiles(home: home, sessionID: sessionID) else { return nil }
        self.files = files
    }

    // MARK: - Métodos públicos

    public func load() throws -> BrowserActivityLog {
        guard let data = try files.read(["actividad-navegador.json"], limit: Self.historyLimit, privateFile: true) else { return .empty }
        return try JSONDecoder().decode(BrowserActivityLog.self, from: data).validated()
    }

    public func capture(for entry: BrowserActivityEntry) -> Data? {
        capture(for: entry, limit: Self.imageLimit)
    }

    // MARK: - Métodos internos

    func capture(for entry: BrowserActivityEntry, limit: Int) -> Data? {
        let maximum = min(limit, Self.imageLimit)
        guard maximum > 0, let name = entry.captureName else { return nil }
        return files.capture(named: name, limit: maximum)
    }
}
