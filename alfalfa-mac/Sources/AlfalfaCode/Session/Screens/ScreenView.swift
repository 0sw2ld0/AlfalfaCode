import AlfalfaKit
import SwiftUI

/// Una pantalla que dibujó el agente (perfil `ui` de PACAI): la tarjeta, el estado de sus campos y lo que pasa al
/// pulsar. El árbol de componentes lo pinta `ScreenNodeView`; la lógica de los eventos está en `Screen.handle`.
struct ScreenView: View {

    // MARK: - Propiedades

    let screen: Screen
    /// La copia que se puede usar; una que bajó a un mensaje posterior queda apagada.
    let live: Bool
    @ObservedObject var session: LiveSession

    var body: some View {
        content
            .padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(RoundedRectangle(cornerRadius: 14).fill(Theme.card))
            .overlay(RoundedRectangle(cornerRadius: 14).stroke(Theme.cardBorder))
            .overlay(alignment: .topTrailing) {
                if waiting { ProgressView().controlSize(.small).padding(10).accessibilityLabel("Enviando") }
            }
            .opacity(live ? 1 : 0.5)
            .disabled(!interactive)
            .accessibilityElement(children: .contain)
            .accessibilityLabel(screen.alt.isEmpty ? "Pantalla del agente" : screen.alt)
            .onChange(of: screen.rev) {
                // La pantalla cambió: manda lo que dice el agente, y lo que se envió ya tiene respuesta.
                values = screen.state
                waiting = false
            }
            .onChange(of: session.timeline.status.isRunning) { _, running in
                if !running { waiting = false }
            }
            .confirmationDialog("¿Abrir este enlace?", isPresented: opening, presenting: pendingURL) { url in
                Button("Abrir " + (url.host() ?? "el enlace")) { openURL(url) }
            } message: { url in
                Text(url.absoluteString)
            }
    }

    // MARK: - Propiedades privadas

    @Environment(\.openURL) private var openURL
    @State private var values: [String: ScreenValue]
    /// Se mandó una acción y aún no hay respuesta: los controles esperan (evita el doble envío).
    @State private var waiting = false
    @State private var pendingURL: URL?

    private var interactive: Bool { live && !waiting && session.acceptsScreenActions }

    private var opening: Binding<Bool> {
        Binding(get: { pendingURL != nil }, set: { if !$0 { pendingURL = nil } })
    }

    @ViewBuilder private var content: some View {
        if let root = screen.nodes[Screen.root] {
            ScreenNodeView(node: root, depth: 0, context: context)
        } else {
            Text(screen.alt.isEmpty ? "Preparando la pantalla…" : screen.alt)
                .font(.system(size: 13)).foregroundStyle(Theme.muted)
        }
    }

    private var context: ScreenContext {
        ScreenContext(screen: screen, values: $values) { event, node, item in
            fire(event, on: node, item: item)
        }
    }

    // MARK: - Inicializadores

    init(screen: Screen, live: Bool, session: LiveSession) {
        self.screen = screen
        self.live = live
        self.session = session
        _values = State(initialValue: screen.state)
    }

    // MARK: - Métodos privados

    private func fire(_ event: String, on node: ScreenNode, item: ScreenValue?) {
        guard interactive else { return }
        let result = screen.handle(event, on: node, state: values, item: item)
        values = result.state
        pendingURL = result.urls.first
        if let request = result.request {
            waiting = true
            session.act(request)
        }
    }
}

/// Lo que necesita cada nodo para pintarse y avisar de sus eventos.
struct ScreenContext {
    let screen: Screen
    /// El estado de los campos: lo que escribe el usuario, encima de lo que dio el agente.
    let values: Binding<[String: ScreenValue]>
    /// Un evento de un nodo: el nombre, el nodo y, en `row_press` e `item_press`, la fila o el id.
    let fire: (String, ScreenNode, ScreenValue?) -> Void

    var state: [String: ScreenValue] { values.wrappedValue }

    func text(_ key: String, of node: ScreenNode) -> String? { node.text(key, in: state) }

    func has(_ event: String, _ node: ScreenNode) -> Bool { node.events[event] != nil }

    /// Un campo de texto enlazado a la clave `name` del estado; `number` guarda números.
    func binding(_ name: String, number: Bool = false) -> Binding<String> {
        Binding(get: { state[name]?.text ?? "" }, set: { text in
            values.wrappedValue[name] = number ? Double(text).map(ScreenValue.number) ?? .string(text) : .string(text)
        })
    }
}
