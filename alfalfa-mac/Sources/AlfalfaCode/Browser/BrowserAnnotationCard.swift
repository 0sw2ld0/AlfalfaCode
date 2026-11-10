import AlfalfaKit
import AppKit
import SwiftUI

struct BrowserAnnotationCard: View {
    @ObservedObject var browser: BrowserEngine
    @State private var comment = ""

    var body: some View {
        if browser.isSelectingAnnotation {
            HStack {
                Text("Pulsa un elemento o arrastra un área para comentar.")
                Spacer()
                Button("Cancelar") { browser.cancelAnnotation() }
            }
            .font(.system(size: 12)).padding(10).background(Theme.card)
        } else if let annotation = browser.annotation {
            VStack(alignment: .leading, spacing: 8) {
                HStack(alignment: .top) {
                    if let image = NSImage(data: annotation.png) {
                        Image(nsImage: image).resizable().scaledToFit().frame(maxWidth: 100, maxHeight: 64)
                    }
                    VStack(alignment: .leading, spacing: 3) {
                        Text("Comentar [\(annotation.ref)]").fontWeight(.semibold)
                        Text(annotation.text).lineLimit(2).foregroundStyle(Theme.secondary)
                    }
                    Spacer()
                }
                TextField("Por ejemplo: este botón, más grande", text: $comment, axis: .vertical)
                    .lineLimit(1...4).textFieldStyle(.roundedBorder)
                    .accessibilityLabel("Comentario sobre el elemento")
                HStack {
                    Button("Cancelar") { browser.cancelAnnotation() }
                    Spacer()
                    Button("Añadir al mensaje") { browser.submitAnnotation(comment: comment) }
                        .disabled(comment.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                }
            }
            .font(.system(size: 12)).padding(10).background(Theme.card)
            .onAppear { comment = "" }
            .onChange(of: browser.annotation?.id) { comment = "" }
        }
    }
}

extension LiveSession {
    /// Prepara el borrador: la persona todavía puede editarlo y elegir cuándo enviarlo.
    func addBrowserAnnotation(_ annotation: BrowserAnnotation, comment: String) {
        do {
            let folder = FileManager.default.temporaryDirectory.appendingPathComponent("alfalfa-anotaciones")
            try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
            let path = folder.appendingPathComponent(annotation.id.uuidString + ".png")
            try annotation.png.write(to: path, options: .atomic)
            draftImages.append(path)
            let message = annotation.message(comment: comment)
            pendingDraft = [pendingDraft, message].compactMap { $0 }.joined(separator: "\n\n")
        } catch {
            addNotice("No se pudo adjuntar el comentario: " + error.localizedDescription)
        }
    }
}
