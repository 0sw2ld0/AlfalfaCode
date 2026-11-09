import Foundation

public struct DiffLine: Equatable, Identifiable, Sendable {
    public enum Kind: Sendable { case context, added, removed, meta }
    public var id: Int
    public var kind: Kind
    public var text: String
}

public struct DiffFile: Equatable, Identifiable, Sendable {
    public var path: String
    public var added = 0
    public var removed = 0
    public var lines: [DiffLine] = []
    public var id: String { path }
}

public enum DiffParser {
    /// Interpreta la salida de `git diff`. Conserva como máximo `maxLines` líneas visibles por archivo.
    public static func parse(_ text: String, maxLines: Int = 400) -> [DiffFile] {
        var files: [DiffFile] = []
        var inHunk = false
        for raw in text.split(separator: "\n", omittingEmptySubsequences: false) {
            let line = String(raw)
            if line.hasPrefix("diff --git ") {
                let path = line.split(separator: " ").last.map { String($0.dropFirst(2)) } ?? line
                files.append(DiffFile(path: path))
                inHunk = false
            } else if files.isEmpty {
                continue
            } else if line.hasPrefix("@@") {
                inHunk = true
                append(&files, .meta, line, maxLines)
            } else if inHunk, line.hasPrefix("+") {
                files[files.count - 1].added += 1
                append(&files, .added, line, maxLines)
            } else if inHunk, line.hasPrefix("-") {
                files[files.count - 1].removed += 1
                append(&files, .removed, line, maxLines)
            } else if inHunk, !line.isEmpty {
                append(&files, .context, line, maxLines)
            }
        }
        return files
    }

    private static func append(_ files: inout [DiffFile], _ kind: DiffLine.Kind, _ text: String, _ maxLines: Int) {
        let index = files.count - 1
        guard files[index].lines.count < maxLines else { return }
        files[index].lines.append(DiffLine(id: files[index].lines.count, kind: kind, text: text))
    }
}

public struct GitSnapshot: Equatable, Sendable {
    public var branch: String?
    public var files: [DiffFile]
    public var added: Int { files.reduce(0) { $0 + $1.added } }
    public var removed: Int { files.reduce(0) { $0 + $1.removed } }
    /// false si en este Mac no hay un git que se pueda usar sin instalar nada (ver `Workspace.git`).
    public var gitAvailable = true
}

public enum Workspace {
    /// Ejecuta una orden de solo lectura y devuelve stdout, o nil si falla.
    static func capture(_ executable: String, _ arguments: [String], in directory: URL) async -> String? {
        await withCheckedContinuation { continuation in
            DispatchQueue.global().async {
                let process = Process()
                process.executableURL = URL(fileURLWithPath: executable)
                process.arguments = arguments
                process.currentDirectoryURL = directory
                var env = ProcessInfo.processInfo.environment
                env["GIT_OPTIONAL_LOCKS"] = "0"
                env["GIT_CONFIG_COUNT"] = "1"
                env["GIT_CONFIG_KEY_0"] = "core.fsmonitor"
                env["GIT_CONFIG_VALUE_0"] = "false"
                process.environment = env
                let pipe = Pipe()
                process.standardOutput = pipe
                process.standardError = FileHandle.nullDevice
                process.standardInput = FileHandle.nullDevice
                do { try process.run() } catch { continuation.resume(returning: nil); return }
                let data = pipe.fileHandleForReading.readDataToEndOfFile()
                process.waitUntilExit()
                continuation.resume(returning: process.terminationStatus == 0 ? String(decoding: data, as: UTF8.self) : nil)
            }
        }
    }

    /// Si están las herramientas de línea de órdenes de Apple (o Xcode). Sin ellas, `/usr/bin/git`
    /// es sólo un atajo que abre el diálogo de macOS para instalarlas. `xcode-select -p` no lo abre.
    public static let appleToolsInstalled: Bool = {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/xcode-select")
        process.arguments = ["-p"]
        process.standardOutput = FileHandle.nullDevice
        process.standardError = FileHandle.nullDevice
        guard (try? process.run()) != nil else { return false }
        process.waitUntilExit()
        return process.terminationStatus == 0
    }()

    /// El git que se puede usar sin que macOS pida instalar nada: `/usr/bin/git` si están las
    /// herramientas de Apple; si no, otro del PATH (Homebrew, por ejemplo); si no hay, nil.
    public static func git(environment: [String: String] = ProcessInfo.processInfo.environment,
                           appleTools: Bool = appleToolsInstalled,
                           isExecutable: (String) -> Bool = FileManager.default.isExecutableFile(atPath:)) -> String? {
        if appleTools { return "/usr/bin/git" }
        let path = (environment["PATH"] ?? "").split(separator: ":").map(String.init)
        for directory in path + ["/opt/homebrew/bin", "/usr/local/bin"] where !directory.isEmpty && directory != "/usr/bin" {
            let candidate = (directory as NSString).appendingPathComponent("git")
            if isExecutable(candidate) { return candidate }
        }
        return nil
    }

    public static func snapshot(of directory: URL, environment: [String: String] = ProcessInfo.processInfo.environment) async -> GitSnapshot {
        guard let git = git(environment: environment) else {
            var empty = GitSnapshot(branch: nil, files: [])
            empty.gitAvailable = false
            return empty
        }
        let branch = await capture(git, ["rev-parse", "--abbrev-ref", "HEAD"], in: directory)?
            .trimmingCharacters(in: .whitespacesAndNewlines)
        let diff = await capture(git, ["diff", "--no-ext-diff", "--no-textconv", "--no-color"], in: directory)
        // La redacción va antes de interpretar el diff: ninguna línea expuesta contiene la clave.
        let clean = Redactor.clean(diff ?? "", environment: environment)
        return GitSnapshot(branch: branch?.isEmpty == false ? branch : nil, files: DiffParser.parse(clean))
    }

    public static func ollamaURL(environment: [String: String] = ProcessInfo.processInfo.environment) -> URL {
        URL(string: environment["ALFALFA_OLLAMA_URL"] ?? "http://127.0.0.1:11434") ?? URL(string: "http://127.0.0.1:11434")!
    }

    /// Comprueba si Ollama responde. No envía credenciales.
    public static func ollamaReachable(at base: URL = ollamaURL()) async -> Bool {
        var request = URLRequest(url: base.appendingPathComponent("api/tags"), timeoutInterval: 3)
        request.httpMethod = "GET"
        guard let (_, response) = try? await URLSession.shared.data(for: request),
              let http = response as? HTTPURLResponse else { return false }
        return http.statusCode == 200
    }
}
