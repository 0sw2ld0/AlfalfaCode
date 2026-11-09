import AlfalfaKit
import SwiftUI

/// Las opciones del selector de modelo, agrupadas por proveedor cuando hay más de uno.
/// Los modelos del selector: los de cada proveedor y, aparte, «Gratis ahora» con el aviso de datos.
/// Cada modelo lleva su ventana y su precio del catálogo de models.dev («256K · 0,25/2 $ por M»).
struct ModelMenuItems: View {
    @ObservedObject var app: AppModel
    let groups: [(provider: String, names: [String])]
    let selected: String
    let choose: (String) -> Void

    var body: some View {
        if groups.count > 1 {
            ForEach(groups, id: \.provider) { group in
                Section(app.providerName(group.provider)) { items(group.names) }
            }
        } else {
            items(groups.first?.names ?? [])
        }
        let free = app.freeGroups
        if !free.isEmpty {
            Section("Gratis ahora · " + CatalogFormat.freeNotice.lowercased()) {
                ForEach(free, id: \.provider) { group in
                    Menu(group.title) { items(group.names) }
                }
            }
        }
    }

    @ViewBuilder private func items(_ names: [String]) -> some View {
        ForEach(names, id: \.self) { name in
            Button { choose(name) } label: {
                if name == ModelProvider.listed(selected, in: app.models) { Label(title(name), systemImage: "checkmark") } else { Text(title(name)) }
            }
        }
    }

    private func title(_ name: String) -> String {
        let label = app.label(for: name)
        guard let detail = app.info(for: name).flatMap(CatalogFormat.detail) else { return label }
        return label + "  ·  " + detail
    }
}

/// Ajustes ▸ Proveedores: Ollama con su URL y su clave, OpenAI y los compatibles con OpenAI (OpenRouter,
/// OpenCode Zen…) con la del usuario, el servicio Alfalfa y el proveedor por defecto.
/// Las claves van al Llavero a través de `alfalfa`; la app no las guarda ni las vuelve a enseñar.
struct ProvidersView: View {
    @EnvironmentObject private var model: AppModel
    @State private var ollamaURL = ""
    @State private var ollamaKey = ""
    @State private var openAIKey = ""
    @State private var alfalfaCode = ""
    /// Claves que se escriben para los compatibles con OpenAI (OpenRouter, OpenCode Zen…), por id.
    @State private var compatibleKeys: [String: String] = [:]
    @State private var busy = false
    @State private var notice: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            SettingsTitle(text: "Proveedores")
            defaultPicker
            section("Ollama", status: status("ollama"),
                    detail: "Local, u Ollama Cloud con tu clave. Sin URL se usa la de este Mac.") {
                TextField("http://127.0.0.1:11434", text: $ollamaURL).textFieldStyle(.roundedBorder)
                    .accessibilityLabel("URL de Ollama")
                SecureField(status("ollama")?.hasKey == true ? "Clave guardada · escribe otra para cambiarla"
                                                             : "Clave de Ollama Cloud (opcional)", text: $ollamaKey)
                    .textFieldStyle(.roundedBorder).accessibilityLabel("Clave de Ollama")
                HStack {
                    Button("Guardar") { Task { await saveOllama() } }.disabled(busy)
                    if status("ollama")?.hasKey == true {
                        Button("Borrar clave") { Task { await run { await model.deleteKey(for: "ollama") } } }
                            .disabled(busy)
                    }
                }
            }
            section("OpenAI", status: status("openai"),
                    detail: "Con tu propia clave de la API; el uso se cobra en tu cuenta de OpenAI.") {
                SecureField(status("openai")?.hasKey == true ? "Clave guardada · escribe otra para cambiarla"
                                                             : "sk-…", text: $openAIKey)
                    .textFieldStyle(.roundedBorder).accessibilityLabel("Clave de OpenAI")
                HStack {
                    Button("Guardar") { Task { await saveOpenAI() } }.disabled(busy || openAIKey.isEmpty)
                    if status("openai")?.hasKey == true {
                        Button("Borrar clave") { Task { await run { await model.deleteKey(for: "openai") } } }
                            .disabled(busy)
                    }
                }
            }
            ForEach(model.providers.filter(\.compatible)) { provider in
                compatibleSection(provider)
            }
            section("Alfalfa", status: status("alfalfa"),
                    detail: "Con un código de cortesía, CUY-…@servicio, que trae la dirección del servicio: "
                        + "cuota mensual de GPT-6 Luna, Claude Haiku 5.5, GLM 5.3 Flash y DeepSeek v4.1 Flash. "
                        + "El código queda atado a este Mac.") {
                // La dirección del servicio va dentro del código: CUY-XXXX-XXXX-XXXX@servicio.
                SecureField(status("alfalfa")?.configured == true ? "Código activado · pega otro para cambiarlo"
                                                                   : "Código de cortesía (CUY-…@servicio)",
                            text: $alfalfaCode)
                    .textFieldStyle(.roundedBorder).accessibilityLabel("Código de cortesía")
                if let host = status("alfalfa")?.url.flatMap({ URL(string: $0)?.host() }) {
                    Text("Servicio: \(host)").font(.system(size: 11.5)).foregroundStyle(Theme.muted)
                }
                HStack {
                    Button("Activar en este Mac") { Task { await activateAlfalfa() } }
                        .disabled(busy || alfalfaCode.isEmpty)
                    if status("alfalfa")?.hasKey == true {
                        Button("Quitar código") { Task { await run { await model.deleteKey(for: "alfalfa") } } }
                            .disabled(busy)
                    }
                }
                if !model.alfalfaQuotas.isEmpty { QuotaBars(quotas: model.alfalfaQuotas) }
                HStack(spacing: 14) {
                    Link("Qué es Alfalfa Cloud", destination: Links.cloud)
                    Link("Qué datos guarda", destination: Links.privacy)
                }
                .font(.system(size: 11.5))
            }
            if let text = model.providersError ?? notice {
                Text(text).font(.system(size: 12))
                    .foregroundStyle(model.providersError == nil ? Theme.secondary : Theme.danger)
            }
        }
        .frame(width: 480, alignment: .leading)
        .task {
            await model.reloadProviders()
            ollamaURL = status("ollama")?.url ?? ""
            await model.loadAlfalfaQuota()
        }
    }

    private func status(_ id: String) -> ProviderStatus? { model.providers.first { $0.id == id } }

    /// Un compatible con OpenAI del catálogo de models.dev: con tu clave, a su precio.
    private func compatibleSection(_ provider: ProviderStatus) -> some View {
        let key = Binding(get: { compatibleKeys[provider.id] ?? "" }, set: { compatibleKeys[provider.id] = $0 })
        let variable = provider.variable.map { " (\($0))" } ?? ""
        return section(provider.name, status: provider,
                       detail: "Compatible con OpenAI, con tu clave\(variable); el uso se cobra en tu cuenta. "
                           + "Sus modelos gratis salen en «Gratis ahora»: " + CatalogFormat.freeNotice.lowercased()
                           + ".") {
            if let notice = provider.notice {
                Label(notice, systemImage: "info.circle").font(.system(size: 12)).foregroundStyle(Theme.warning)
                    .fixedSize(horizontal: false, vertical: true)
            }
            SecureField(provider.hasKey ? "Clave guardada · escribe otra para cambiarla"
                                        : (provider.variable ?? "Clave"), text: key)
                .textFieldStyle(.roundedBorder).accessibilityLabel("Clave de \(provider.name)")
            HStack {
                Button("Guardar") {
                    Task {
                        await run {
                            if await model.saveKey(key.wrappedValue, for: provider.id) {
                                compatibleKeys[provider.id] = ""
                                notice = "Clave guardada en el Llavero."
                            }
                        }
                    }
                }
                .disabled(busy || key.wrappedValue.isEmpty)
                if provider.hasKey {
                    Button("Borrar clave") { Task { await run { await model.deleteKey(for: provider.id) } } }
                        .disabled(busy)
                }
                if let page = provider.keyPage, let url = URL(string: page) {
                    Link("Conseguir la clave", destination: url).font(.system(size: 12))
                }
            }
        }
    }

    private var defaultPicker: some View {
        HStack {
            Text("Por defecto").foregroundStyle(Theme.secondary)
            Picker("Por defecto", selection: Binding(get: { model.defaultProvider }, set: { value in
                Task { await run { await model.setDefaultProvider(value) } }
            })) {
                ForEach(model.providers) { provider in
                    Text(provider.name).tag(provider.id)
                }
            }
            .labelsHidden().fixedSize()
        }
    }

    @ViewBuilder
    private func section<Content: View>(_ title: String, status: ProviderStatus?, detail: String,
                                        @ViewBuilder content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 6) {
                Text(title).font(.system(size: 14, weight: .semibold))
                if status?.configured == true {
                    Image(systemName: "checkmark.circle.fill").foregroundStyle(Theme.alfalfa)
                        .accessibilityLabel("Configurado")
                }
            }
            Text(detail).font(.system(size: 12)).foregroundStyle(Theme.secondary)
            content()
        }
    }

    private func run(_ action: () async -> Void) async {
        busy = true
        notice = nil
        await action()
        busy = false
    }

    private func saveOllama() async {
        await run {
            let current = status("ollama")?.url ?? ""
            if ollamaURL != current, await model.setURL(ollamaURL, for: "ollama") { notice = "URL guardada." }
            if !ollamaKey.isEmpty, await model.saveKey(ollamaKey, for: "ollama") {
                ollamaKey = ""
                notice = "Clave guardada en el Llavero."
            }
        }
    }

    private func activateAlfalfa() async {
        await run {
            // Sin URL aparte: `alfalfa --activar-codigo` la lee del propio código.
            if await model.activate(code: alfalfaCode, url: "") {
                alfalfaCode = ""
                notice = "Código activado en este Mac."
            }
        }
    }

    private func saveOpenAI() async {
        await run {
            if await model.saveKey(openAIKey, for: "openai") {
                openAIKey = ""
                notice = "Clave guardada en el Llavero."
            }
        }
    }
}
