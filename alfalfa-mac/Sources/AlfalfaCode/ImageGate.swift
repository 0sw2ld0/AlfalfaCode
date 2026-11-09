import AlfalfaKit
import SwiftUI

/// Las imágenes según el modelo elegido: con uno que no las ve no se adjuntan; con visión
/// desconocida, sí. Lo usan el compositor de la sesión y el del encargo nuevo.
extension AppModel {
    /// Si el modelo ve imágenes, según `--modelos --json`; nil si no se sabe o no está en la lista.
    func vision(of name: String) -> Bool? { info(for: name)?.vision }

    func allowsImages(_ name: String) -> Bool { ImageSupport.allows(vision(of: name)) }

    /// Modelos con visión que el usuario tiene, para ofrecer el cambio.
    func imageAlternatives(to name: String) -> [ModelChoice] {
        ImageSupport.alternatives(to: name, in: models)
    }
}

/// El aviso breve que sale al pegar o soltar una imagen con un modelo que no las ve.
@MainActor
final class ImageNotice: ObservableObject {
    @Published private(set) var text: String?
    private var clear: Task<Void, Never>?

    func show(_ text: String = ImageSupport.notice + ": la imagen no se adjuntó.") {
        self.text = text
        clear?.cancel()
        clear = Task { [weak self] in
            try? await Task.sleep(for: .seconds(4))
            guard !Task.isCancelled else { return }
            self?.text = nil
        }
    }
}

struct ImageNoticeLine: View {
    @ObservedObject var notice: ImageNotice

    var body: some View {
        if let text = notice.text {
            Label(text, systemImage: "eye.slash")
                .font(.system(size: 12)).foregroundStyle(Theme.warning)
                .frame(maxWidth: .infinity, alignment: .leading)
                .accessibilityAddTraits(.updatesFrequently)
        }
    }
}

/// Al enviar con imágenes adjuntas a un modelo que no las ve: cambiar a uno con visión o quitarlas.
struct ImageChoiceButtons: View {
    let alternatives: [ModelChoice]
    let switchTo: (ModelChoice) -> Void
    let dropImages: () -> Void

    var body: some View {
        ForEach(alternatives.prefix(2), id: \.model) { choice in
            Button("Cambiar a \(choice.label) y enviar") { switchTo(choice) }
        }
        Button("Quitar las imágenes y enviar", role: .destructive) { dropImages() }
        Button("Cancelar", role: .cancel) {}
    }
}

extension ImageChoiceButtons {
    static let title = ImageSupport.notice

    static func message(model: String, alternatives: [ModelChoice]) -> String {
        alternatives.isEmpty
            ? "\(model) no puede mirar las imágenes adjuntas y no tienes otro modelo con visión. "
                + "Puedes enviar el mensaje sin ellas."
            : "\(model) no puede mirar las imágenes adjuntas. Cambia a un modelo con visión o envía el mensaje sin ellas."
    }
}
