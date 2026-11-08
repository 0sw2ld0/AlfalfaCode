import AppKit
import SwiftUI

/// El panel de terminal, como el de Claude Code: pestañas arriba, «+» para otra y «×» para
/// cerrar el panel (las shells siguen vivas). Se ensancha arrastrando su borde izquierdo.
struct TerminalPane: View {

    // MARK: - Constantes

    static let widths: ClosedRange<Double> = 320...1000

    // MARK: - Propiedades

    @ObservedObject var terminals: SessionTerminals

    var body: some View {
        VStack(spacing: 0) {
            header
            Divider().overlay(Theme.border)
            if let tab = terminals.current {
                TerminalHost(view: tab.view).id(tab.id)
                    .padding(.leading, 8).padding(.top, 6)
                    .background(Theme.sidebar)
            } else {
                Theme.sidebar
            }
        }
        .frame(width: CGFloat(width))
        .overlay(alignment: .leading) { resizeHandle }
        .background(Theme.sidebar)
        .accessibilityElement(children: .contain)
        .accessibilityLabel("Terminal")
    }

    // MARK: - Propiedades privadas

    @AppStorage("terminal.ancho") private var width = 480.0
    @State private var dragStart: Double?

    private var header: some View {
        HStack(spacing: 4) {
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 2) {
                    ForEach(terminals.tabs) { tab in tabChip(tab) }
                }
            }
            button("plus", "Nueva pestaña de terminal") { terminals.addTab() }
            Spacer(minLength: 4)
            button("xmark", "Cerrar el panel (las terminales siguen abiertas)") { terminals.close() }
        }
        .padding(.horizontal, 10).frame(height: 40)
    }

    private var resizeHandle: some View {
        Color.clear.frame(width: 6).contentShape(Rectangle())
            .onHover { inside in inside ? NSCursor.resizeLeftRight.push() : NSCursor.pop() }
            .gesture(DragGesture(minimumDistance: 1).onChanged { value in
                let start = dragStart ?? width
                dragStart = start
                width = min(Self.widths.upperBound, max(Self.widths.lowerBound, start - value.translation.width))
            }.onEnded { _ in dragStart = nil })
    }

    // MARK: - Métodos privados

    private func tabChip(_ tab: TerminalTab) -> some View {
        let active = tab.id == terminals.current?.id
        return HStack(spacing: 6) {
            Image(systemName: "terminal").font(.system(size: 11))
            Text(tab.title).font(.system(size: 12.5, weight: active ? .semibold : .regular)).lineLimit(1)
            if terminals.tabs.count > 1 {
                Button { terminals.closeTab(tab) } label: {
                    Image(systemName: "xmark").font(.system(size: 9, weight: .semibold))
                }
                .buttonStyle(.plain).help("Cerrar esta terminal").accessibilityLabel("Cerrar \(tab.title)")
            }
        }
        .foregroundStyle(active ? Theme.text : Theme.secondary)
        .padding(.horizontal, 9).frame(height: 26)
        .background(active ? Theme.selection : Color.clear, in: RoundedRectangle(cornerRadius: 6))
        .contentShape(Rectangle())
        .onTapGesture { terminals.select(tab) }
        .accessibilityAddTraits(active ? [.isButton, .isSelected] : .isButton)
    }

    private func button(_ symbol: String, _ help: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: symbol).font(.system(size: 12.5)).foregroundStyle(Theme.secondary)
                .frame(width: 26, height: 26).contentShape(Rectangle())
        }
        .buttonStyle(.plain).help(help).accessibilityLabel(help)
    }
}

/// Pone la vista de AppKit de la pestaña dentro de SwiftUI. La vista es de la pestaña, no de
/// SwiftUI: al cambiar de pestaña o cerrar el panel no se pierde lo escrito.
private struct TerminalHost: NSViewRepresentable {
    let view: NSView

    func makeNSView(context: Context) -> NSView {
        let container = NSView()
        attach(to: container)
        return container
    }

    func updateNSView(_ container: NSView, context: Context) {
        if view.superview !== container { attach(to: container) }
    }

    private func attach(to container: NSView) {
        view.removeFromSuperview()
        view.translatesAutoresizingMaskIntoConstraints = false
        container.addSubview(view)
        NSLayoutConstraint.activate([
            view.leadingAnchor.constraint(equalTo: container.leadingAnchor),
            view.trailingAnchor.constraint(equalTo: container.trailingAnchor),
            view.topAnchor.constraint(equalTo: container.topAnchor),
            view.bottomAnchor.constraint(equalTo: container.bottomAnchor)
        ])
        DispatchQueue.main.async { view.window?.makeFirstResponder(view) }
    }
}

/// Botón de la cabecera de la sesión que abre y cierra la terminal (⌃`, como en Claude Code).
struct TerminalToggle: View {

    // MARK: - Propiedades

    @ObservedObject var terminals: SessionTerminals

    var body: some View {
        Button { terminals.toggle() } label: {
            Image(systemName: "apple.terminal").foregroundStyle(terminals.isOpen ? Theme.alfalfa : Theme.secondary)
                .frame(width: 26, height: 26).contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .keyboardShortcut("`", modifiers: .control)
        .help(terminals.isOpen ? "Cerrar la terminal (⌃`)" : "Abrir una terminal en la carpeta del proyecto (⌃`)")
        .accessibilityLabel(terminals.isOpen ? "Cerrar la terminal" : "Abrir la terminal")
    }
}

/// A la derecha de la conversación: la terminal si está abierta; si no, el panel de detalles.
struct SessionSidePane<Details: View>: View {

    // MARK: - Propiedades

    @ObservedObject var terminals: SessionTerminals
    @ViewBuilder let details: () -> Details

    var body: some View {
        if terminals.isOpen {
            TerminalPane(terminals: terminals)
        } else {
            details().frame(width: 300)
        }
    }
}
