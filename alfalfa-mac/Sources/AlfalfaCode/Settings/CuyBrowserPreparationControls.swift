import AlfalfaKit
import SwiftUI

struct CuyBrowserPreparationControls: View {

    // MARK: - Propiedades

    let launcher: URL?
    let directory: URL
    var allowsSourceChoice = false

    @AppStorage(BrowserEnginePreferences.pathKey) private var fromPath = false
    @State private var preparation: CuyBrowserPreparation?
    @State private var error: String?
    @State private var loading = true
    @State private var downloading = false
    @State private var confirming = false

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            if allowsSourceChoice {
                Toggle("Usar mi CuyBrowser de PATH", isOn: $fromPath).disabled(downloading)
            }
            if loading || downloading {
                HStack(spacing: 8) {
                    ProgressView().controlSize(.small)
                    Text(downloading ? "Descargando Chromium…" : "Buscando un navegador compatible…")
                }
            } else if let error {
                Text(error).foregroundStyle(Theme.danger)
                Button("Volver a comprobar") { Task { await consultar() } }.controlSize(.small)
            } else if let preparation {
                resultado(preparation)
            }
        }
        .font(.caption)
        .task(id: [directory.path, String(fromPath)]) { await consultar() }
        .confirmationDialog("¿Descargar Chromium de Playwright?", isPresented: $confirming, titleVisibility: .visible) {
            Button("Descargar Chromium") { Task { await consultar(descargar: true) } }
            Button("Cancelar", role: .cancel) {}
        } message: {
            Text("La descarga ocupa unos 190 MB y se comparte entre tus proyectos de AlfalfaCode.")
        }
    }

    // MARK: - Métodos privados

    @ViewBuilder private func resultado(_ preparation: CuyBrowserPreparation) -> some View {
        switch preparation.status {
        case .available:
            let source = preparation.source == "incluido" ? "CuyBrowser incluido" : "CuyBrowser de PATH"
            Label("\(preparation.browser ?? "Chromium") disponible · \(source)", systemImage: "checkmark.circle")
                .foregroundStyle(Theme.secondary)
        case .needsChromium:
            Text(preparation.message ?? "No se encontró un navegador Chromium compatible.")
                .foregroundStyle(Theme.secondary)
            Button("Descargar Chromium (unos 190 MB)…") { confirming = true }.controlSize(.small)
        case .missing, .error:
            Text(preparation.message ?? "No se pudo preparar CuyBrowser.").foregroundStyle(Theme.danger)
        }
    }

    private func consultar(descargar: Bool = false) async {
        guard !downloading else { return }
        guard let launcher else {
            loading = false
            error = "El motor de AlfalfaCode no está disponible."
            return
        }
        loading = !descargar
        downloading = descargar
        error = nil
        var arguments = descargar ? ["--descargar-chromium", "--confirmar-descarga-chromium"] : ["--cuybrowser-estado"]
        arguments.append(fromPath ? "--cuybrowser-desde-path" : "--cuybrowser-solo-incluido")
        arguments.append("--json")
        defer {
            if !Task.isCancelled {
                loading = false
                downloading = false
            }
        }
        do {
            let messages = try await AlfalfaProcess.query(launcher: launcher, arguments: arguments,
                                                          directory: directory)
            guard !Task.isCancelled else { return }
            for case let .cuyBrowserPreparation(value) in messages {
                preparation = value
                return
            }
            error = "AlfalfaCode no devolvió el estado de CuyBrowser."
        } catch {
            if !Task.isCancelled { self.error = error.localizedDescription }
        }
    }
}
