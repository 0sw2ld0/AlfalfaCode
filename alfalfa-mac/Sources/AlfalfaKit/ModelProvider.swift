import Foundation

/// De qué proveedor es un modelo: Ollama, OpenAI, Anthropic o Alfalfa.
public enum ModelProvider {

    // MARK: - Constantes

    public static let names = ["ollama": "Ollama", "openai": "OpenAI", "anthropic": "Anthropic", "alfalfa": "Alfalfa"]

    // MARK: - Métodos públicos

    /// Por el prefijo del modelo, como en el motor: sin prefijo de OpenAI, Anthropic o Alfalfa es Ollama.
    public static func id(of model: String) -> String {
        let head = model.split(separator: "/", maxSplits: 1).first.map(String.init) ?? ""
        return model.contains("/") && ["openai", "anthropic", "alfalfa"].contains(head) ? head : "ollama"
    }

    /// El de la lista de modelos de `alfalfa`, que manda; si el modelo no está, el del prefijo.
    public static func id(of model: String, in models: [ModelInfo]) -> String {
        let name = listed(model, in: models)
        return models.first { $0.name == name }?.provider ?? id(of: name)
    }

    /// El nombre con que la lista trae un modelo guardado: el mismo o, para uno de Ollama cuyo autor se
    /// llama como un proveedor del catálogo (`deepseek/deepseek-r1:7b`), el que lleva `ollama/` delante.
    public static func listed(_ model: String, in models: [ModelInfo]) -> String {
        if models.contains(where: { $0.name == model }) { return model }
        let explicit = "ollama/" + model
        return models.contains { $0.name == explicit } ? explicit : model
    }

    /// El nombre corto que se enseña: sin el `ollama/` explícito ni el sufijo `:cloud`.
    public static func shortName(_ model: String) -> String {
        let bare = model.hasPrefix("ollama/") ? String(model.dropFirst(7)) : model
        return bare.hasSuffix(":cloud") ? String(bare.dropLast(6)) : bare
    }

    /// El nombre visible: el que da `alfalfa --proveedores`, el conocido o el propio id.
    public static func name(of id: String, listed: [ProviderStatus] = []) -> String {
        listed.first { $0.id == id }?.name ?? names[id] ?? id
    }
}
