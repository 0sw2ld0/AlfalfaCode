import AlfalfaKit
import SwiftUI

/// Ajustes ▸ General: el nombre del pie y la carpeta de los encargos nuevos.
struct GeneralSettings: View {

    // MARK: - Propiedades

    @EnvironmentObject private var model: AppModel
    @AppStorage(Preferences.Key.displayName) private var name = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            SettingsTitle(text: "General")
            SettingsBlock(title: "Nombre", detail: "Se muestra en el pie de la barra lateral. Déjalo vacío para "
                          + "usar el de tu cuenta del Mac (\(Preferences.shownName(saved: nil, system: account))).") {
                TextField("Nombre", text: $name, prompt: Text(account))
                    .textFieldStyle(.roundedBorder).frame(maxWidth: 320)
                    .accessibilityLabel("Nombre que se muestra")
            }
            SettingsBlock(title: "Carpeta por defecto",
                          detail: "La carpeta con la que empieza cada encargo nuevo. Sin ella, la última que usaste.") {
                HStack(spacing: 10) {
                    Image(systemName: "folder").foregroundStyle(Theme.secondary)
                    Text(model.defaultFolder.map(Presentation.abbreviate) ?? "La última que usaste")
                        .font(Theme.mono).foregroundStyle(model.defaultFolder == nil ? Theme.muted : Theme.text)
                        .lineLimit(1).truncationMode(.middle)
                        .help(model.defaultFolder ?? "")
                }
                HStack {
                    Button("Elegir…") { model.chooseDefaultFolder() }
                    if model.defaultFolder != nil {
                        Button("Usar la última") { model.defaultFolder = nil }
                    }
                }
            }
        }
    }

    // MARK: - Propiedades privadas

    private var account: String { NSFullUserName() }
}
