import AlfalfaKit
import SwiftUI

/// Hoja que sale al elegir un modelo cuyo proveedor no tiene clave: cuál falta, dónde se
/// consigue y un campo para guardarla en el Llavero (a través de `alfalfa`, nunca en la app).
struct KeySheet: View {
    @EnvironmentObject private var model: AppModel
    let request: KeyRequest
    @State private var secret = ""
    @State private var busy = false

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Falta la clave de \(providerName)").font(.system(size: 15, weight: .semibold))
            Text("Para usar \(request.model.label) hace falta tu clave de \(providerName) "
                 + "(\(request.missing.variable)). Se guarda en el Llavero de este Mac.")
                .font(.system(size: 12.5)).foregroundStyle(Theme.secondary)
                .fixedSize(horizontal: false, vertical: true)
            if let page = request.missing.url, let url = URL(string: page) {
                Link("Conseguir la clave: \(url.host() ?? page)", destination: url).font(.system(size: 12.5))
            }
            if request.model.free {
                Label(request.model.notice ?? CatalogFormat.freeNotice, systemImage: "exclamationmark.triangle")
                    .font(.system(size: 12)).foregroundStyle(Theme.warning)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if request.model.provider == "openrouter" {
                connectRow
                Text("O pega una clave que ya tengas:").font(.system(size: 12)).foregroundStyle(Theme.secondary)
            }
            SecureField(request.missing.variable, text: $secret).textFieldStyle(.roundedBorder)
                .accessibilityLabel("Clave de \(providerName)")
                .onSubmit { Task { await save() } }
            if let error = model.providersError {
                Text(error).font(.system(size: 12)).foregroundStyle(Theme.danger)
            }
            HStack {
                Spacer()
                Button("Cancelar") { model.keyRequest = nil }.keyboardShortcut(.cancelAction)
                Button("Guardar y usar") { Task { await save() } }
                    .keyboardShortcut(.defaultAction).disabled(busy || secret.isEmpty)
            }
        }
        .padding(20).frame(width: 440)
    }

    /// OpenRouter se conecta en el navegador (OAuth): sin copiar ni pegar la clave.
    @ViewBuilder
    private var connectRow: some View {
        if let waiting = model.connecting {
            HStack(spacing: 8) {
                ProgressView().controlSize(.small)
                Text("Autoriza AlfalfaCode en el navegador…").font(.system(size: 12.5))
                if let url = waiting.url { Link("Abrir otra vez", destination: url).font(.system(size: 12.5)) }
                Spacer()
                Button("Cancelar") { model.cancelConnect() }.controlSize(.small)
            }
        } else {
            Button {
                Task {
                    let request = self.request
                    if await model.connect("openrouter") != nil {
                        request.apply(request.model.name)
                        model.keyRequest = nil
                    }
                }
            } label: {
                Label("Conectar con OpenRouter sin pegar la clave", systemImage: "arrow.up.forward.app")
            }
            .buttonStyle(.borderedProminent).tint(Theme.alfalfa)
        }
    }

    private var providerName: String { request.model.providerName ?? model.providerName(request.model.provider) }

    private func save() async {
        guard !secret.isEmpty else { return }
        busy = true
        if await model.saveKey(for: request, secret: secret) { secret = "" }
        busy = false
    }
}
