import Foundation

/// Un trozo acotado de un texto largo para pintarlo en la conversación sin colgar la app.
///
/// Un `apply_patch` de cientos de líneas llegaba como una sola línea escapada de decenas de miles
/// de caracteres, y SwiftUI se quedaba midiendo ese `Text` sin fin (99 % de CPU). La vista previa
/// tiene como mucho `maxLines` líneas de `maxLineLength` caracteres; el resto, con «Ver todo».
public struct TextPreview: Equatable, Sendable {
    public static let maxLines = 40
    public static let maxLineLength = 300

    public var shown: String
    public var totalLines: Int
    /// Hay algo más que no se enseña: líneas de más o líneas recortadas.
    public var truncated: Bool

    public var shownLines: Int
    public var hiddenLines: Int { max(0, totalLines - shownLines) }

    public init(_ text: String, maxLines: Int = TextPreview.maxLines, maxLineLength: Int = TextPreview.maxLineLength) {
        var lines: [String] = []
        var total = 0
        var cut = false
        // Sin partir el texto entero: sólo se recorren las líneas que hacen falta y se cuentan las demás.
        var start = text.startIndex
        while start <= text.endIndex {
            let end = text[start...].firstIndex(of: "\n") ?? text.endIndex
            total += 1
            if lines.count < maxLines {
                let line = text[start..<end]
                if line.count > maxLineLength {
                    lines.append(String(line.prefix(maxLineLength)) + "…")
                    cut = true
                } else {
                    lines.append(String(line))
                }
            }
            if end == text.endIndex { break }
            start = text.index(after: end)
            if lines.count >= maxLines {
                // Basta contar los saltos que quedan: tras el último salto queda una línea más.
                total += 1 + text[start...].reduce(0) { $1 == "\n" ? $0 + 1 : $0 }
                break
            }
        }
        shown = lines.joined(separator: "\n")
        totalLines = total
        shownLines = lines.count
        truncated = cut || total > lines.count
    }

    /// Una sola línea corta para una fila, un título o un distintivo: los espacios y saltos se juntan
    /// y lo que pasa de `limit` caracteres se corta con «…». Sólo recorre lo que va a enseñar, así que
    /// sirve igual con un texto de 50 KB.
    public static func line(_ text: String, limit: Int = 300) -> String {
        var result = ""
        var count = 0
        var pendingSpace = false
        for character in text {
            if character.isWhitespace || character.isNewline {
                pendingSpace = !result.isEmpty
                continue
            }
            if count >= limit { return result + "…" }
            if pendingSpace {
                result.append(" ")
                count += 1
                pendingSpace = false
                if count >= limit { return result.trimmingCharacters(in: .whitespaces) + "…" }
            }
            result.append(character)
            count += 1
        }
        return result
    }

    /// Un paso de un subagente en el panel: su primera línea, acotada. El motor manda hasta 20 000
    /// caracteres por paso (el encargo entero llega como primer paso) y el panel pinta los 40 últimos.
    public static func subagentStep(_ step: AgentEvent) -> String {
        let firstLine = step.text.split(separator: "\n", maxSplits: 1, omittingEmptySubsequences: true).first ?? ""
        let first = line(String(firstLine.prefix(maxLineLength * 4)))
        switch step.role {
        case "agent", "assistant": return "› " + first
        case "tool": return first.hasPrefix("Inicio ") ? "· " + String(first.dropFirst(7)) : "  " + first
        default: return "  " + first
        }
    }
}

/// Lo legible de una edición en la línea de tiempo: el parche o el contenido, con saltos de línea
/// de verdad, en vez de la llamada escapada.
public enum EditText {
    /// `apply_patch` → el parche; `write_file` → «+++ ruta» y el contenido; `edit_file` → − viejo, + nuevo.
    public static func of(_ call: ToolCall) -> String? {
        guard let request = call.request else { return nil }
        switch call.name {
        case "apply_patch":
            return pythonString("input", in: request)
        case "write_file":
            guard let content = pythonString("content", in: request) else { return nil }
            return "+++ " + (call.target ?? "archivo") + "\n" + content.split(separator: "\n", omittingEmptySubsequences: false)
                .map { "+" + $0 }.joined(separator: "\n")
        case "edit_file":
            let old = pythonString("old", in: request) ?? ""
            let new = pythonString("new", in: request) ?? ""
            guard !old.isEmpty || !new.isEmpty else { return nil }
            func mark(_ text: String, _ sign: String) -> [String] {
                text.isEmpty ? [] : text.split(separator: "\n", omittingEmptySubsequences: false).map { sign + $0 }
            }
            return (["--- " + (call.target ?? "archivo"), "+++ " + (call.target ?? "archivo")]
                    + mark(old, "-") + mark(new, "+")).joined(separator: "\n")
        default:
            return nil
        }
    }

    /// Valor de `'clave': '…'` o `'clave': "…"` en el repr de Python del evento, con sus escapes deshechos.
    static func pythonString(_ key: String, in text: String) -> String? {
        guard let range = text.range(of: "'\(key)': ") else { return nil }
        var iterator = text[range.upperBound...].makeIterator()
        guard let quote = iterator.next(), quote == "'" || quote == "\"" else { return nil }
        var value = ""
        while let character = iterator.next() {
            if character == quote { return value }
            guard character == "\\" else { value.append(character); continue }
            guard let escaped = iterator.next() else { break }
            switch escaped {
            case "n": value.append("\n")
            case "t": value.append("\t")
            case "r": value.append("\r")
            case "x", "u":
                var hex = ""
                let length = escaped == "x" ? 2 : 4
                while hex.count < length, let digit = iterator.next() { hex.append(digit) }
                if let code = UInt32(hex, radix: 16), let scalar = Unicode.Scalar(code) { value.append(Character(scalar)) }
            default: value.append(escaped)
            }
        }
        // Cortado (el evento recorta las llamadas largas): lo que llegó.
        return value.isEmpty ? nil : value
    }
}
