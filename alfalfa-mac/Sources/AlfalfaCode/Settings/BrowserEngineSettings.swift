import AlfalfaKit
import SwiftUI

struct BrowserEngineSettings: View {

    // MARK: - Propiedades

    @AppStorage(BrowserEnginePreferences.engineKey) private var engine = "webkit"
    @AppStorage(BrowserEnginePreferences.profileKey) private var profile = "alfalfa"
    @EnvironmentObject private var model: AppModel

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Motor del navegador").font(.title2.bold())
            Picker("Navegador", selection: $engine) {
                Text("WebKit · integrado").tag("webkit")
                Text("CuyBrowser · Chromium").tag("cuybrowser")
                Text("Desactivado").tag("off")
            }
            .pickerStyle(.segmented)
            if engine == "cuybrowser" {
                TextField("Perfil propio", text: $profile)
                Text("Usa letras, números, guiones o guiones bajos (hasta 64). "
                     + "Cada proyecto conserva su propio perfil.")
                    .font(.caption).foregroundStyle(Theme.secondary)
                if profile.range(of: "^[A-Za-z0-9_-]{1,64}$", options: .regularExpression) == nil {
                    Text("Corrige el nombre del perfil antes de iniciar CuyBrowser.").foregroundStyle(Theme.danger)
                }
                CuyBrowserPreparationControls(
                    launcher: model.launcher, directory: model.folder.map { URL(fileURLWithPath: $0) }
                        ?? FileManager.default.temporaryDirectory, allowsSourceChoice: true)
                Text("Abre una ventana propia de Chromium. Al iniciar pedirá permiso una vez. "
                     + "Sus diálogos confirman las acciones sensibles, también en modo Omitir.")
                    .font(.callout)
                Text("No admite JavaScript de sólo lectura, descargar una URL al proyecto ni enfocar con una tecla. "
                     + "La protección estricta de subrecursos requiere WebKit.")
                    .font(.caption).foregroundStyle(Theme.secondary)
            }
            Text("El cambio se aplica al iniciar el motor de la siguiente sesión.")
                .font(.caption).foregroundStyle(Theme.secondary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}
