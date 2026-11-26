import AppKit
import Foundation

/// Un trozo de un mensaje del agente: texto corrido (párrafos, títulos, listas y citas seguidos) o un
/// bloque que se pinta aparte (código con sus botones, tabla, separador, imágenes).
public enum MarkdownSegment: Equatable, Sendable {
    case prose([MarkdownBlock])
    case block(MarkdownBlock)
}

/// El texto corrido de un mensaje en una sola pieza seleccionable. Antes cada párrafo, título o viñeta era
/// un `Text` con su propia selección: al arrastrar sólo se seleccionaba ese trozo y la selección no seguía
/// al siguiente. Ahora todo el tramo es un `NSAttributedString` para un único `NSTextView`.
public enum MarkdownProse {

    // MARK: - Constantes

    /// Lo que marca una cita, para dibujar la barra verde a su izquierda.
    public static let quoteAttribute = NSAttributedString.Key("AlfalfaQuote")

    public struct Style {
        public var font: NSFont
        public var mono: NSFont
        public var text: NSColor
        public var secondary: NSColor
        public var muted: NSColor
        public var accent: NSColor
        public var link: NSColor
        public var codeBackground: NSColor
        public var lineSpacing: CGFloat
        public var blockSpacing: CGFloat

        public init(font: NSFont = .systemFont(ofSize: 15), mono: NSFont = .monospacedSystemFont(ofSize: 13.5, weight: .regular),
                    text: NSColor = .labelColor, secondary: NSColor = .secondaryLabelColor,
                    muted: NSColor = .tertiaryLabelColor, accent: NSColor = .systemGreen, link: NSColor = .linkColor,
                    codeBackground: NSColor = .quaternaryLabelColor, lineSpacing: CGFloat = 5, blockSpacing: CGFloat = 12) {
            self.font = font
            self.mono = mono
            self.text = text
            self.secondary = secondary
            self.muted = muted
            self.accent = accent
            self.link = link
            self.codeBackground = codeBackground
            self.lineSpacing = lineSpacing
            self.blockSpacing = blockSpacing
        }
    }

    // MARK: - Métodos

    /// Junta los bloques de texto seguidos en un solo tramo; código, tablas, separadores e imágenes van aparte.
    public static func segments(_ blocks: [MarkdownBlock]) -> [MarkdownSegment] {
        var result: [MarkdownSegment] = []
        for block in blocks {
            guard isProse(block) else {
                result.append(.block(block))
                continue
            }
            if case let .prose(previous)? = result.last {
                result[result.count - 1] = .prose(previous + [block])
            } else {
                result.append(.prose([block]))
            }
        }
        return result
    }

    public static func isProse(_ block: MarkdownBlock) -> Bool {
        switch block {
        case .paragraph, .heading, .bullet, .numbered, .task, .quote: return true
        case .code, .table, .rule, .media: return false
        }
    }

    /// El tramo listo para el `NSTextView`: un bloque por párrafo, con su sangría y su separación.
    public static func attributed(_ blocks: [MarkdownBlock], style: Style = Style()) -> NSAttributedString {
        let result = NSMutableAttributedString()
        for (index, block) in blocks.enumerated() {
            if index > 0 { result.append(NSAttributedString(string: "\n")) }
            let start = result.length
            append(block, to: result, style: style)
            let range = NSRange(location: start, length: result.length - start)
            paragraphs(in: result, range: range, block: block, first: index == 0,
                       last: index == blocks.count - 1, style: style)
        }
        return result
    }

    // MARK: - Métodos privados

    private static func append(_ block: MarkdownBlock, to result: NSMutableAttributedString, style: Style) {
        let base: [NSAttributedString.Key: Any] = [.font: style.font, .foregroundColor: style.text]
        switch block {
        case let .paragraph(value):
            result.append(inline(value, base: base, style: style))
        case let .heading(level, value):
            let size: CGFloat = [22, 18, 16, 15, 15, 15][min(max(level, 1), 6) - 1]
            let font = NSFont.systemFont(ofSize: size, weight: level <= 2 ? .bold : .semibold)
            result.append(inline(value, base: base.merging([.font: font]) { $1 }, style: style))
        case let .bullet(value, level):
            result.append(NSAttributedString(string: (level == 0 ? "•" : "◦") + "\t", attributes: base))
            result.append(inline(value, base: base, style: style))
        case let .numbered(number, value, _):
            let font = NSFont.monospacedDigitSystemFont(ofSize: style.font.pointSize, weight: .regular)
            result.append(NSAttributedString(string: "\(number).\t",
                                             attributes: [.font: font, .foregroundColor: style.secondary]))
            result.append(inline(value, base: base, style: style))
        case let .task(done, value, _):
            result.append(NSAttributedString(string: (done ? "☑" : "☐") + "\t",
                                             attributes: [.font: style.font,
                                                          .foregroundColor: done ? style.accent : style.muted]))
            result.append(inline(value, base: base, style: style))
        case let .quote(value):
            let quote = NSMutableAttributedString(attributedString:
                inline(value, base: base.merging([.foregroundColor: style.secondary]) { $1 }, style: style))
            quote.addAttribute(quoteAttribute, value: true, range: NSRange(location: 0, length: quote.length))
            result.append(quote)
        case .code, .table, .rule, .media:
            break
        }
    }

    /// Sangría colgante en las listas (el texto de varias líneas queda alineado tras la viñeta, como en Codex),
    /// el aire de los títulos y la separación entre bloques sólo tras la última línea de cada uno.
    private static func paragraphs(in text: NSMutableAttributedString, range: NSRange, block: MarkdownBlock,
                                   first: Bool, last: Bool, style: Style) {
        var indent: CGFloat = 0, marker: CGFloat = 0, before: CGFloat = 0
        switch block {
        case let .bullet(_, level), let .numbered(_, _, level), let .task(_, _, level):
            indent = 4 + CGFloat(level) * 24
            marker = 26
        case .quote:
            indent = 13
        case let .heading(level, _):
            before = first ? 0 : (level <= 2 ? 10 : 4)
        default:
            break
        }
        let string = text.string as NSString
        var location = range.location
        while location < NSMaxRange(range) {
            let line = string.paragraphRange(for: NSRange(location: location, length: 0))
            let clipped = NSIntersectionRange(line, range)
            let paragraph = NSMutableParagraphStyle()
            paragraph.lineSpacing = style.lineSpacing
            let isFirstLine = clipped.location == range.location
            // La primera línea lleva la viñeta; las demás de un mismo punto, alineadas con su texto.
            paragraph.firstLineHeadIndent = isFirstLine ? indent : indent + marker
            paragraph.headIndent = indent + marker
            if marker > 0 { paragraph.tabStops = [NSTextTab(textAlignment: .left, location: indent + marker)] }
            paragraph.defaultTabInterval = 24
            paragraph.paragraphSpacingBefore = isFirstLine ? before : 0
            // Tras el último bloque, nada: el aire lo pone ya la conversación.
            paragraph.paragraphSpacing = !last && NSMaxRange(line) >= NSMaxRange(range) ? style.blockSpacing : 0
            text.addAttribute(.paragraphStyle, value: paragraph, range: clipped)
            location = NSMaxRange(line)
        }
    }

    /// Negrita, cursiva, `código`, tachado y enlaces de una línea (lo que `MarkdownText.inline` interpreta).
    static func inline(_ value: String, base: [NSAttributedString.Key: Any], style: Style) -> NSAttributedString {
        let parsed = MarkdownText.inline(value)
        let result = NSMutableAttributedString()
        let baseFont = base[.font] as? NSFont ?? style.font
        for run in parsed.runs {
            var attributes = base
            let intent = run.inlinePresentationIntent ?? []
            var font = baseFont
            if intent.contains(.code) {
                font = NSFont.monospacedSystemFont(ofSize: baseFont.pointSize * 0.9, weight: .regular)
                attributes[.backgroundColor] = style.codeBackground
            }
            var traits: NSFontDescriptor.SymbolicTraits = []
            if intent.contains(.stronglyEmphasized) { traits.insert(.bold) }
            if intent.contains(.emphasized) { traits.insert(.italic) }
            if !traits.isEmpty {
                let descriptor = font.fontDescriptor.withSymbolicTraits(font.fontDescriptor.symbolicTraits.union(traits))
                font = NSFont(descriptor: descriptor, size: font.pointSize) ?? font
            }
            attributes[.font] = font
            if intent.contains(.strikethrough) { attributes[.strikethroughStyle] = NSUnderlineStyle.single.rawValue }
            if let link = run.link { attributes[.link] = link }
            result.append(NSAttributedString(string: String(parsed[run.range].characters), attributes: attributes))
        }
        return result
    }
}
