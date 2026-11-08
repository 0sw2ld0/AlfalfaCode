import Foundation

/// Bloque de Markdown del texto del agente. Cubre lo que pinta la TUI de Codex
/// (codex-rs/tui/src/markdown_render.rs): títulos, citas, listas anidadas y de
/// tareas, código con lenguaje, líneas separadoras y tablas GFM.
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
}

public enum MarkdownText {
    /// Divide el texto en bloques. El formato en línea (negrita, código, enlaces) se resuelve con `inline`.
    public static func blocks(_ text: String) -> [MarkdownBlock] {
        var result: [MarkdownBlock] = []
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
        return (try? AttributedString(markdown: text, options: options)) ?? AttributedString(text)
    }

    // MARK: - Piezas

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
