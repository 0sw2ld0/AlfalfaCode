import SwiftUI

/// Un bloque de una parte de Ajustes: título, explicación y sus controles.
struct SettingsBlock<Content: View>: View {

    // MARK: - Propiedades

    let title: String
    let detail: String
    @ViewBuilder var content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title).font(.system(size: 14, weight: .semibold))
            Text(detail).font(.system(size: 12)).foregroundStyle(Theme.secondary)
                .fixedSize(horizontal: false, vertical: true)
            content
        }
        .padding(.bottom, 10)
    }
}

/// El título de una parte de Ajustes.
struct SettingsTitle: View {

    // MARK: - Propiedades

    let text: String

    var body: some View {
        Text(text).font(.system(size: 17, weight: .semibold)).padding(.bottom, 6)
    }
}
