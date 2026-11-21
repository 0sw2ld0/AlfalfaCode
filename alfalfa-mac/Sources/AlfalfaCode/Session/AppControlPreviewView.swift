import AlfalfaKit
import SwiftUI

struct AppControlPreviewView: View {
    let store: AppControlPreviewStore
    let running: Bool

    @AppStorage("panel.controlApps.abierto") private var open = true
    @State private var preview: AppControlPreview?
    @State private var image: NSImage?
    @State private var expanded = false

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            if let preview {
                PanelSection(title: "Control de apps", open: $open) {
                    VStack(alignment: .leading, spacing: 6) {
                        Text(preview.app).font(.subheadline.weight(.semibold)).foregroundStyle(Theme.text)
                        Text(preview.action).font(.caption).foregroundStyle(Theme.secondary)
                        if let image {
                            Button { expanded = true } label: {
                                Image(nsImage: image).resizable().scaledToFit()
                                    .frame(maxWidth: .infinity, maxHeight: 144)
                                    .background(Theme.border, in: RoundedRectangle(cornerRadius: 6))
                                    .clipShape(RoundedRectangle(cornerRadius: 6))
                            }
                            .buttonStyle(.plain)
                            .accessibilityLabel("Ampliar captura de \(preview.app)")
                            .help("Ampliar la última captura")
                        } else {
                            Text(preview.notice.isEmpty ? "Captura no disponible" : preview.notice)
                                .font(.caption).foregroundStyle(Theme.secondary)
                        }
                    }
                }
                Divider().overlay(Theme.border).padding(.vertical, 12)
            }
        }
        .task(id: running) { await observe() }
        .sheet(isPresented: $expanded) {
            VStack(spacing: 14) {
                if let image {
                    Image(nsImage: image).resizable().scaledToFit()
                        .accessibilityLabel("Captura de \(preview?.app ?? "la aplicación")")
                } else {
                    Text("Captura no disponible").frame(maxHeight: .infinity)
                }
                Button("Cerrar captura") { expanded = false }.keyboardShortcut(.cancelAction)
            }
            .padding(20).frame(idealWidth: 680, idealHeight: 480)
            .frame(minWidth: 360, minHeight: 270)
        }
    }

    private func observe() async {
        repeat {
            let previous = preview
            let store = store
            let latest = await Task.detached(priority: .utility) {
                let activity = store.load()
                let data = activity == previous ? nil : activity.flatMap { store.capture(for: $0) }
                return (activity, data)
            }.value
            guard !Task.isCancelled else { return }
            if latest.0 != previous {
                preview = latest.0
                image = latest.1.flatMap { NSImage(data: $0) }
            }
            if running { try? await Task.sleep(for: .milliseconds(550)) }
        } while running && !Task.isCancelled
    }
}
