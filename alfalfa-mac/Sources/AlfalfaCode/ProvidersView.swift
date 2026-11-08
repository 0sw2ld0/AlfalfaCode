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

/// Ollama con su URL y su clave, OpenAI con la del usuario y el proveedor por defecto.
/// Las claves van al Llavero a través de `alfalfa`; la app no las guarda ni las vuelve a enseñar.
struct ProvidersView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss
    @State private var ollamaURL = ""
    @State private var ollamaKey = ""
    @State private var openAIKey = ""
    @State private var busy = false
    @State private var notice: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            HStack {
                Text("Proveedores").font(.system(size: 17, weight: .semibold))
                Spacer()
                Button("Cerrar") { dismiss() }.keyboardShortcut(.cancelAction)
            }
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
            if let text = model.providersError ?? notice {
                Text(text).font(.system(size: 12))
                    .foregroundStyle(model.providersError == nil ? Theme.secondary : Theme.danger)
            }
        }
        .padding(24)
        .frame(width: 480)
        .task {
            await model.reloadProviders()
            ollamaURL = status("ollama")?.url ?? ""
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

    private func saveOpenAI() async {
        await run {
            if await model.saveKey(openAIKey, for: "openai") {
                openAIKey = ""
                notice = "Clave guardada en el Llavero."
            }
        }
    }
}
