import AlfalfaKit
import SwiftUI

struct BrowserFirstRunCard: View {

    // MARK: - Propiedades

    @Environment(\.dismiss) private var dismiss
    @EnvironmentObject private var model: AppModel
    @AppStorage(BrowserEnginePreferences.engineKey) private var engine = "webkit"

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("Búsqueda y navegación").font(.title2.bold())
            if let search = model.webSearch {
                Toggle("Acceso web del agente", isOn: Binding(get: { search.mode != "apagado" }, set: { active in
                    Task { await model.setWebSearchMode(active ? "auto" : "apagado") }
                }))
                if search.needsInstall {
                    Text("Preparando la búsqueda web…").font(.caption).foregroundStyle(Theme.secondary)
                }
            }
            Toggle("Navegador de la app · WebKit", isOn: motor("webkit")).disabled(!webAccess)
            Toggle("Navegador Chromium · CuyBrowser", isOn: motor("cuybrowser")).disabled(!webAccess)
            if webAccess {
                CuyBrowserPreparationControls(
                    launcher: model.launcher, directory: model.folder.map { URL(fileURLWithPath: $0) }
                        ?? FileManager.default.temporaryDirectory)
            } else {
                Text("Activa el acceso web para que el agente pueda usar el navegador.")
                    .font(.caption).foregroundStyle(Theme.secondary)
            }
            Text("El motor elegido se aplica a los nuevos encargos de la app.")
                .font(.caption).foregroundStyle(Theme.secondary)
            HStack {
                Spacer()
                Button("Listo") { dismiss() }.keyboardShortcut(.defaultAction)
            }
        }
        .padding(24)
        .frame(width: 470)
    }

    private var webAccess: Bool { model.webSearch?.mode != "apagado" }

    // MARK: - Métodos privados

    private func motor(_ value: String) -> Binding<Bool> {
        Binding(get: { webAccess && engine == value }, set: { active in
            if active { engine = value } else if engine == value { engine = "off" }
        })
    }
}
