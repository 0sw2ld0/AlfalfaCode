import AlfalfaKit
import SwiftUI

/// Qué se aprobaría, acotado (PermissionPreview): la orden o, en una herramienta como spawn_agent,
/// un titular, sus argumentos cortos y las primeras líneas del encargo. «Ver todo» abre el texto
/// entero en una hoja con scroll y texto seleccionable (LargeTextSheet, un NSTextView).
///
/// Sólo se vuelve a calcular si cambia la petición: la tarjeta observa la sesión entera y no debe
/// volver a leer un JSON de 50 KB con cada evento.
struct PermissionCallView: View, Equatable {
    let request: PermissionRequest

    var body: some View {
        let preview = PermissionPreview(request)
        VStack(alignment: .leading, spacing: 6) {
            if let headline = preview.headline {
                Text(headline).font(.system(size: 13, weight: .semibold)).foregroundStyle(Theme.text).lineLimit(2)
            }
            if !preview.fields.isEmpty {
                VStack(alignment: .leading, spacing: 2) {
                    ForEach(preview.fields, id: \.key) { field in
                        (Text(field.key + ": ").fontWeight(.semibold) + Text(field.value))
                            .font(.system(size: 12, design: .monospaced)).foregroundStyle(Theme.secondary)
                            .lineLimit(2)
                    }
                }
            }
            if !preview.body.shown.isEmpty {
                if preview.bodyIsCommand {
                    Text(preview.body.shown).font(Theme.mono).textSelection(.enabled)
                        .lineLimit(preview.body.shownLines)
                } else {
                    // El encargo de un subagente se lee como texto: cada línea de la vista previa puede
                    // ocupar varias en la tarjeta, pero nunca más de PermissionPreview.maxBodyLines × 3.
                    Text(preview.body.shown).font(.system(size: 12.5)).foregroundStyle(Theme.text)
                        .textSelection(.enabled).lineLimit(preview.body.shownLines * 3)
                        .padding(10).frame(maxWidth: .infinity, alignment: .leading)
                        .background(Theme.field, in: RoundedRectangle(cornerRadius: 8))
                }
            }
            if preview.truncated {
                SeeAllButton(label: seeAllLabel(preview), title: preview.headline ?? "Lo que se aprobaría",
                             text: preview.full)
            }
        }
    }

    private func seeAllLabel(_ preview: PermissionPreview) -> String {
        let what = preview.bodyKey == "message" ? "Ver el encargo entero" : "Ver todo"
        return preview.body.hiddenLines > 0 ? what + " (\(preview.body.totalLines) líneas)" : what
    }
}

/// «Justificación: …» acotado a unas líneas, con «Ver todo» si hay más.
struct BoundedDetailLine: View {
    let label: String
    let preview: TextPreview
    let full: String

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack(alignment: .firstTextBaseline, spacing: 4) {
                Text(label + ":").fontWeight(.semibold)
                Text(preview.shown).textSelection(.enabled).lineLimit(preview.shownLines * 3)
            }
            if preview.truncated { SeeAllButton(label: "Ver todo", title: label, text: full) }
        }
        .font(.system(size: 12.5)).foregroundStyle(Theme.secondary)
    }
}

/// Botón que abre un texto entero en una hoja con scroll; el texto sólo se pinta al abrirla.
struct SeeAllButton: View {
    let label: String
    let title: String
    let text: String
    @State private var showing = false

    var body: some View {
        Button(label) { showing = true }
            .buttonStyle(.plain).font(.system(size: 12, weight: .medium)).foregroundStyle(Theme.link)
            .sheet(isPresented: $showing) { LargeTextSheet(title: title, text: text) }
    }
}
