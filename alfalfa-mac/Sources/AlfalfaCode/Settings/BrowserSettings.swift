import SwiftUI

struct BrowserSettings: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            SettingsTitle(text: "Navegador")
            BrowserEngineSettings()
            Divider().overlay(Theme.border)
            Text("Perfil de WebKit · navegador integrado").font(.system(size: 14, weight: .semibold))
            BrowserProfileSettings(project: model.browserProject, profiles: model.browserProfiles,
                                   canChange: model.canChangeBrowserProfile(for: model.browserProject),
                                   change: model.setBrowserProfile, clear: model.clearBrowserProfile)
        }
    }
}
