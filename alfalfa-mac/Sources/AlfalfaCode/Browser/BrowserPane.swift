import AlfalfaKit
import AppKit
import SwiftUI
import WebKit

/// El navegador propio de la sesión, como el de la app de Codex: atrás, adelante, recargar, la
/// barra de dirección y la página. Arriba de la página se ve lo que está haciendo el agente.
/// Se ensancha arrastrando su borde izquierdo, como la terminal.
struct BrowserPane: View {

    // MARK: - Constantes

    static let widths: ClosedRange<Double> = 380...1200

    // MARK: - Propiedades

    @ObservedObject var browser: BrowserEngine

    var body: some View {
        VStack(spacing: 0) {
            toolbar
            if browser.isLoading {
                ProgressView(value: browser.progress).progressViewStyle(.linear).tint(Theme.alfalfa)
                    .frame(height: 2).padding(.horizontal, 0)
            } else {
                Divider().overlay(Theme.border)
            }
            if let size = browser.viewport {
                Text("\(Int(size.width)) × \(Int(size.height)) píxeles CSS · Vista a escala")
                    .font(.system(size: 10.5)).foregroundStyle(Theme.secondary)
                    .padding(.vertical, 4).frame(maxWidth: .infinity).background(Theme.card)
            }
            if let line = agentLine { agentBanner(line) }
            if let notice = browser.notice { noticeBanner(notice) }
            ZStack {
                Color.white
                if let size = browser.viewport {
                    GeometryReader { available in
                        let scale = min(1, available.size.width / size.width, available.size.height / size.height)
                        BrowserHost(view: browser.webView, viewport: size)
                            .frame(width: size.width, height: size.height)
                            .scaleEffect(scale, anchor: .topLeading)
                            .frame(width: size.width * scale, height: size.height * scale, alignment: .topLeading)
                            .frame(maxWidth: .infinity, maxHeight: .infinity)
                    }
                    .background(Color.gray.opacity(0.12))
                } else {
                    BrowserHost(view: browser.webView)
                }
                if browser.url == nil { emptyState }
            }
        }
        .frame(width: CGFloat(width))
        .overlay(alignment: .leading) { resizeHandle }
        .background(Theme.sidebar)
        .onChange(of: browser.url) { _, url in if !editing { address = url?.absoluteString ?? "" } }
        .onAppear { address = browser.url?.absoluteString ?? "" }
        .accessibilityElement(children: .contain)
        .accessibilityLabel("Navegador")
    }

    // MARK: - Propiedades privadas

    @AppStorage("navegador.ancho") private var width = 620.0
    @State private var dragStart: Double?
    @State private var address = ""
    @FocusState private var editing: Bool

    /// Lo que hace el agente ahora o lo último que hizo.
    private var agentLine: String? { browser.activity ?? browser.lastActivity }

    private var toolbar: some View {
        HStack(spacing: 2) {
            button("chevron.left", "Atrás", enabled: browser.canGoBack) { browser.goBack() }
            button("chevron.right", "Adelante", enabled: browser.canGoForward) { browser.goForward() }
            button(browser.isLoading ? "xmark" : "arrow.clockwise", browser.isLoading ? "Detener" : "Recargar",
                   enabled: browser.url != nil) { browser.reload() }
            HStack(spacing: 6) {
                Image(systemName: lockSymbol).font(.system(size: 10.5)).foregroundStyle(Theme.muted)
                TextField("Escribe una dirección", text: $address)
                    .textFieldStyle(.plain).font(.system(size: 12.5)).focused($editing)
                    .onSubmit {
                        browser.userOpen(address)
                        if let url = BrowserAddress.normalized(address) { address = url.absoluteString }
                        editing = false
                    }
                    .accessibilityLabel("Dirección")
            }
            .padding(.horizontal, 9).frame(height: 26)
            .background(Theme.field, in: RoundedRectangle(cornerRadius: 7))
            .overlay(RoundedRectangle(cornerRadius: 7).stroke(editing ? Theme.alfalfa.opacity(0.6) : Theme.border))
            .padding(.horizontal, 4)
            button("xmark", "Cerrar el navegador (la página sigue abierta)") { browser.isPanelOpen = false }
        }
        .padding(.horizontal, 8).frame(height: 40)
    }

    private var lockSymbol: String {
        guard let url = browser.url else { return "globe" }
        if url.scheme == "https" { return "lock.fill" }
        return BrowserAddress.kind(of: url) == .publicInternet ? "lock.open" : "desktopcomputer"
    }

    private var emptyState: some View {
        VStack(spacing: 10) {
            Image(systemName: "globe").font(.system(size: 28)).foregroundStyle(Theme.muted)
            Text("Navegador de la sesión").font(.system(size: 14, weight: .semibold)).foregroundStyle(Theme.text)
            Text("Aislado: sin tus cuentas ni las cookies de Safari. Lo usa el agente y puedes usarlo tú.")
                .font(.system(size: 12)).foregroundStyle(Theme.secondary).multilineTextAlignment(.center)
                .frame(maxWidth: 300)
        }
        .padding(24).frame(maxWidth: .infinity, maxHeight: .infinity).background(Theme.background)
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

    private func agentBanner(_ line: String) -> some View {
        let working = browser.activity != nil
        return HStack(spacing: 7) {
            if working {
                ProgressView().controlSize(.mini)
            } else {
                Image(systemName: "sparkles").font(.system(size: 10.5)).foregroundStyle(Theme.alfalfa)
            }
            Text((working ? "El agente: " : "Último paso del agente: ") + line)
                .font(.system(size: 11.5)).foregroundStyle(working ? Theme.text : Theme.secondary)
                .lineLimit(1).truncationMode(.middle)
            Spacer(minLength: 0)
        }
        .padding(.horizontal, 12).frame(height: 26)
        .background(working ? Theme.alfalfa.opacity(0.16) : Theme.card)
        .accessibilityElement(children: .combine)
    }

    private func noticeBanner(_ text: String) -> some View {
        HStack(spacing: 7) {
            Image(systemName: "exclamationmark.triangle.fill").font(.system(size: 10.5)).foregroundStyle(Theme.warning)
            Text(text).font(.system(size: 11.5)).foregroundStyle(Theme.text).lineLimit(2)
            Spacer(minLength: 0)
            Button { browser.dismissNotice() } label: {
                Image(systemName: "xmark").font(.system(size: 9, weight: .semibold)).foregroundStyle(Theme.secondary)
            }
            .buttonStyle(.plain).help("Quitar el aviso").accessibilityLabel("Quitar el aviso")
        }
        .padding(.horizontal, 12).padding(.vertical, 5)
        .background(Theme.warning.opacity(0.12))
    }

    private func button(_ symbol: String, _ help: String, enabled: Bool = true,
                        action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: symbol).font(.system(size: 12.5))
                .foregroundStyle(enabled ? Theme.secondary : Theme.muted.opacity(0.5))
                .frame(width: 26, height: 26).contentShape(Rectangle())
        }
        .buttonStyle(.plain).disabled(!enabled).help(help).accessibilityLabel(help)
    }
}

/// El WKWebView de la sesión dentro de SwiftUI. La vista es del navegador, no de SwiftUI: al cerrar
/// el panel o cambiar de sesión la página sigue cargada.
private struct BrowserHost: NSViewRepresentable {
    final class Coordinator {
        var viewport: CGSize?
        var constraints: [NSLayoutConstraint] = []
    }

    let view: WKWebView
    var viewport: CGSize?

    func makeCoordinator() -> Coordinator { Coordinator() }

    func makeNSView(context: Context) -> NSView {
        let container = BrowserViewportContainer()
        attach(to: container, coordinator: context.coordinator)
        return container
    }

    func updateNSView(_ container: NSView, context: Context) {
        if view.superview !== container || context.coordinator.viewport != viewport {
            attach(to: container, coordinator: context.coordinator)
        }
    }

    private func attach(to container: NSView, coordinator: Coordinator) {
        if let container = container as? BrowserViewportContainer {
            container.webView = view
            container.viewport = viewport
            container.needsLayout = true
        }
        NSLayoutConstraint.deactivate(coordinator.constraints)
        // Al pasar del panel libre al fijo SwiftUI puede crear otro host para el mismo WKWebView.
        NSLayoutConstraint.deactivate(view.constraints.filter { $0.identifier == "alfalfa.viewport" })
        view.removeFromSuperview()
        view.translatesAutoresizingMaskIntoConstraints = false
        container.addSubview(view)
        var constraints = [view.leadingAnchor.constraint(equalTo: container.leadingAnchor),
                           view.topAnchor.constraint(equalTo: container.topAnchor)]
        if let viewport {
            let dimensions = [view.widthAnchor.constraint(equalToConstant: viewport.width),
                              view.heightAnchor.constraint(equalToConstant: viewport.height)]
            for dimension in dimensions { dimension.identifier = "alfalfa.viewport" }
            constraints += dimensions
        } else {
            constraints += [view.trailingAnchor.constraint(equalTo: container.trailingAnchor),
                            view.bottomAnchor.constraint(equalTo: container.bottomAnchor)]
        }
        NSLayoutConstraint.activate(constraints)
        coordinator.constraints = constraints
        coordinator.viewport = viewport
    }
}

/// Mantiene el tamaño CSS después del redondeo visual de AppKit sobre la vista escalada.
private final class BrowserViewportContainer: NSView {
    weak var webView: WKWebView?
    var viewport: CGSize?

    override func layout() {
        super.layout()
        guard let webView, let viewport else { return }
        if webView.frame.size != viewport { webView.setFrameSize(viewport) }
    }
}

/// Botón de la cabecera de la sesión que abre y cierra el navegador. Antes de usarlo por primera vez el
/// navegador no existe: el botón lo crea (`open`).
struct BrowserToggle: View {

    // MARK: - Propiedades

    let browser: BrowserEngine?
    @ObservedObject var terminals: SessionTerminals
    let open: () -> BrowserEngine

    var body: some View {
        if let browser {
            ObservedToggle(browser: browser, terminals: terminals)
        } else {
            BrowserToggleButton(isOpen: false) {
                open().isPanelOpen = true
                terminals.close()
            }
        }
    }

    private struct ObservedToggle: View {
        @ObservedObject var browser: BrowserEngine
        @ObservedObject var terminals: SessionTerminals

        var body: some View {
            BrowserToggleButton(isOpen: browser.isPanelOpen) {
                browser.isPanelOpen.toggle()
                if browser.isPanelOpen { terminals.close() }
            }
        }
    }
}

private struct BrowserToggleButton: View {
    let isOpen: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Image(systemName: "globe").foregroundStyle(isOpen ? Theme.alfalfa : Theme.secondary)
                .frame(width: 26, height: 26).contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .keyboardShortcut("b", modifiers: [.command, .shift])
        .help(isOpen ? "Cerrar el navegador (⇧⌘B)" : "Abrir el navegador de la sesión (⇧⌘B)")
        .accessibilityLabel(isOpen ? "Cerrar el navegador" : "Abrir el navegador")
    }
}

/// A la derecha de la conversación: el navegador si está abierto; si no, la terminal o los detalles.
/// Abrir la terminal cierra el navegador, y al revés: también cuando lo abre el agente, para que el
/// botón de la terminal no necesite dos clics.
struct SessionBrowserSlot<Other: View>: View {

    // MARK: - Propiedades

    let browser: BrowserEngine?
    @ObservedObject var terminals: SessionTerminals
    @ViewBuilder let other: () -> Other

    var body: some View {
        if let browser { Slot(browser: browser, terminals: terminals, other: other) } else { other() }
    }

    private struct Slot: View {
        @ObservedObject var browser: BrowserEngine
        @ObservedObject var terminals: SessionTerminals
        let other: () -> Other

        var body: some View {
            Group {
                if browser.isPanelOpen { BrowserPane(browser: browser) } else { other() }
            }
            .onChange(of: terminals.isOpen) { _, open in if open { browser.isPanelOpen = false } }
            .onChange(of: browser.isPanelOpen) { _, open in if open && terminals.isOpen { terminals.close() } }
            .onAppear { if browser.isPanelOpen && terminals.isOpen { terminals.close() } }
        }
    }
}
