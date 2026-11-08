import AlfalfaKit
import AppKit
import SwiftUI

/// La barra de título está oculta y el contenido llega hasta arriba, así que SwiftUI
/// se queda los clics de esa franja. Esta zona la devuelve a la ventana: arrastrar
/// la mueve y el doble clic hace lo mismo que en las demás apps.
struct TitleBarArea: NSViewRepresentable {
    func makeNSView(context: Context) -> NSView { TitleBarView() }
    func updateNSView(_ nsView: NSView, context: Context) {}
}

private final class TitleBarView: NSView {
    override var mouseDownCanMoveWindow: Bool { true }

    override func mouseDown(with event: NSEvent) {
        guard let window else { return }
        if event.clickCount == 2 {
            switch TitleBarDoubleClick.current {
            case .zoom: window.zoom(nil)
            case .minimize: window.miniaturize(nil)
            case .nothing: break
            }
        } else {
            window.performDrag(with: event)
        }
    }
}
