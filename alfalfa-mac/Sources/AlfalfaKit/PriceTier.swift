import Foundation

/// Cómo usa la sesión un modelo que cobra más por contexto largo (Luna pasados 272K, Haiku 5.5
/// pasados 100K): «ahorro» compacta antes del umbral; «completo» usa la ventana entera.
public enum ContextMode: String, CaseIterable, Sendable {
    case saving = "ahorro"
    case full = "completo"

    public var label: String {
        switch self {
        case .saving: return "Ahorro"
        case .full: return "Contexto completo"
        }
    }

    public var explanation: String {
        switch self {
        case .saving: return "Compacta antes de que el modelo pase a la tarifa cara."
        case .full: return "Usa la ventana entera; pasado el umbral cada petición cuesta más."
        }
    }
}

/// El tramo de contexto largo de un modelo: por encima de `from` tokens de prompt, toda la
/// petición cuesta `input` veces la entrada y `output` veces la salida.
public struct PriceTier: Equatable, Sendable {
    public var from: Int
    public var input: Double
    public var output: Double
    /// Modo de la sesión cuando llega en el evento `contexto`.
    public var mode: ContextMode?

    public init(from: Int, input: Double, output: Double, mode: ContextMode? = nil) {
        self.from = from
        self.input = input
        self.output = output
        self.mode = mode
    }

    /// «5×» o «2× la entrada y 1,5× la salida».
    public var multiplier: String {
        input == output ? Self.times(input) : Self.times(input) + " la entrada y " + Self.times(output) + " la salida"
    }

    static func times(_ value: Double) -> String {
        let text = value == value.rounded() ? String(Int(value)) : String(format: "%.2f", value)
            .replacingOccurrences(of: #"0+$"#, with: "", options: .regularExpression)
            .replacingOccurrences(of: ".", with: ",")
        return text + "×"
    }

    /// `{desde, entrada, salida, modo}`; nil si falta el umbral.
    static func parse(_ value: Any?) -> PriceTier? {
        guard let object = value as? [String: Any], let from = object["desde"] as? Int, from > 0 else { return nil }
        let input = (object["entrada"] as? NSNumber)?.doubleValue ?? 1
        let output = (object["salida"] as? NSNumber)?.doubleValue ?? 1
        return PriceTier(from: from, input: input, output: output,
                         mode: (object["modo"] as? String).flatMap(ContextMode.init(rawValue:)))
    }
}

/// El aviso de tarifa que manda el motor (`tarifa` de un `evento`): al 80 % del umbral en
/// Ahorro, con tres salidas, o al pasarlo en Contexto completo.
public struct PriceWarning: Equatable, Sendable {
    public var tier: PriceTier
    public var mode: ContextMode
    /// Nombre visible del modelo («Claude Haiku 5.5»).
    public var model: String
    /// Tokens en uso cuando avisó, y cuántos faltan para la compactación (Ahorro).
    public var estimate: Int
    public var left: Int?
    /// El texto entero del motor, para la terminal y la exportación.
    public var text: String

    public init(tier: PriceTier, mode: ContextMode, model: String, estimate: Int, left: Int?, text: String) {
        self.tier = tier
        self.mode = mode
        self.model = model
        self.estimate = estimate
        self.left = left
        self.text = text
    }

    /// Lo que llega en el campo `tarifa` del evento.
    static func parse(_ value: Any?, text: String) -> PriceWarning? {
        guard let tier = PriceTier.parse(value), let object = value as? [String: Any] else { return nil }
        let mode = (object["modo"] as? String).flatMap(ContextMode.init(rawValue:)) ?? .saving
        return PriceWarning(tier: PriceTier(from: tier.from, input: tier.input, output: tier.output),
                            mode: mode, model: object["modelo"] as? String ?? "El modelo",
                            estimate: object["estimado"] as? Int ?? 0, left: object["faltan"] as? Int, text: text)
    }

    /// «A partir de 100K tokens, Claude Haiku 5.5 cobra 5×.»
    public var headline: String {
        "A partir de \(CatalogFormat.window(tier.from)) tokens, \(model) cobra \(tier.multiplier)."
    }

    /// «Compactaré en unos 10K.» (sólo en Ahorro).
    public var detail: String? {
        guard mode == .saving else { return nil }
        guard let left, left > 0 else { return "Compactaré antes de la siguiente respuesta." }
        return "Compactaré en unos \(CatalogFormat.window(left))."
    }
}
