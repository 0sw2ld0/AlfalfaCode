import AlfalfaKit
import AppKit
import SwiftUI

/// `Table`: columnas `{key, label}` y filas; con `row_press`, cada fila se puede pulsar.
struct ScreenTable: View {

    // MARK: - Propiedades

    let node: ScreenNode
    let context: ScreenContext

    var body: some View {
        let columns = node.props["columns"]?.array?.compactMap(\.object) ?? []
        let rows = node.prop("rows", in: context.state)?.array ?? []
        let pressable = context.has("row_press", node)
        Grid(alignment: .leading, horizontalSpacing: 16, verticalSpacing: 0) {
            GridRow {
                ForEach(columns.indices, id: \.self) { index in
                    Text((columns[index]["label"] ?? columns[index]["key"] ?? .null).text.uppercased())
                        .font(.system(size: 11, weight: .semibold)).foregroundStyle(Theme.muted)
                        .padding(.vertical, 6)
                }
            }
            Divider()
            ForEach(rows.indices, id: \.self) { index in
                let row = rows[index].object ?? [:]
                GridRow {
                    ForEach(columns.indices, id: \.self) { column in
                        Text(row[columns[column]["key"]?.text ?? ""]?.text ?? "")
                            .font(.system(size: 13.5)).foregroundStyle(Theme.text).padding(.vertical, 7)
                    }
                }
                .contentShape(Rectangle())
                .onTapGesture { if pressable { context.fire("row_press", node, rows[index]) } }
                .accessibilityAddTraits(pressable ? .isButton : [])
                if index < rows.count - 1 { Divider() }
            }
        }
    }
}

/// `List`: elementos `{id, title, subtitle}`; con `item_press`, cada uno se puede pulsar.
struct ScreenList: View {

    // MARK: - Propiedades

    let node: ScreenNode
    let context: ScreenContext

    var body: some View {
        let items = node.prop("items", in: context.state)?.array?.compactMap(\.object) ?? []
        VStack(alignment: .leading, spacing: 0) {
            ForEach(items.indices, id: \.self) { index in
                let item = items[index]
                VStack(alignment: .leading, spacing: 2) {
                    Text(item["title"]?.text ?? "").font(.system(size: 13.5)).foregroundStyle(Theme.text)
                    if let subtitle = item["subtitle"]?.text, !subtitle.isEmpty {
                        Text(subtitle).font(.system(size: 12.5)).foregroundStyle(Theme.muted)
                    }
                }
                .padding(.vertical, 7)
                .frame(maxWidth: .infinity, alignment: .leading)
                .contentShape(Rectangle())
                .onTapGesture {
                    if context.has("item_press", node) { context.fire("item_press", node, item["id"]) }
                }
                if index < items.count - 1 { Divider() }
            }
        }
    }
}

/// `Form`: sus campos y el botón de enviar, que espera a que estén los obligatorios.
struct ScreenForm: View {

    // MARK: - Propiedades

    let node: ScreenNode
    let depth: Int
    let context: ScreenContext

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Divider()
            ForEach(context.screen.children(of: node)) { child in
                ScreenNodeView(node: child, depth: depth + 1, context: context)
            }
            ScreenButton(title: context.text("submit_label", of: node) ?? "Enviar", variant: "primary") {
                context.fire("submit", node, nil)
            }
            .disabled(!complete)
            .keyboardShortcut(.defaultAction)
        }
    }

    // MARK: - Propiedades privadas

    /// Los campos obligatorios del formulario (directos) tienen algo escrito o elegido.
    private var complete: Bool {
        context.screen.children(of: node).allSatisfy { child in
            guard child.component == "Input" || child.component == "Select", child.props["required"]?.bool == true,
                  let name = child.props["name"]?.string else { return true }
            if let values = context.state[name]?.array { return !values.isEmpty }
            return !(context.state[name]?.text ?? "").trimmingCharacters(in: .whitespaces).isEmpty
        }
    }
}

/// `Input`: texto, número, correo, fecha o varias líneas, enlazado a la clave `name` del estado.
struct ScreenInput: View {

    // MARK: - Propiedades

    let node: ScreenNode
    let context: ScreenContext

    var body: some View {
        let name = node.props["name"]?.string ?? node.id
        let kind = node.props["kind"]?.string ?? "text"
        let value = context.binding(name, number: kind == "number")
        VStack(alignment: .leading, spacing: 4) {
            ScreenLabel(text: context.text("label", of: node) ?? name, required: node.props["required"]?.bool == true)
            Group {
                if kind == "multiline" {
                    TextEditor(text: value).frame(minHeight: 70).scrollContentBackground(.hidden)
                } else {
                    TextField(context.text("placeholder", of: node) ?? "", text: value).textFieldStyle(.plain)
                }
            }
            .font(.system(size: 13.5))
            .padding(8)
            .background(RoundedRectangle(cornerRadius: 8).fill(Theme.field))
            .overlay(RoundedRectangle(cornerRadius: 8).stroke(Theme.border))
            .onChange(of: value.wrappedValue) { if context.has("change", node) { context.fire("change", node, nil) } }
        }
    }
}

/// `Select`: una opción (desplegable) o varias (casillas), enlazado a la clave `name` del estado.
struct ScreenSelect: View {

    // MARK: - Propiedades

    let node: ScreenNode
    let context: ScreenContext

    var body: some View {
        let name = node.props["name"]?.string ?? node.id
        let options = node.prop("options", in: context.state)?.array?.compactMap(\.object) ?? []
        let required = node.props["required"]?.bool == true
        VStack(alignment: .leading, spacing: 4) {
            ScreenLabel(text: context.text("label", of: node) ?? name, required: required)
            if node.props["multiple"]?.bool == true {
                ForEach(options.indices, id: \.self) { index in
                    Toggle(options[index]["label"]?.text ?? options[index]["value"]?.text ?? "",
                           isOn: chosen(name, options[index]["value"]?.text ?? ""))
                }
            } else {
                Picker("", selection: context.binding(name)) {
                    // Con `required` empieza sin opción elegida; el Form no se envía hasta elegir una.
                    if required { Text("—").tag("") }
                    ForEach(options.indices, id: \.self) { index in
                        let value = options[index]["value"]?.text ?? ""
                        Text(options[index]["label"]?.text ?? value).tag(value)
                    }
                }
                .labelsHidden()
            }
        }
        .onChange(of: context.state[name]) { if context.has("change", node) { context.fire("change", node, nil) } }
    }

    // MARK: - Métodos privados

    private func chosen(_ name: String, _ value: String) -> Binding<Bool> {
        Binding(get: { context.state[name]?.array?.contains(.string(value)) == true }, set: { on in
            var values = (context.state[name]?.array ?? []).filter { $0 != .string(value) }
            if on { values.append(.string(value)) }
            context.values.wrappedValue[name] = .array(values)
        })
    }
}

/// La etiqueta de un campo, con «*» si es obligatorio.
struct ScreenLabel: View {
    let text: String
    let required: Bool

    var body: some View {
        Text(text + (required ? " *" : "")).font(.system(size: 12)).foregroundStyle(Theme.muted)
    }
}

/// `Image`: sólo `https:` o `data:image/…;base64,`. Sin una fuente válida enseña su `alt`.
struct ScreenImage: View {

    // MARK: - Constantes

    private static let inlineTypes = ["png", "jpeg", "gif", "webp"]

    // MARK: - Propiedades

    let node: ScreenNode
    let context: ScreenContext

    var body: some View {
        let source = context.text("src", of: node) ?? ""
        let alt = context.text("alt", of: node) ?? ""
        Group {
            if let picture = Self.picture(source, fill: false) {
                picture
            } else {
                Text(alt).font(.system(size: 13)).foregroundStyle(Theme.muted)
            }
        }
        .frame(maxWidth: 520, maxHeight: 360, alignment: .leading)
        .clipShape(RoundedRectangle(cornerRadius: 8))
        .accessibilityLabel(alt)
    }

    // MARK: - Métodos públicos

    /// La imagen de una fuente válida (`https:` o `data:image/…;base64,`); nil si no lo es. `fill` la recorta para
    /// llenar su marco (la de una tarjeta); si no, se ve entera.
    @ViewBuilder static func picture(_ source: String, fill: Bool = true) -> (some View)? {
        if let image = inline(source) {
            Image(nsImage: image).resizable().aspectRatio(contentMode: fill ? .fill : .fit)
        } else if let url = URL(string: source), url.scheme == "https" {
            AsyncImage(url: url) { image in
                image.resizable().aspectRatio(contentMode: fill ? .fill : .fit)
            } placeholder: { ProgressView() }
        }
    }

    // MARK: - Métodos privados

    private static func inline(_ source: String) -> NSImage? {
        guard source.hasPrefix("data:image/"), let comma = source.firstIndex(of: ",") else { return nil }
        let header = source[..<comma]
        guard header.hasSuffix(";base64"), inlineTypes.contains(where: { header.hasPrefix("data:image/" + $0) }),
              let data = Data(base64Encoded: String(source[source.index(after: comma)...])) else { return nil }
        return NSImage(data: data)
    }
}

/// Lo que una tarjeta lleva además de título y subtítulo: sus etiquetas y su detalle, que se despliega con «Ver más»
/// sin volver a llamar al agente.
struct ScreenCardExtras: View {

    // MARK: - Propiedades

    let tags: [String]
    let detail: String?

    var body: some View {
        if !tags.isEmpty {
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 4) {
                    ForEach(Array(tags.prefix(8).enumerated()), id: \.offset) { _, tag in
                        Text(tag).font(.system(size: 11)).foregroundStyle(Theme.text)
                            .padding(.horizontal, 8).padding(.vertical, 2)
                            .background(Capsule().fill(Theme.alfalfa.opacity(0.16)))
                    }
                }
            }
        }
        if let detail, !detail.isEmpty {
            DisclosureGroup(isExpanded: $expanded) {
                Text(detail).font(.system(size: 13)).foregroundStyle(Theme.text).textSelection(.enabled)
                    .fixedSize(horizontal: false, vertical: true).padding(.top, 4)
            } label: {
                Text(expanded ? "Ver menos" : "Ver más").font(.system(size: 12.5, weight: .medium))
                    .foregroundStyle(Theme.alfalfa)
            }
        }
    }

    // MARK: - Propiedades privadas

    @State private var expanded = false
}
