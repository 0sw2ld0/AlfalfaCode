import Foundation

/// Precio de un modelo en USD por millón de tokens, del catálogo de models.dev.
public struct ModelPrice: Equatable, Sendable {
    public var input: Double
    public var output: Double
    public var isFree: Bool { input == 0 && output == 0 }

    public init(input: Double, output: Double) {
        self.input = input
        self.output = output
    }
}

/// La clave que le falta a un proveedor compatible con OpenAI y dónde se consigue.
public struct MissingKey: Equatable, Sendable {
    /// `OPENROUTER_API_KEY`: el nombre con que el proveedor la llama.
    public var variable: String
    /// Página donde se consigue (la `doc` del catálogo o la conocida).
    public var url: String?
    /// La explicación completa que da `alfalfa`.
    public var text: String

    public init(variable: String, url: String? = nil, text: String) {
        self.variable = variable
        self.url = url
        self.text = text
    }
}

/// Los modelos de un proveedor en el selector: los recomendados arriba y el resto en «Más modelos».
public enum ModelMenuSplit {
    /// Los que la lista marca `recomendado: false` van a «Más modelos»; los marcados y los que no dicen
    /// nada (Ollama, Alfalfa, los compatibles) se quedan arriba. Conserva el orden de la lista.
    public static func split(_ names: [String], in models: [ModelInfo]) -> (recommended: [String], more: [String]) {
        var recommended: [String] = []
        var more: [String] = []
        for name in names {
            let listed = ModelProvider.listed(name, in: models)
            if models.first(where: { $0.name == listed })?.recommended == false {
                more.append(name)
            } else {
                recommended.append(name)
            }
        }
        return (recommended, more)
    }
}

/// Cómo se enseñan ventanas, precios y costes en el selector y en el panel.
public enum CatalogFormat {
    /// Aviso que acompaña a cada modelo gratis.
    public static let freeNotice = "Gratis: puede usar tus datos para entrenar"

    /// «256K», «1M», «1,05M»: ventana de contexto.
    public static func window(_ tokens: Int) -> String {
        if tokens >= 1_000_000 {
            let value = Double(tokens) / 1_000_000
            return value == value.rounded() ? "\(Int(value))M" : decimal(value, digits: 2) + "M"
        }
        if tokens >= 1000 { return "\(Int((Double(tokens) / 1000).rounded()))K" }
        return "\(tokens)"
    }

    /// «gratis» o «0,25/2 $ por M» (entrada/salida por millón de tokens).
    public static func price(_ price: ModelPrice) -> String {
        if price.isFree { return "gratis" }
        return amount(price.input) + "/" + amount(price.output) + " $ por M"
    }

    /// Coste estimado: «0 $», «< 0,01 $», «0,37 $», «12,40 $».
    public static func cost(_ usd: Double) -> String {
        if usd <= 0 { return "0 $" }
        if usd < 0.01 { return "< 0,01 $" }
        return decimal(usd, digits: 2, keepZeros: true) + " $"
    }

    /// Lo que el selector añade al nombre: «256K · gratis», «1M · barato hasta 100K · 0,1/0,5 $ por M».
    public static func detail(_ model: ModelInfo) -> String? {
        var parts: [String] = []
        if let context = model.context { parts.append(window(context)) }
        // «1M · barato hasta 100K»: pasado eso, toda la petición cuesta más.
        if let cheap = model.cheapUntil, cheap < (model.context ?? .max) { parts.append("barato hasta " + window(cheap)) }
        if let price = model.price { parts.append(Self.price(price)) }
        if model.missingKey != nil { parts.append("sin clave") }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
    }

    private static func amount(_ value: Double) -> String {
        value >= 100 ? String(Int(value.rounded())) : decimal(value, digits: value < 1 ? 3 : 2)
    }

    private static func decimal(_ value: Double, digits: Int, keepZeros: Bool = false) -> String {
        var text = String(format: "%.\(digits)f", value)
        if !keepZeros, text.contains(".") {
            while text.hasSuffix("0") { text.removeLast() }
            if text.hasSuffix(".") { text.removeLast() }
        }
        return text.replacingOccurrences(of: ".", with: ",")
    }
}
