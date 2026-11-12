import Foundation

/// Lo que se puede hacer con un bloque de código del agente: copiarlo siempre y,
/// si es de terminal, ejecutarlo en Terminal.app dentro de la carpeta de la sesión.
public enum CodeActions {
    static let shellLanguages: Set<String> = ["bash", "sh", "zsh", "shell", "console", "terminal", "fish"]

    public static func isRunnable(language: String?, code: String) -> Bool {
        guard let language, shellLanguages.contains(language.lowercased()) else { return false }
        return !command(from: code).isEmpty
    }

    /// La orden sin el `$ ` de los ejemplos ni las líneas vacías del final.
    public static func command(from code: String) -> String {
        code.components(separatedBy: "\n")
            .map { line in line.hasPrefix("$ ") ? String(line.dropFirst(2)) : line }
            .joined(separator: "\n")
            .trimmingCharacters(in: .whitespacesAndNewlines)
    }

    /// AppleScript que abre una ventana de Terminal en `folder` y escribe la orden.
    public static func terminalScript(command: String, folder: String) -> String {
        let shell = "cd " + shellQuote(folder) + " && " + command
        return "tell application \"Terminal\"\n    activate\n    do script \"\(appleScriptEscape(shell))\"\nend tell"
    }

    static func shellQuote(_ text: String) -> String { "'" + text.replacingOccurrences(of: "'", with: "'\\''") + "'" }

    static func appleScriptEscape(_ text: String) -> String {
        text.replacingOccurrences(of: "\\", with: "\\\\").replacingOccurrences(of: "\"", with: "\\\"")
    }
}

/// Imágenes y archivos adjuntos a un mensaje. El arnés copia las imágenes reducidas a la sesión
/// y de los archivos (PDF, texto, código, Word, Excel) extrae su texto (alfalfa/adjuntos.py).
/// La app guarda unas y otros en la misma lista; al enviar se separan por su extensión.
public enum Attachments {
    public static let extensions: Set<String> = ["png", "jpg", "jpeg", "gif", "webp", "heic", "heif", "tif", "tiff", "bmp"]
    /// Lo mismo que `alfalfa.adjuntos.TIPOS`.
    public static let fileExtensions: Set<String> = [
        "pdf", "docx", "xlsx",
        "txt", "text", "md", "markdown", "rst", "adoc", "org", "tex", "log", "csv", "tsv", "json", "jsonl", "ndjson",
        "yaml", "yml", "toml", "ini", "cfg", "conf", "xml", "html", "htm", "css", "scss", "less", "svg",
        "py", "pyi", "ipynb", "js", "mjs", "cjs", "jsx", "ts", "tsx", "vue", "svelte", "swift", "m", "mm", "h", "hpp",
        "c", "cc", "cpp", "cxx", "cs", "java", "kt", "kts", "scala", "go", "rs", "rb", "php", "pl", "lua", "r",
        "jl", "dart", "ex", "exs", "erl", "hs", "clj", "sql", "graphql", "proto", "sh", "bash", "zsh", "fish",
        "ps1", "bat", "gradle", "cmake", "mk", "dockerfile", "tf", "hcl", "nix", "diff", "patch", "srt", "vtt",
    ]
    static let fileNames: Set<String> = ["dockerfile", "makefile", "readme", "license", "gemfile", "procfile", "justfile"]
    /// Ocho imágenes y ocho archivos por mensaje, como en el arnés.
    public static let maximum = 8

    public static func isImage(_ url: URL) -> Bool { url.isFileURL && extensions.contains(url.pathExtension.lowercased()) }

    /// PDF, texto, código, Word o Excel: no hace falta que el modelo vea imágenes.
    public static func isFile(_ url: URL) -> Bool {
        guard url.isFileURL else { return false }
        let ext = url.pathExtension.lowercased()
        return ext.isEmpty ? fileNames.contains(url.lastPathComponent.lowercased()) : fileExtensions.contains(ext)
    }

    public static func isAttachable(_ url: URL) -> Bool { isImage(url) || isFile(url) }

    /// Las rutas de imágenes (`imagenes`) y las de archivos (`archivos`), en su orden.
    public static func partition(_ paths: [String]) -> (images: [String], files: [String]) {
        let urls = paths.map { URL(fileURLWithPath: $0) }
        return (urls.filter(isImage).map(\.path), urls.filter { !isImage($0) }.map(\.path))
    }

    /// El texto de un mensaje que sólo trae adjuntos, como `SOLO_IMAGENES` y `SOLO_ARCHIVOS` del motor.
    public static func placeholder(for paths: [String]) -> String {
        partition(paths).images.isEmpty ? "Lee el archivo adjunto." : "Mira la imagen adjunta."
    }

    /// Guarda una imagen pegada (PNG) en la carpeta de adjuntos y devuelve su ruta.
    public static func store(png data: Data, in directory: URL) throws -> URL {
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true,
                                                attributes: [.posixPermissions: 0o700])
        let url = directory.appendingPathComponent("pegada-\(UUID().uuidString.prefix(8)).png")
        try data.write(to: url, options: .atomic)
        return url
    }

    /// `$ALFALFA_HOME/adjuntos`, o `~/.alfalfa/adjuntos`.
    public static func directory(environment: [String: String] = ProcessInfo.processInfo.environment) -> URL {
        let home = environment["ALFALFA_HOME"].map { URL(fileURLWithPath: $0) }
            ?? FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".alfalfa")
        return home.appendingPathComponent("adjuntos")
    }
}

/// Las etiquetas `<image name=[Image #N] path="...">` y `<file name="…" path="…">` que el arnés
/// añade al texto.
public enum ImageTags {
    static let imagePattern = #"<image name=\[Image #\d+\] path="([^"\n]+)"></image>\n?"#
    static let filePattern = #"<file name="[^"\n]*" path="([^"\n]+)"></file>\n?"#

    /// Rutas de las imágenes, de los archivos, de todos los adjuntos (en el orden en que van) y
    /// el texto sin etiquetas.
    public static func split(_ text: String) -> (images: [String], files: [String], attachments: [String], text: String) {
        func paths(_ pattern: String, in text: String) -> (matches: [String], clean: String) {
            guard let regex = try? NSRegularExpression(pattern: pattern) else { return ([], text) }
            let range = NSRange(text.startIndex..., in: text)
            let found = regex.matches(in: text, range: range).compactMap { match in
                Range(match.range(at: 1), in: text).map { String(text[$0]) }
            }
            return (found, regex.stringByReplacingMatches(in: text, range: range, withTemplate: ""))
        }
        let files = paths(filePattern, in: text)
        let images = paths(imagePattern, in: files.clean)
        return (images.matches, files.matches, images.matches + files.matches,
                images.clean.trimmingCharacters(in: .whitespacesAndNewlines))
    }

    /// Texto con etiquetas, como lo escriben `alfalfa.imagenes.con_etiquetas` y
    /// `alfalfa.adjuntos.con_etiquetas`. En `images` pueden ir también archivos: van con la suya.
    public static func join(_ text: String, images: [String]) -> String {
        let parts = Attachments.partition(images)
        let files = parts.files.map { path in
            let name = URL(fileURLWithPath: path).lastPathComponent
                .replacingOccurrences(of: #"["<>\n\r]"#, with: "_", options: .regularExpression)
            return "<file name=\"\(name)\" path=\"\(path)\"></file>"
        }
        let tags = parts.images.enumerated().map { "<image name=[Image #\($0.offset + 1)] path=\"\($0.element)\"></image>" }
        return (files + tags + [text]).filter { !$0.isEmpty }.joined(separator: "\n")
    }
}

/// Una imagen o un archivo soltado sobre el cuadro de texto: el campo de AppKit lo recibe antes
/// que la app y escribe su ruta. Como en Codex, esa ruta se quita del texto y se adjunta.
public enum DroppedImages {
    /// Si lo que se acaba de insertar (`old` → `new`) son sólo rutas de imágenes o archivos
    /// admitidos que existen, el texto sin ellas y los adjuntos; si no (se escribió o pegó otra cosa), nil.
    public static func extract(old: String, new: String,
                               exists: (URL) -> Bool = { FileManager.default.fileExists(atPath: $0.path) })
        -> (text: String, images: [URL])? {
        let before = Array(old), after = Array(new)
        guard after.count > before.count else { return nil }
        var prefix = 0
        while prefix < before.count, before[prefix] == after[prefix] { prefix += 1 }
        var suffix = 0
        while suffix < before.count - prefix, before[before.count - 1 - suffix] == after[after.count - 1 - suffix] {
            suffix += 1
        }
        let inserted = String(after[prefix..<(after.count - suffix)])
        guard let images = images(in: inserted, exists: exists) else { return nil }
        var head = String(after[..<prefix]), tail = String(after[(after.count - suffix)...])
        if head.hasSuffix(" ") && tail.hasPrefix(" ") { tail.removeFirst() }
        if tail.isEmpty { while head.hasSuffix(" ") { head.removeLast() } }
        return (head + tail, images)
    }

    /// Una o varias rutas (con espacios, entre comillas, con `\ ` o como file://).
    static func images(in chunk: String, exists: (URL) -> Bool) -> [URL]? {
        let text = chunk.trimmingCharacters(in: .whitespacesAndNewlines)
        guard text.count > 1 else { return nil }
        func url(_ raw: String) -> URL? {
            var value = raw.trimmingCharacters(in: .whitespacesAndNewlines)
            if value.count >= 2, let first = value.first, first == value.last, first == "\"" || first == "'" {
                value = String(value.dropFirst().dropLast())
            }
            if value.hasPrefix("file://") { return URL(string: value).flatMap { $0.isFileURL ? $0 : nil } }
            value = value.replacingOccurrences(of: "\\ ", with: " ")
            if value.hasPrefix("~/") { value = NSHomeDirectory() + value.dropFirst() }
            guard value.hasPrefix("/") else { return nil }
            return URL(fileURLWithPath: value)
        }
        func valid(_ parts: [String]) -> [URL]? {
            let urls = parts.map(url)
            guard !urls.isEmpty, urls.allSatisfy({ $0.map { Attachments.isAttachable($0) && exists($0) } ?? false }) else { return nil }
            return urls.compactMap { $0 }
        }
        // Una sola ruta, aunque tenga espacios («Captura de pantalla 2026….png»).
        if let one = valid([text]) { return one }
        if let lines = valid(text.components(separatedBy: .newlines).filter { !$0.trimmingCharacters(in: .whitespaces).isEmpty }) {
            return lines
        }
        // Varias en una línea: se separan por un espacio seguido de «/» o de «file://».
        let pieces = text.replacingOccurrences(of: " file://", with: "\n file://")
            .replacingOccurrences(of: "(?<!\\\\) (?=/)", with: "\n", options: .regularExpression)
            .components(separatedBy: "\n")
        return valid(pieces)
    }
}
