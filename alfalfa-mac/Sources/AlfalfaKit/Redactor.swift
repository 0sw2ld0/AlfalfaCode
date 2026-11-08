import Foundation

/// Oculta la clave de Ollama si estuviera presente en el entorno. La app nunca
/// la lee para otra cosa, ni la muestra, ni la guarda.
public enum Redactor {
    public static func clean(_ text: String, environment: [String: String] = ProcessInfo.processInfo.environment) -> String {
        guard let secret = environment["OLLAMA_API_KEY"], !secret.isEmpty else { return text }
        return text.replacingOccurrences(of: secret, with: "[REDACTADO]")
    }
}
