import Darwin
import Foundation

enum BrowserActivityFileError: Error {
    case unavailable, invalid
}

/// Lee únicamente lo que escribió el motor para la sesión elegida, sin seguir enlaces.
public struct BrowserActivityStore: Hashable, Sendable {

    // MARK: - Constantes

    private static let historyLimit = 512 * 1024
    private static let imageLimit = 8 * 1024 * 1024

    // MARK: - Propiedades privadas

    private let home: URL
    private let components: [String]

    // MARK: - Inicializadores

    public init?(home: URL, sessionID: String) {
        guard home.isFileURL, sessionID.count <= 128,
              sessionID.range(of: #"\A[A-Za-z0-9_-]{1,128}\z"#, options: .regularExpression) != nil else { return nil }
        self.home = home.standardizedFileURL
        self.components = ["sesiones", sessionID, "native", sessionID]
    }

    // MARK: - Métodos públicos

    public func load() throws -> BrowserActivityLog {
        let parts = components + ["actividad-navegador.json"]
        guard let data = try read(parts, limit: Self.historyLimit, privateFile: true) else { return .empty }
        return try JSONDecoder().decode(BrowserActivityLog.self, from: data).validated()
    }

    public func capture(for entry: BrowserActivityEntry) -> Data? {
        capture(for: entry, limit: Self.imageLimit)
    }

    // MARK: - Métodos internos

    func capture(for entry: BrowserActivityEntry, limit: Int) -> Data? {
        let maximum = min(limit, Self.imageLimit)
        guard maximum > 0, let name = entry.captureName,
              let data = try? read(components + ["capturas", name], limit: maximum, privateFile: false),
              Self.isPNG(data) else { return nil }
        return data
    }

    // MARK: - Métodos privados

    private func read(_ parts: [String], limit: Int, privateFile: Bool) throws -> Data? {
        guard let name = parts.last else { throw BrowserActivityFileError.invalid }
        let flags = O_RDONLY | O_DIRECTORY | O_NOFOLLOW
        var directory = open(home.path, flags)
        guard directory >= 0 else { return try Self.missing() }
        defer { close(directory) }
        try Self.checkPrivate(directory)
        for part in parts.dropLast() {
            let next = openat(directory, part, flags)
            guard next >= 0 else { return try Self.missing() }
            close(directory)
            directory = next
            try Self.checkPrivate(directory)
        }
        let file = openat(directory, name, O_RDONLY | O_NOFOLLOW | O_NONBLOCK)
        guard file >= 0 else { return try Self.missing() }
        let handle = FileHandle(fileDescriptor: file, closeOnDealloc: true)
        defer { try? handle.close() }
        var info = stat()
        guard fstat(file, &info) == 0, (info.st_mode & S_IFMT) == S_IFREG, info.st_nlink == 1,
              info.st_uid == getuid(), info.st_size >= 0, info.st_size <= limit,
              !privateFile || (info.st_mode & 0o077) == 0 else { throw BrowserActivityFileError.invalid }
        let data = try handle.read(upToCount: limit + 1) ?? Data()
        guard data.count == info.st_size, data.count <= limit else { throw BrowserActivityFileError.invalid }
        return data
    }

    private static func checkPrivate(_ descriptor: Int32) throws {
        var info = stat()
        guard fstat(descriptor, &info) == 0, info.st_uid == getuid(), (info.st_mode & 0o077) == 0 else {
            throw BrowserActivityFileError.unavailable
        }
    }

    private static func missing() throws -> Data? {
        if errno == ENOENT { return nil }
        throw BrowserActivityFileError.unavailable
    }

    private static func isPNG(_ data: Data) -> Bool {
        let header = Array(data.prefix(24))
        guard header.count == 24, Array(header.prefix(8)) == [137, 80, 78, 71, 13, 10, 26, 10],
              Array(header[12..<16]) == [73, 72, 68, 82] else { return false }
        let width = header[16..<20].reduce(0) { ($0 << 8) | Int($1) }
        let height = header[20..<24].reduce(0) { ($0 << 8) | Int($1) }
        return (1...8192).contains(width) && (1...8192).contains(height) && width * height <= 16_777_216
    }
}
