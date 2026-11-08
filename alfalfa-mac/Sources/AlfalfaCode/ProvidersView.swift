import AlfalfaKit
import SwiftUI

/// Las opciones del selector de modelo, agrupadas por proveedor cuando hay más de uno.
struct ModelMenuItems: View {
    let groups: [(provider: String, names: [String])]
    let selected: String
    let label: (String) -> String
    let providerName: (String) -> String
    let choose: (String) -> Void

    var body: some View {
        if groups.count > 1 {
            ForEach(groups, id: \.provider) { group in
                Section(providerName(group.provider)) { items(group.names) }
            }
        } else {
            items(groups.first?.names ?? [])
        }
    }

    @ViewBuilder private func items(_ names: [String]) -> some View {
        ForEach(names, id: \.self) { name in
            Button { choose(name) } label: {
                if name == selected { Label(label(name), systemImage: "checkmark") } else { Text(label(name)) }
            }
        }
    }
}

/// Ajustes ▸ Proveedores: Ollama con su URL y su clave, OpenAI con la del usuario y el proveedor por defecto.
/// Las claves van al Llavero a través de `alfalfa`; la app no las guarda ni las vuelve a enseñar.
struct ProvidersView: View {
    @EnvironmentObject private var model: AppModel
    @State private var ollamaURL = ""
    @State private var ollamaKey = ""
    @State private var openAIKey = ""
    @State private var alfalfaCode = ""
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
            section("Alfalfa", status: status("alfalfa"),
                    detail: "Con un código de cortesía, CUY-…@servicio, que trae la dirección del servicio: "
                        + "cuota mensual de GPT-6 Luna, GLM 5.3 Flash y DeepSeek v4.1 Flash. "
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

/// El % usado de la cuota del mes, una barra por parte (GPT-6 Luna, los Flash).
struct QuotaBars: View {
    let quotas: [Quota]

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            ForEach(quotas) { quota in
                VStack(alignment: .leading, spacing: 3) {
                    HStack {
                        Text(quota.label).font(.system(size: 11.5))
                        Spacer()
                        Text(String(format: "%.0f %%", quota.usedPercent)).font(.system(size: 11.5, design: .monospaced))
                    }
                    ProgressView(value: min(100, max(0, quota.usedPercent)), total: 100)
                        .tint(quota.usedPercent >= 90 ? Theme.danger : quota.usedPercent >= 70 ? Theme.warning : Theme.alfalfa)
                }
                .accessibilityElement(children: .combine)
                .accessibilityLabel("\(quota.label): \(Int(quota.usedPercent)) por ciento de la cuota del mes")
            }
            if let renews = quotas.first?.renews {
                Text("Se renueva el \(renews)").font(.system(size: 11)).foregroundStyle(Theme.muted)
            }
        }
    }
}

/// La cuota en una línea, encima del cuadro de texto de una sesión con Alfalfa.
struct QuotaLine: View {
    let quotas: [Quota]

    var body: some View {
        HStack(spacing: 14) {
            ForEach(quotas) { quota in
                HStack(spacing: 6) {
                    Text(quota.label).font(.system(size: 11)).foregroundStyle(Theme.secondary)
                    ProgressView(value: min(100, max(0, quota.usedPercent)), total: 100).frame(width: 60)
                        .tint(quota.usedPercent >= 90 ? Theme.danger : quota.usedPercent >= 70 ? Theme.warning : Theme.alfalfa)
                    Text(String(format: "%.0f %%", quota.usedPercent)).font(.system(size: 11, design: .monospaced))
                        .foregroundStyle(Theme.secondary)
                }
                .accessibilityElement(children: .combine)
                .accessibilityLabel("\(quota.label): \(Int(quota.usedPercent)) por ciento de la cuota del mes")
            }
        }
        .help(quotas.first?.renews.map { "Cuota del mes del servicio Alfalfa · se renueva el \($0)" } ?? "Cuota del mes")
    }
}
