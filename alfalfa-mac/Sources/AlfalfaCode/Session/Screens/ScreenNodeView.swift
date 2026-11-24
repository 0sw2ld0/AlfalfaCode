import AlfalfaKit
import SwiftUI

/// Un nodo de una pantalla del agente y sus hijos. Un componente fuera del catálogo base no se dibuja.
struct ScreenNodeView: View {

    // MARK: - Propiedades

    let node: ScreenNode
    let depth: Int
    let context: ScreenContext

    var body: some View {
        if depth < Screen.maxDepth {
            component
        }
    }

    // MARK: - Propiedades privadas

    @ViewBuilder private var component: some View {
        switch node.component {
        case "Stack": stack
        case "Card": card
        case "Text": text
        case "Table": ScreenTable(node: node, context: context)
        case "List": ScreenList(node: node, context: context)
        case "Button": button
        case "Form": ScreenForm(node: node, depth: depth, context: context)
        case "Input": ScreenInput(node: node, context: context)
        case "Select": ScreenSelect(node: node, context: context)
        case "Chart": ScreenChart(node: node, context: context)
        case "Image": ScreenImage(node: node, context: context)
        case "Code": code
        default: EmptyView()
        }
    }

    private var spacing: CGFloat {
        switch context.text("gap", of: node) {
        case "none": return 0
        case "small": return 6
        case "large": return 20
        default: return 12
        }
    }

    @ViewBuilder private var stack: some View {
        if context.text("direction", of: node) == "horizontal" {
            HStack(alignment: .center, spacing: spacing) { children }
        } else {
            VStack(alignment: .leading, spacing: spacing) { children }
        }
    }

    private var card: some View {
        VStack(alignment: .leading, spacing: 10) {
            if let title = context.text("title", of: node) {
                VStack(alignment: .leading, spacing: 2) {
                    Text(title).font(.system(size: 15, weight: .semibold)).foregroundStyle(Theme.text)
                    if let subtitle = context.text("subtitle", of: node) {
                        Text(subtitle).font(.system(size: 12.5)).foregroundStyle(Theme.muted)
                    }
                }
            }
            children
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(depth == 0 ? 0 : 14)
        .overlay(RoundedRectangle(cornerRadius: 10).stroke(depth == 0 ? .clear : Theme.border))
        .contentShape(Rectangle())
        .onTapGesture { if context.has("press", node) { context.fire("press", node, nil) } }
    }

    private var text: some View {
        let style = context.text("style", of: node) ?? "body"
        let font: Font = switch style {
        case "title": .system(size: 17, weight: .semibold)
        case "subtitle": .system(size: 14.5, weight: .semibold)
        case "caption", "muted": .system(size: 12.5)
        default: Theme.body
        }
        return Text(context.text("text", of: node) ?? "")
            .font(font)
            .foregroundStyle(style == "caption" || style == "muted" ? Theme.muted : Theme.text)
            .textSelection(.enabled)
            .fixedSize(horizontal: false, vertical: true)
    }

    private var button: some View {
        ScreenButton(title: context.text("label", of: node) ?? "",
                     variant: context.text("variant", of: node) ?? "secondary") {
            context.fire("press", node, nil)
        }
        .disabled(node.prop("disabled", in: context.state)?.bool == true)
    }

    private var code: some View {
        ScrollView(.horizontal) {
            Text(context.text("code", of: node) ?? "").font(Theme.mono).foregroundStyle(Theme.text)
                .textSelection(.enabled).padding(10)
        }
        .background(RoundedRectangle(cornerRadius: 8).fill(Theme.field))
    }

    private var children: some View {
        ForEach(context.screen.children(of: node)) { child in
            ScreenNodeView(node: child, depth: depth + 1, context: context)
        }
    }
}

/// Un botón de pantalla: `primary`, `secondary` o `danger`.
struct ScreenButton: View {

    // MARK: - Propiedades

    let title: String
    let variant: String
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Text(title)
                .font(.system(size: 13, weight: .medium))
                .foregroundStyle(variant == "primary" ? Theme.dark : variant == "danger" ? Theme.danger : Theme.text)
                .padding(.horizontal, 14).padding(.vertical, 7)
                .background(RoundedRectangle(cornerRadius: 8).fill(variant == "primary" ? Theme.alfalfa : Theme.field))
                .overlay(RoundedRectangle(cornerRadius: 8)
                    .stroke(variant == "danger" ? Theme.danger : variant == "primary" ? .clear : Theme.border))
        }
        .buttonStyle(.plain)
    }
}
