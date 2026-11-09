import SwiftUI

/// El aviso de «Buscar actualización». Lo llevan la ventana principal y la de Ajustes, y sólo lo
/// enseña la que está delante, para que no salga dos veces.
struct UpdateAlert: ViewModifier {

    // MARK: - Propiedades

    @EnvironmentObject private var model: AppModel
    @Environment(\.controlActiveState) private var activeState

    // MARK: - Propiedades privadas

    /// Sólo se enseña con un resultado; mientras busca o instala, el botón muestra el progreso.
    private var shown: Binding<Bool> {
        Binding(get: {
            guard activeState == .key else { return false }
            switch model.update {
            case .available, .message: return true
            default: return false
            }
        }, set: { shown in
            if !shown, activeState == .key, model.update != .updating { model.update = nil }
        })
    }

    private var title: String {
        switch model.update {
        case .available: return "Actualización disponible"
        case let .message(title, _): return title
        default: return ""
        }
    }

    // MARK: - Métodos públicos

    func body(content: Content) -> some View {
        content.alert(title, isPresented: shown, presenting: model.update) { check in
            if case let .available(info) = check, info.needsDownload, let url = info.downloadURL {
                // AlfalfaCode.app no se cambia sola (está firmada): se descarga el DMG nuevo.
                Button("Descargar") { NSWorkspace.shared.open(url); model.update = nil }
                Button("Ahora no", role: .cancel) { model.update = nil }
            } else if case .available = check {
                Button("Actualizar") { model.installUpdate() }
                Button("Ahora no", role: .cancel) { model.update = nil }
            } else {
                Button("Aceptar", role: .cancel) { model.update = nil }
            }
        } message: { check in
            switch check {
            case let .available(info) where info.needsDownload:
                Text(info.text)
            case let .available(info):
                Text("Hay una versión nueva: \(info.latest ?? "?") (tienes \(info.installed)).")
            case let .message(_, text): Text(text)
            default: EmptyView()
            }
        }
    }
}
