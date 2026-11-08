import Foundation

/// De qué proveedor es un modelo: Ollama, OpenAI o Alfalfa.
public enum ModelProvider {

    // MARK: - Constantes

    public static let names = ["ollama": "Ollama", "openai": "OpenAI", "alfalfa": "Alfalfa"]

    // MARK: - Métodos públicos

    /// Por el prefijo del modelo, como en el motor: sin prefijo de OpenAI o Alfalfa es Ollama.
    public static func id(of model: String) -> String {
        let head = model.split(separator: "/", maxSplits: 1).first.map(String.init) ?? ""
        return model.contains("/") && ["openai", "alfalfa"].contains(head) ? head : "ollama"
    }

    /// El de la lista de modelos de `alfalfa`, que manda; si el modelo no está, el del prefijo.
    public static func id(of model: String, in models: [ModelInfo]) -> String {
        models.first { $0.name == model }?.provider ?? id(of: model)
    }

    /// El nombre visible: el que da `alfalfa --proveedores`, el conocido o el propio id.
    public static func name(of id: String, listed: [ProviderStatus] = []) -> String {
        listed.first { $0.id == id }?.name ?? names[id] ?? id
    }
}
