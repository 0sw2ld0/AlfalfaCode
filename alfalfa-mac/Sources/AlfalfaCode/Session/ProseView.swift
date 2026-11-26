import AlfalfaKit
import AppKit
import SwiftUI

/// El texto corrido de un mensaje del agente en un solo `NSTextView`: se selecciona arrastrando de un
/// párrafo, título o viñeta al siguiente, como en Codex. Los enlaces pasan por el `openURL` de la
/// conversación (sólo http y https; los archivos se enseñan en el Finder).
struct ProseView: NSViewRepresentable {

    // MARK: - Propiedades

    let blocks: [MarkdownBlock]

    // MARK: - NSViewRepresentable

    func makeCoordinator() -> Coordinator { Coordinator() }

    func makeNSView(context: Context) -> ProseTextView {
        let view = ProseTextView(frame: .zero)
        view.isEditable = false
        view.isSelectable = true
        view.isRichText = true
        view.drawsBackground = false
        view.textContainerInset = .zero
        view.textContainer?.lineFragmentPadding = 0
        view.textContainer?.widthTracksTextView = true
        view.isVerticallyResizable = false
        view.isHorizontallyResizable = false
        view.linkTextAttributes = [.foregroundColor: NSColor(Theme.link), .cursor: NSCursor.pointingHand]
        view.delegate = context.coordinator
        view.setContentHuggingPriority(.defaultLow, for: .horizontal)
        view.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        return view
    }

    func updateNSView(_ view: ProseTextView, context: Context) {
        context.coordinator.openURL = context.environment.openURL
        guard context.coordinator.blocks != blocks else { return }
        context.coordinator.blocks = blocks
        context.coordinator.sizes = [:]
        view.textStorage?.setAttributedString(MarkdownProse.attributed(blocks, style: Self.style))
    }

    /// El alto que ocupa el texto con el ancho propuesto. Se mide aparte (no con la vista que se ve) y se
    /// guarda por ancho: SwiftUI pregunta varias veces en cada pasada.
    func sizeThatFits(_ proposal: ProposedViewSize, nsView: ProseTextView, context: Context) -> CGSize? {
        let coordinator = context.coordinator
        if coordinator.blocks != blocks {
            coordinator.blocks = blocks
            coordinator.sizes = [:]
            nsView.textStorage?.setAttributedString(MarkdownProse.attributed(blocks, style: Self.style))
        }
        let width = proposal.width.flatMap { $0.isFinite ? max($0, 1) : nil } ?? 10_000
        if let size = coordinator.sizes[width] { return size }
        guard let storage = nsView.textStorage else { return nil }
        let measured = NSTextStorage(attributedString: storage)
        let layout = NSLayoutManager()
        let container = NSTextContainer(size: CGSize(width: width, height: .greatestFiniteMagnitude))
        container.lineFragmentPadding = 0
        layout.addTextContainer(container)
        measured.addLayoutManager(layout)
        layout.ensureLayout(for: container)
        let used = layout.usedRect(for: container)
        // Sin ancho propuesto, el de la línea más larga; con ancho, todo el que se ofrece.
        let size = CGSize(width: proposal.width == nil || width == 10_000 ? ceil(used.width) : width,
                          height: ceil(used.height))
        coordinator.sizes[width] = size
        return size
    }

    // MARK: - Coordinator

    final class Coordinator: NSObject, NSTextViewDelegate {
        var blocks: [MarkdownBlock]?
        var sizes: [CGFloat: CGSize] = [:]
        var openURL: OpenURLAction?

        func textView(_ textView: NSTextView, clickedOnLink link: Any, at charIndex: Int) -> Bool {
            let url = (link as? URL) ?? (link as? String).flatMap(URL.init(string:))
            guard let url, let openURL else { return false }
            openURL(url)
            return true
        }
    }

    // MARK: - Propiedades privadas

    private static let style = MarkdownProse.Style(
        font: .systemFont(ofSize: 15), mono: .monospacedSystemFont(ofSize: 12.5, weight: .regular),
        text: .labelColor, secondary: NSColor(Theme.secondary), muted: NSColor(Theme.muted),
        accent: NSColor(Theme.alfalfa), link: NSColor(Theme.link),
        codeBackground: NSColor(Theme.field), lineSpacing: Theme.bodyLineSpacing, blockSpacing: 12)
}

/// Dibuja la barra verde a la izquierda de las citas, que en un `NSTextView` no es un carácter.
final class ProseTextView: NSTextView {
    override func drawBackground(in rect: NSRect) {
        super.drawBackground(in: rect)
        guard let storage = textStorage, let layout = layoutManager, let container = textContainer else { return }
        NSColor(Theme.alfalfa).withAlphaComponent(0.7).setFill()
        storage.enumerateAttribute(MarkdownProse.quoteAttribute, in: NSRange(location: 0, length: storage.length)) {
            value, range, _ in
            guard value != nil else { return }
            let glyphs = layout.glyphRange(forCharacterRange: range, actualCharacterRange: nil)
            let bounds = layout.boundingRect(forGlyphRange: glyphs, in: container)
            let origin = textContainerOrigin
            NSBezierPath(roundedRect: NSRect(x: origin.x, y: origin.y + bounds.minY, width: 3, height: bounds.height),
                         xRadius: 1.5, yRadius: 1.5).fill()
        }
    }
}
