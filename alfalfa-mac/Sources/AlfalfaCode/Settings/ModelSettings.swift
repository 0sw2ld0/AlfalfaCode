import AlfalfaKit
import SwiftUI

/// Ajustes ▸ Modelo: con qué modelo, esfuerzo y modo de permisos empieza cada encargo nuevo.
struct ModelSettings: View {

    // MARK: - Propiedades

    @EnvironmentObject private var model: AppModel

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            SettingsTitle(text: "Modelo")
            SettingsBlock(title: "Modelo por defecto",
                          detail: "Cada encargo nuevo empieza con este modelo. Puedes cambiarlo en el encargo.") {
                Menu {
                    ModelMenuItems(groups: model.modelGroups, selected: model.defaultModel, label: model.label(for:),
                                   providerName: model.providerName) { model.defaultModel = $0 }
                } label: {
                    Text(model.label(for: model.defaultModel))
                }
                .fixedSize().accessibilityLabel("Modelo por defecto")
            }
            SettingsBlock(title: "Esfuerzo", detail: "Cuánto piensa el modelo antes de contestar.") {
                Picker("Esfuerzo", selection: binding(\.defaultEffort)) {
                    ForEach(Presentation.efforts, id: \.value) { Text($0.label).tag($0.value) }
                }
                .labelsHidden().pickerStyle(.segmented).frame(maxWidth: 420)
            }
            SettingsBlock(title: "Permisos", detail: model.defaultMode.summary) {
                Picker("Permisos", selection: binding(\.defaultMode)) {
                    ForEach(PermissionMode.allCases, id: \.self) { mode in
                        Label(mode.title, systemImage: mode.symbol).tag(mode)
                    }
                }
                .labelsHidden().fixedSize().accessibilityLabel("Modo de permisos por defecto")
            }
        }
    }

    // MARK: - Métodos privados

    private func binding<Value>(_ path: ReferenceWritableKeyPath<AppModel, Value>) -> Binding<Value> {
        Binding(get: { model[keyPath: path] }, set: { model[keyPath: path] = $0 })
    }
}
