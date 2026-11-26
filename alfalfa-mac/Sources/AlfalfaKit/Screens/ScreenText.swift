import Foundation

/// Una pantalla como texto, para verla en «Texto» y copiarla: markdown legible o el bloque `pacai.ui/1` en JSON.
///
/// Las dos salen de lo que tiene la app, que siempre recibe `pacai.ui/1`: con PACAI UI Lang encendido, el motor ya lo
/// tradujo antes de mandarlo, y el texto que escribió el modelo sólo queda en la transcripción de la sesión.
public extension Screen {

    // MARK: - Propiedades privadas

    /// Los nodos desde `root`, en profundidad; los que no cuelgan de ningún sitio, al final por id.
    private var orderedNodes: [ScreenNode] {
        var seen = Set<String>()
        var ordered: [ScreenNode] = []
        func visit(_ id: String) {
            guard let node = nodes[id], seen.insert(id).inserted else { return }
            ordered.append(node)
            node.children.forEach(visit)
        }
        visit(Self.root)
        ordered += nodes.keys.filter { !seen.contains($0) }.sorted().compactMap { nodes[$0] }
        return ordered
    }

    // MARK: - Métodos públicos

    /// La pantalla como markdown: títulos y textos tal cual, una tabla como tabla, las tarjetas como lista, los campos
    /// con su etiqueta y su valor y los gráficos como sus datos. `state` es el estado que se ve (con lo que escribió
    /// el usuario); por defecto, el que dio el agente. Los botones no salen: no son contenido.
    func markdown(state: [String: ScreenValue]? = nil) -> String {
        guard let root = nodes[Self.root] else { return alt }
        let writer = ScreenMarkdown(screen: self, state: state ?? self.state)
        return writer.blocks(of: root, depth: 0).joined(separator: "\n\n")
    }

    /// El bloque `pacai.ui/1` que deja la pantalla como se ve: un `replace` con sus nodos (en el orden del árbol) y
    /// el estado. JSON con sangría y claves ordenadas, para que dos copias de lo mismo sean iguales.
    func rawJSON(state: [String: ScreenValue]? = nil) -> String {
        var data: [String: Any] = ["view": name, "op": "replace", "rev": rev, "nodes": orderedNodes.map(\.data)]
        let state = state ?? self.state
        if !state.isEmpty { data["state"] = state.mapValues(\.json) }
        if !alt.isEmpty { data["alt"] = alt }
        if placement != .inline { data["placement"] = placement.rawValue }
        guard let json = try? JSONSerialization.data(withJSONObject: data,
                                                     options: [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes])
        else { return "" }
        return String(decoding: json, as: UTF8.self)
    }
}

extension ScreenNode {

    /// El nodo como lo escribe `pacai.ui/1`.
    var data: [String: Any] {
        var data: [String: Any] = ["id": id, "component": component]
        if !props.isEmpty { data["props"] = props.mapValues(\.json) }
        if !children.isEmpty { data["children"] = children }
        if !events.isEmpty {
            data["events"] = events.mapValues { actions in actions.map(\.data) }
        }
        return data
    }
}

extension ScreenAction {

    /// La acción como la escribe `pacai.ui/1`.
    var data: [String: Any] {
        switch self {
        case let .send(name, keys):
            var data: [String: Any] = ["send": name]
            if let keys { data["with"] = keys }
            return data
        case let .set(values): return ["set": values.mapValues(\.json)]
        case let .openURL(url): return ["open_url": url.absoluteString]
        }
    }
}

/// Recorre el árbol de una pantalla y escribe cada nodo como bloques de markdown.
private struct ScreenMarkdown {

    let screen: Screen
    let state: [String: ScreenValue]

    /// Los bloques de un nodo y de sus hijos, separados luego por una línea en blanco.
    func blocks(of node: ScreenNode, depth: Int) -> [String] {
        guard depth < Screen.maxDepth else { return [] }
        switch node.component {
        case "Text": return text(node)
        case "Card": return card(node, depth: depth)
        case "Table": return table(node)
        case "List": return list(node)
        case "Input", "Select": return field(node)
        case "Chart": return chart(node)
        case "Image": return image(node)
        case "Code": return code(node)
        case "Button": return []
        default: return children(of: node, depth: depth)       // Stack, Form y los componentes propios
        }
    }

    // MARK: - Componentes

    private func children(of node: ScreenNode, depth: Int) -> [String] {
        let children = screen.children(of: node)
        // Varias tarjetas seguidas (un Stack grid) son una lista: sin línea en blanco entre ellas.
        if children.count > 1, children.allSatisfy({ $0.component == "Card" }) {
            return [children.map { cardItem($0, depth: depth + 1) }.joined(separator: "\n")]
        }
        return children.flatMap { blocks(of: $0, depth: depth + 1) }
    }

    private func text(_ node: ScreenNode) -> [String] {
        let text = value("text", node)
        guard !text.isEmpty else { return [] }
        switch value("style", node) {
        case "title": return ["## " + Self.line(text)]
        case "subtitle": return ["### " + Self.line(text)]
        case "caption", "muted": return ["*" + Self.line(text) + "*"]
        default: return [text]
        }
    }

    /// La tarjeta raíz es la pantalla entera: su título va de encabezado. Una tarjeta suelta dentro, de elemento.
    private func card(_ node: ScreenNode, depth: Int) -> [String] {
        guard depth == 0 else { return [cardItem(node, depth: depth)] }
        var head: [String] = []
        if !value("title", node).isEmpty { head.append("## " + Self.line(value("title", node))) }
        head += [value("subtitle", node), value("meta", node), tags(node)].filter { !$0.isEmpty }
        let detail = value("detail", node)
        return [head.joined(separator: "\n"), detail].filter { !$0.isEmpty } + children(of: node, depth: depth)
    }

    /// `- **Título** · subtítulo`, y debajo, sangrados, la meta, las etiquetas, el detalle y lo que lleve dentro.
    private func cardItem(_ node: ScreenNode, depth: Int) -> String {
        let title = [value("title", node), value("subtitle", node)].filter { !$0.isEmpty }
        var first = title.isEmpty ? "-" : "- **" + Self.line(title[0]) + "**"
        if title.count > 1 { first += " · " + Self.line(title[1]) }
        let inner = [value("meta", node), tags(node), value("detail", node)].filter { !$0.isEmpty }
            + children(of: node, depth: depth)
        let indented = inner.map { block in
            block.split(separator: "\n", omittingEmptySubsequences: false).map { "  " + $0 }.joined(separator: "\n")
        }
        return ([first] + indented).joined(separator: "\n")
    }

    private func tags(_ node: ScreenNode) -> String {
        let tags = (node.prop("tags", in: state)?.array ?? []).map(\.text).filter { !$0.isEmpty }
        return tags.isEmpty ? "" : tags.map { "`" + $0 + "`" }.joined(separator: " ")
    }

    private func table(_ node: ScreenNode) -> [String] {
        let columns = node.prop("columns", in: state)?.array?.compactMap(\.object) ?? []
        guard !columns.isEmpty else { return [] }
        let keys = columns.map { $0["key"]?.text ?? "" }
        let labels = columns.map { ($0["label"] ?? $0["key"] ?? .null).text }
        let rows = (node.prop("rows", in: state)?.array ?? []).map { row in
            keys.map { row.object?[$0]?.text ?? "" }
        }
        return [Self.table(header: labels, rows: rows)]
    }

    private func list(_ node: ScreenNode) -> [String] {
        let items = node.prop("items", in: state)?.array?.compactMap(\.object) ?? []
        guard !items.isEmpty else { return [] }
        return [items.map { item in
            let subtitle = item["subtitle"]?.text ?? ""
            return "- " + Self.line(item["title"]?.text ?? "") + (subtitle.isEmpty ? "" : " — " + Self.line(subtitle))
        }.joined(separator: "\n")]
    }

    /// `**Etiqueta:** valor`; en un Select, las etiquetas de las opciones elegidas.
    private func field(_ node: ScreenNode) -> [String] {
        let name = node.props["name"]?.string ?? node.id
        let label = node.text("label", in: state) ?? name
        let current = state[name] ?? .null
        let chosen = current.array ?? (current == .null || current.text.isEmpty ? [] : [current])
        var shown = chosen.map(\.text)
        if node.component == "Select" {
            let options = node.prop("options", in: state)?.array?.compactMap(\.object) ?? []
            shown = shown.map { value in
                options.first { $0["value"]?.text == value }?["label"]?.text ?? value
            }
        }
        let text = shown.joined(separator: ", ")
        let head = "**" + Self.line(label) + ":**"
        if node.props["kind"]?.string == "multiline", text.contains("\n") { return [head + "\n" + text] }
        return [head + " " + (text.isEmpty ? "—" : Self.line(text))]
    }

    /// Una tabla con una columna por serie y una fila por etiqueta.
    private func chart(_ node: ScreenNode) -> [String] {
        let series = (node.prop("series", in: state)?.array ?? []).compactMap { row -> (String, [ScreenValue])? in
            guard let fields = row.object, let values = fields["values"]?.array else { return nil }
            return (fields["name"]?.text ?? "", values)
        }
        guard !series.isEmpty else { return [] }
        let labels = node.prop("labels", in: state)?.array?.map(\.text) ?? []
        let count = max(labels.count, series.map(\.1.count).max() ?? 0)
        let header = ["Etiqueta"] + series.enumerated().map { $1.0.isEmpty ? "Serie \($0 + 1)" : $1.0 }
        let rows = (0..<count).map { index in
            [index < labels.count ? labels[index] : String(index + 1)]
                + series.map { index < $0.1.count ? $0.1[index].text : "" }
        }
        return [Self.table(header: header, rows: rows)]
    }

    private func image(_ node: ScreenNode) -> [String] {
        let source = value("src", node)
        let alt = value("alt", node)
        if source.hasPrefix("https:") { return ["![" + Self.line(alt) + "](" + source + ")"] }
        return alt.isEmpty ? [] : ["*Imagen:* " + Self.line(alt)]
    }

    private func code(_ node: ScreenNode) -> [String] {
        let code = value("code", node)
        let fence = code.contains("```") ? "~~~~" : "```"
        return [fence + value("language", node) + "\n" + code + "\n" + fence]
    }

    // MARK: - Ayudantes

    private func value(_ key: String, _ node: ScreenNode) -> String { node.text(key, in: state) ?? "" }

    /// Un texto en una sola línea (un título, una celda).
    private static func line(_ text: String) -> String {
        text.split(whereSeparator: \.isNewline).joined(separator: " ")
    }

    private static func table(header: [String], rows: [[String]]) -> String {
        func row(_ cells: [String]) -> String {
            "| " + cells.map { line($0).replacingOccurrences(of: "|", with: "\\|") }.joined(separator: " | ") + " |"
        }
        return ([row(header), "|" + String(repeating: " --- |", count: header.count)] + rows.map(row))
            .joined(separator: "\n")
    }
}
