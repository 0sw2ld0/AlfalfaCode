import Foundation

/// Oculta las claves de los proveedores si estuvieran en el entorno. La app nunca
/// las lee para otra cosa, ni las muestra, ni las guarda.
public enum Redactor {
    static let variables = ["OLLAMA_API_KEY", "OPENAI_API_KEY"]

    public static func clean(_ text: String, environment: [String: String] = ProcessInfo.processInfo.environment) -> String {
        let secrets = variables.compactMap { environment[$0] }.filter { !$0.isEmpty }.sorted { $0.count > $1.count }
        return secrets.reduce(text) { $0.replacingOccurrences(of: $1, with: "[REDACTADO]") }
    }
}
