import SwiftUI

/// Ajustes ▸ Actualizaciones: buscar una versión nueva de AlfalfaCode.
struct UpdateSettings: View {

    // MARK: - Propiedades

    @EnvironmentObject private var model: AppModel

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            SettingsTitle(text: "Actualizaciones")
            SettingsBlock(title: "Buscar actualización",
                          detail: "Compara la versión instalada con la última del repositorio de AlfalfaCode. "
                              + "Si la versión nueva cambia la app de Mac, hay que volver a compilarla.") {
                HStack(spacing: 10) {
                    Button("Buscar actualización") { model.checkForUpdate() }
                        .disabled(busy)
                    if busy {
                        ProgressView().controlSize(.small)
                        Text(model.update == .updating ? "Actualizando…" : "Buscando…")
                            .font(.system(size: 12)).foregroundStyle(Theme.secondary)
                    }
                }
            }
        }
    }

    // MARK: - Propiedades privadas

    private var busy: Bool { model.update == .checking || model.update == .updating }
}
