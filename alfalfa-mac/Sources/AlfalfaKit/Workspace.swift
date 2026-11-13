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

    /// La lista completa de `git diff --numstat` con las líneas del diff (recortado) que haya de
    /// cada archivo: así los totales y el número de archivos son los de verdad aunque el diff no
    /// se haya leído entero. Sin filas válidas, `detailed` tal cual.
    public static func merge(numstat: String, detailed: [DiffFile]) -> [DiffFile] {
        let byPath = Dictionary(detailed.map { ($0.path, $0) }, uniquingKeysWith: { first, _ in first })
        var files: [DiffFile] = []
        for row in numstat.split(separator: "\n") {
            let parts = row.split(separator: "\t", maxSplits: 2, omittingEmptySubsequences: false)
            guard parts.count == 3 else { continue }
            let path = String(parts[2])
            var file = byPath[path] ?? DiffFile(path: path)
            file.added = Int(parts[0]) ?? 0  // «-» en los binarios
            file.removed = Int(parts[1]) ?? 0
            files.append(file)
        }
        return files.isEmpty ? detailed : files
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
    /// Lo que se lee de `git diff` para el panel. Un repositorio con miles de archivos cambiados
    /// daba más de 100 MB: leerlo y convertirlo a texto en cada refresco dejaba la app al 30 % de CPU.
    public static let diffLimit = 1_000_000

    /// Ejecuta una orden de solo lectura y devuelve stdout, o nil si falla.
    static func capture(_ executable: String, _ arguments: [String], in directory: URL) async -> String? {
        await capture(executable, arguments, in: directory, limit: nil)?.text
    }

    /// Como `capture`, pero deja de leer y termina la orden al pasar de `limit` bytes; entonces
    /// el texto llega hasta el último salto de línea leído y `truncated` es true.
    static func capture(_ executable: String, _ arguments: [String], in directory: URL,
                        limit: Int?) async -> (text: String, truncated: Bool)? {
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
                let handle = pipe.fileHandleForReading
                var data = Data()
                var truncated = false
                while true {
                    let chunk = handle.availableData
                    if chunk.isEmpty { break }
                    data.append(chunk)
                    if let limit, data.count > limit {
                        truncated = true
                        process.terminate()
                        break
                    }
                }
                try? handle.close()
                process.waitUntilExit()
                if truncated {
                    // Hasta la última línea completa: nunca media línea ni medio carácter.
                    if let end = data.lastIndex(of: UInt8(ascii: "\n")) { data = data[..<end] }
                    continuation.resume(returning: (String(decoding: data, as: UTF8.self), true))
                } else {
                    continuation.resume(returning: process.terminationStatus == 0
                                        ? (String(decoding: data, as: UTF8.self), false) : nil)
                }
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
        let diff = await capture(git, ["diff", "--no-ext-diff", "--no-textconv", "--no-color"], in: directory,
                                 limit: diffLimit)
        // La redacción va antes de interpretar el diff: ninguna línea expuesta contiene la clave.
        let clean = Redactor.clean(diff?.text ?? "", environment: environment)
        var files = DiffParser.parse(clean)
        if diff?.truncated == true,
           let numstat = await capture(git, ["diff", "--numstat", "--no-ext-diff", "--no-textconv", "--no-color"],
                                       in: directory) {
            files = DiffParser.merge(numstat: Redactor.clean(numstat, environment: environment), detailed: files)
        }
        return GitSnapshot(branch: branch?.isEmpty == false ? branch : nil, files: files)
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
