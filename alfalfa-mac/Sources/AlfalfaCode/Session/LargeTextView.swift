import AppKit
import SwiftUI

/// Un texto que puede ser enorme (un parche de miles de líneas, una salida larga), en un
/// `NSTextView` con desplazamiento: AppKit lo maqueta por partes y SwiftUI no lo mide.
struct LargeTextView: NSViewRepresentable {
    let text: String

    func makeNSView(context: Context) -> NSScrollView {
        let scroll = NSTextView.scrollableTextView()
        scroll.hasVerticalScroller = true
        scroll.drawsBackground = false
        if let view = scroll.documentView as? NSTextView {
            view.isEditable = false
            view.isSelectable = true
            view.drawsBackground = false
            view.textContainerInset = NSSize(width: 10, height: 10)
            view.layoutManager?.allowsNonContiguousLayout = true
            view.font = .monospacedSystemFont(ofSize: 12, weight: .regular)
            view.textColor = .textColor
            view.string = text
        }
        return scroll
    }

    func updateNSView(_ scroll: NSScrollView, context: Context) {
        guard let view = scroll.documentView as? NSTextView, view.string != text else { return }
        view.string = text
    }
}

/// Hoja con el texto entero de una llamada: título, el texto y Cerrar.
struct LargeTextSheet: View {
    let title: String
    let text: String
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text(title).font(.system(size: 14, weight: .semibold)).lineLimit(1)
                Spacer()
                Button("Cerrar") { dismiss() }.keyboardShortcut(.cancelAction)
            }
            LargeTextView(text: text)
                .background(Theme.field, in: RoundedRectangle(cornerRadius: 8))
        }
        .padding(16)
        .frame(minWidth: 640, idealWidth: 820, minHeight: 420, idealHeight: 620)
    }
}
