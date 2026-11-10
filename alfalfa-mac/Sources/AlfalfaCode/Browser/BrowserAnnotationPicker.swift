import AlfalfaKit
import AppKit

/// La selección pertenece a la app: ningún clic ni arrastre llega a los controles de la página.
@MainActor
enum BrowserAnnotationPicker {
    static func makeView(browser: BrowserEngine) -> AnnotationSelectionView {
        let view = AnnotationSelectionView()
        view.select = { [weak view] rect in
            guard let view else { return }
            let converted = browser.webView.convert(rect, from: view)
            let area = browser.webView.isFlipped ? converted
                : CGRect(x: converted.minX, y: browser.webView.bounds.height - converted.maxY,
                         width: converted.width, height: converted.height)
            Task { @MainActor in
                do { try await browser.selectAnnotation(in: area) } catch {
                    browser.cancelAnnotation()
                    let alert = NSAlert()
                    alert.messageText = "No se pudo seleccionar el elemento"
                    alert.informativeText = error.localizedDescription
                    alert.addButton(withTitle: "Aceptar")
                    if let window = browser.webView.window { _ = await alert.beginSheetModal(for: window) }
                }
            }
        }
        return view
    }

}

final class AnnotationSelectionView: NSView {
    var select: ((CGRect) -> Void)?
    override var isFlipped: Bool { true }
    private var start: CGPoint?
    private var selection = CGRect.zero

    override func resetCursorRects() { addCursorRect(bounds, cursor: .crosshair) }

    override func mouseDown(with event: NSEvent) {
        start = convert(event.locationInWindow, from: nil)
        selection = .zero
        needsDisplay = true
    }

    override func mouseDragged(with event: NSEvent) {
        guard let start else { return }
        let end = convert(event.locationInWindow, from: nil)
        selection = CGRect(x: min(start.x, end.x), y: min(start.y, end.y),
                           width: abs(start.x - end.x), height: abs(start.y - end.y))
        needsDisplay = true
    }

    override func mouseUp(with event: NSEvent) {
        guard let start else { return }
        mouseDragged(with: event)
        select?(selection.width > 5 || selection.height > 5
            ? selection : CGRect(x: start.x, y: start.y, width: 0, height: 0))
        self.start = nil
    }

    override func draw(_ dirtyRect: NSRect) {
        NSColor.systemGreen.withAlphaComponent(0.06).setFill()
        dirtyRect.fill()
        if selection.width > 0 || selection.height > 0 {
            NSColor.systemGreen.withAlphaComponent(0.18).setFill()
            selection.fill()
            NSColor.systemGreen.setStroke()
            NSBezierPath(rect: selection).stroke()
        }
    }
}
