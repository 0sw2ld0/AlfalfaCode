import Foundation

/// Bloque de Markdown del texto del agente. Cubre lo que pinta la TUI de Codex
/// (codex-rs/tui/src/markdown_render.rs): títulos, citas, listas anidadas y de
/// tareas, código con lenguaje, líneas separadoras y tablas GFM. Además, como la app de
/// Codex, las imágenes y vídeos locales que el agente cita en su propia línea.
public enum MarkdownBlock: Equatable, Sendable {
    case paragraph(String)
    case heading(Int, String)
    case bullet(String, level: Int = 0)
    case numbered(Int, String, level: Int = 0)
    case task(Bool, String, level: Int = 0)
    case quote(String)
    case code(String, language: String? = nil)
    case table(header: [String], rows: [[String]])
    case rule
    /// Una o varias imágenes o vídeos locales seguidos, cada uno en su línea o en la misma.
    case media([MarkdownMedia])
}

/// Imagen o vídeo local citado con `![texto](ruta)`: el texto va de pie de foto.
public struct MarkdownMedia: Equatable, Sendable {
    public enum Kind: Equatable, Sendable { case image, video }

    public var alt: String
    /// La ruta tal como la escribió el agente.
    public var source: String
    /// El archivo, ya resuelto, que existía al leer el texto.
    public var url: URL
    public var kind: Kind

    public init(alt: String, source: String, url: URL, kind: Kind) {
        self.alt = alt
        self.source = source
        self.url = url
        self.kind = kind
    }

    public static let imageExtensions: Set<String> = ["png", "jpg", "jpeg", "heic", "heif", "webp", "gif",
                                                      "tif", "tiff", "bmp"]
    public static let videoExtensions: Set<String> = ["mp4", "mov", "m4v"]

    public static func kind(ofExtension ext: String) -> Kind? {
        let ext = ext.lowercased()
        if imageExtensions.contains(ext) { return .image }
        if videoExtensions.contains(ext) { return .video }
        return nil
    }

    /// El archivo de una ruta de Markdown, si es local, de un formato que se muestra y existe:
    /// absoluta, `~/`, `file://` o relativa a la carpeta de la sesión. Una URL web nunca: no se
    /// descarga nada.
    public static func resolve(_ source: String, folder: URL?,
                               fileExists: (String) -> Bool = { FileManager.default.fileExists(atPath: $0) })
        -> URL? {
        var path = source.trimmingCharacters(in: .whitespaces)
        guard !path.isEmpty else { return nil }
        if path.lowercased().hasPrefix("file://") {
            guard let url = URL(string: path), url.isFileURL else { return nil }
            path = url.path
        } else if hasScheme(path) {
            return nil
        }
        var candidates = [path]
        if let decoded = path.removingPercentEncoding, decoded != path { candidates.append(decoded) }
        for candidate in candidates {
            let url: URL
            if candidate.hasPrefix("/") {
                url = URL(fileURLWithPath: candidate)
            } else if candidate == "~" || candidate.hasPrefix("~/") {
                url = URL(fileURLWithPath: NSString(string: candidate).expandingTildeInPath)
            } else if let folder {
                url = folder.appendingPathComponent(candidate)
            } else {
                continue
            }
            let file = url.standardizedFileURL
            guard kind(ofExtension: file.pathExtension) != nil, fileExists(file.path) else { continue }
            return file
        }
        return nil
    }

    /// `http:`, `data:`, `mailto:`…: un esquema antes de la primera barra.
    private static func hasScheme(_ path: String) -> Bool {
        guard let colon = path.firstIndex(of: ":") else { return false }
        let scheme = path[..<colon]
        guard let first = scheme.first, first.isLetter else { return false }
        return scheme.allSatisfy { $0.isLetter || $0.isNumber || "+-.".contains($0) }
    }
}

public enum MarkdownText {
    /// Divide el texto en bloques. El formato en línea (negrita, código, enlaces) se resuelve con `inline`.
    /// Las rutas relativas de las imágenes se buscan en `folder`, la carpeta de la sesión.
    public static func blocks(_ text: String, folder: URL? = nil,
                              fileExists: (String) -> Bool = { FileManager.default.fileExists(atPath: $0) })
        -> [MarkdownBlock] {
        var result: [MarkdownBlock] = []
        /// La línea anterior era de imágenes: las seguidas, sin línea en blanco, van juntas.
        var mediaRun = false
        var paragraph: [String] = []
        var quote: [String] = []
        let lines = text.components(separatedBy: "\n")
        var index = 0

        func flushParagraph() {
            if !paragraph.isEmpty { result.append(.paragraph(paragraph.joined(separator: "\n"))); paragraph = [] }
        }
        func flushQuote() {
            if !quote.isEmpty { result.append(.quote(quote.joined(separator: "\n"))); quote = [] }
        }
        func flush() { flushParagraph(); flushQuote() }

        while index < lines.count {
            let line = lines[index]
            let trimmed = line.trimmingCharacters(in: .whitespaces)
            index += 1
            let continuesMedia = mediaRun
            mediaRun = false

            if let fence = fenceMarker(trimmed) {
                flush()
                let language = String(trimmed.dropFirst(fence.count)).trimmingCharacters(in: .whitespaces)
                var code: [String] = []
                while index < lines.count, !lines[index].trimmingCharacters(in: .whitespaces).hasPrefix(fence) {
                    code.append(lines[index])
                    index += 1
                }
                index += 1  // la valla de cierre, si existe
                result.append(.code(code.joined(separator: "\n"), language: language.isEmpty ? nil : language))
                continue
            }
            if trimmed.isEmpty { flush(); continue }
            if let items = mediaLine(trimmed, folder: folder, fileExists: fileExists) {
                flush()
                if continuesMedia, case let .media(previous) = result.last {
                    result[result.count - 1] = .media(previous + items)
                } else {
                    result.append(.media(items))
                }
                mediaRun = true
                continue
            }
            if trimmed.hasPrefix(">") {
                flushParagraph()
                var body = trimmed.dropFirst()
                if body.hasPrefix(" ") { body = body.dropFirst() }
                quote.append(String(body))
                continue
            }
            if let (level, title) = heading(trimmed) {
                flush()
                result.append(.heading(level, title))
                continue
            }
            if isRule(trimmed) {
                flush()
                result.append(.rule)
                continue
            }
            if trimmed.contains("|"), index < lines.count, isTableSeparator(lines[index]) {
                flush()
                let header = cells(trimmed)
                var rows: [[String]] = []
                index += 1
                while index < lines.count {
                    let row = lines[index].trimmingCharacters(in: .whitespaces)
                    guard !row.isEmpty, row.contains("|") else { break }
                    var values = cells(row)
                    if values.count < header.count { values += Array(repeating: "", count: header.count - values.count) }
                    rows.append(Array(values.prefix(header.count)))
                    index += 1
                }
                result.append(.table(header: header, rows: rows))
                continue
            }
            let level = min(indent(line) / 2, 3)
            if let (done, item) = taskItem(trimmed) {
                flush()
                result.append(.task(done, item, level: level))
            } else if let item = bulletItem(trimmed) {
                flush()
                result.append(.bullet(item, level: level))
            } else if let (number, item) = numberedItem(trimmed) {
                flush()
                result.append(.numbered(number, item, level: level))
            } else if paragraph.isEmpty, quote.isEmpty, indent(line) >= 2, let last = result.last,
                      let joined = continuing(last, with: trimmed) {
                // Línea sangrada que sigue al elemento de lista anterior.
                result[result.count - 1] = joined
            } else {
                flushQuote()
                paragraph.append(trimmed)
            }
        }
        flush()
        return result
    }

    /// Negrita, cursiva, tachado, código y enlaces. Si el texto no es Markdown válido, se devuelve tal cual.
    public static func inline(_ text: String) -> AttributedString {
        let options = AttributedString.MarkdownParsingOptions(interpretedSyntax: .inlineOnlyPreservingWhitespace)
        let source = visibleImages(text)
        return (try? AttributedString(markdown: source, options: options)) ?? AttributedString(text)
    }

    // MARK: - Piezas

    /// Las imágenes de una línea que no tiene nada más (salvo la viñeta o el número de una
    /// lista). Si alguna no es un archivo local que exista, la línea se queda como texto.
    static func mediaLine(_ line: String, folder: URL?, fileExists: (String) -> Bool) -> [MarkdownMedia]? {
        var body = line
        if let (_, item) = taskItem(body) {
            body = item
        } else if let item = bulletItem(body) {
            body = item
        } else if let (_, item) = numberedItem(body) {
            body = item
        }
        guard let references = imageReferences(body.trimmingCharacters(in: .whitespaces)) else { return nil }
        var result: [MarkdownMedia] = []
        for (alt, source) in references {
            guard let url = MarkdownMedia.resolve(source, folder: folder, fileExists: fileExists),
                  let kind = MarkdownMedia.kind(ofExtension: url.pathExtension) else { return nil }
            result.append(MarkdownMedia(alt: alt, source: source, url: url, kind: kind))
        }
        return result
    }

    /// `![texto](ruta)` una o varias veces, separadas sólo por espacios.
    static func imageReferences(_ line: String) -> [(alt: String, source: String)]? {
        var rest = Substring(line)
        var result: [(String, String)] = []
        while !rest.isEmpty {
            guard let (alt, source, remainder) = imageReference(at: rest) else { return nil }
            result.append((alt, source))
            rest = remainder.drop { $0 == " " || $0 == "\t" }
        }
        return result.isEmpty ? nil : result
    }

    /// Una `![texto](ruta)` al principio de `text` y lo que queda detrás. La ruta admite
    /// `<…>`, paréntesis equilibrados (`foto (1).png`) y un título final entre comillas.
    static func imageReference(at text: Substring) -> (alt: String, source: String, rest: Substring)? {
        guard text.hasPrefix("![") else { return nil }
        var rest = text.dropFirst(2)
        guard let close = rest.range(of: "](") else { return nil }
        let alt = String(rest[..<close.lowerBound])
        guard !alt.contains("]"), !alt.contains("\n") else { return nil }
        rest = rest[close.upperBound...]
        var destination: String
        if rest.hasPrefix("<") {
            guard let end = rest.range(of: ">)") else { return nil }
            destination = String(rest[rest.index(after: rest.startIndex)..<end.lowerBound])
            rest = rest[end.upperBound...]
        } else {
            var depth = 0
            var end: Substring.Index?
            for position in rest.indices {
                let character = rest[position]
                if character == "\n" { return nil }
                if character == "(" { depth += 1 }
                if character == ")" {
                    if depth == 0 { end = position; break }
                    depth -= 1
                }
            }
            guard let end else { return nil }
            destination = String(rest[..<end])
            rest = rest[rest.index(after: end)...]
            if let quote = destination.range(of: " \"", options: .backwards), destination.hasSuffix("\""),
               quote.lowerBound < destination.index(before: destination.endIndex) {
                destination = String(destination[..<quote.lowerBound])
            }
        }
        destination = destination.trimmingCharacters(in: .whitespaces)
        guard !destination.isEmpty else { return nil }
        return (alt.trimmingCharacters(in: .whitespaces), destination, rest)
    }

    /// Una imagen que se queda en el texto (en medio de un párrafo, web o que no existe) no
    /// se pinta, pero tampoco pierde la ruta: una web queda como enlace y una local, a la vista.
    static func visibleImages(_ text: String) -> String {
        guard text.contains("![") else { return text }
        var output = ""
        var rest = Substring(text)
        var inCode = false
        while let character = rest.first {
            if character == "`" { inCode.toggle() }
            if !inCode, character == "!", let (alt, source, remainder) = imageReference(at: rest) {
                let lower = source.lowercased()
                if lower.hasPrefix("https://") || lower.hasPrefix("http://") {
                    output += "[\(alt.isEmpty ? source : alt)](\(source))"
                } else {
                    output += alt.isEmpty ? "`\(source)`" : "\(alt) (`\(source)`)"
                }
                rest = remainder
                continue
            }
            output.append(character)
            rest = rest.dropFirst()
        }
        return output
    }

    private static func fenceMarker(_ line: String) -> String? {
        for marker in ["```", "~~~"] where line.hasPrefix(marker) { return marker }
        return nil
    }

    private static func heading(_ line: String) -> (Int, String)? {
        let hashes = line.prefix { $0 == "#" }
        guard (1...6).contains(hashes.count) else { return nil }
        let rest = line.dropFirst(hashes.count)
        guard rest.hasPrefix(" ") else { return nil }
        var title = rest.trimmingCharacters(in: .whitespaces)
        while title.hasSuffix("#") { title.removeLast() }
        return (hashes.count, title.trimmingCharacters(in: .whitespaces))
    }

    private static func isRule(_ line: String) -> Bool {
        let compact = line.replacingOccurrences(of: " ", with: "")
        guard compact.count >= 3, let first = compact.first, "-*_".contains(first) else { return false }
        return compact.allSatisfy { $0 == first }
    }

    private static func isTableSeparator(_ line: String) -> Bool {
        let parts = cells(line.trimmingCharacters(in: .whitespaces))
        guard !parts.isEmpty else { return false }
        return parts.allSatisfy { part in
            var core = Substring(part)
            if core.hasPrefix(":") { core = core.dropFirst() }
            if core.hasSuffix(":") { core = core.dropLast() }
            return !core.isEmpty && core.allSatisfy { $0 == "-" }
        }
    }

    /// Celdas de una fila `| a | b |`. Un `\|` no separa.
    static func cells(_ row: String) -> [String] {
        var body = Substring(row)
        if body.hasPrefix("|") { body = body.dropFirst() }
        if body.hasSuffix("|"), !body.hasSuffix("\\|") { body = body.dropLast() }
        var values: [String] = []
        var current = ""
        var escaped = false
        for character in body {
            if escaped { current.append(character == "|" ? "|" : "\\\(character)"); escaped = false; continue }
            if character == "\\" { escaped = true; continue }
            if character == "|" { values.append(current); current = ""; continue }
            current.append(character)
        }
        if escaped { current.append("\\") }
        values.append(current)
        return values.map { $0.trimmingCharacters(in: .whitespaces) }
    }

    private static func indent(_ line: String) -> Int {
        line.prefix { $0 == " " || $0 == "\t" }.reduce(0) { $0 + ($1 == "\t" ? 4 : 1) }
    }

    private static func continuing(_ block: MarkdownBlock, with line: String) -> MarkdownBlock? {
        switch block {
        case let .bullet(text, level): return .bullet(text + " " + line, level: level)
        case let .numbered(number, text, level): return .numbered(number, text + " " + line, level: level)
        case let .task(done, text, level): return .task(done, text + " " + line, level: level)
        default: return nil
        }
    }

    private static func taskItem(_ line: String) -> (Bool, String)? {
        guard let item = bulletItem(line) else { return nil }
        for (marker, done) in [("[ ] ", false), ("[x] ", true), ("[X] ", true)] where item.hasPrefix(marker) {
            return (done, String(item.dropFirst(marker.count)))
        }
        return nil
    }

    private static func bulletItem(_ line: String) -> String? {
        for marker in ["- ", "* ", "+ ", "• "] where line.hasPrefix(marker) {
            return String(line.dropFirst(marker.count))
        }
        return nil
    }

    private static func numberedItem(_ line: String) -> (Int, String)? {
        let digits = line.prefix { $0.isNumber }
        guard !digits.isEmpty, digits.count <= 3, let number = Int(digits) else { return nil }
        let rest = line.dropFirst(digits.count)
        guard rest.hasPrefix(". ") || rest.hasPrefix(") ") else { return nil }
        return (number, String(rest.dropFirst(2)))
    }
}
