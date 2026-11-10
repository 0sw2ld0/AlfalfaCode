import SwiftUI

struct BrowserSettings: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            SettingsTitle(text: "Navegador")
            BrowserSiteSettings(project: model.browserSiteProject)
        }
    }
}
