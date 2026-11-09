import Foundation

/// Lo que ahorró la caché de prompts en una sesión (`cache` de `turno_terminado`, `fin` y
/// `presupuesto`). Anthropic lee y escribe la caché; OpenAI sólo la lee; con Ollama no hay.
public struct CacheSavings: Equatable, Sendable {
    /// Tokens que el proveedor sirvió de la caché.
    public var read: Int
    /// Tokens que escribió en ella (Anthropic los cobra a 1,25× la entrada).
    public var written: Int
    /// Lo que habrían costado esas peticiones sin caché y lo que costaron; nil sin precio.
    public var withoutCache: Double?
    public var withCache: Double?

    public init(read: Int, written: Int = 0, withoutCache: Double? = nil, withCache: Double? = nil) {
        self.read = read
        self.written = written
        self.withoutCache = withoutCache
        self.withCache = withCache
    }

    /// nil si no llega, no es un objeto o no hay tokens de caché.
    public init?(object value: Any?) {
        guard let object = value as? [String: Any] else { return nil }
        let read = max(0, (object["leidos"] as? NSNumber)?.intValue ?? 0)
        let written = max(0, (object["escritos"] as? NSNumber)?.intValue ?? 0)
        guard read > 0 || written > 0 else { return nil }
        self.init(read: read, written: written, withoutCache: Self.amount(object["sin_cache"]),
                  withCache: Self.amount(object["con_cache"]))
    }

    /// Lo ahorrado en USD (negativo si escribir aún cuesta más de lo que ahorra leer); nil sin precio.
    public var saved: Double? {
        guard let withoutCache, let withCache else { return nil }
        return withoutCache - withCache
    }

    /// «ahorró 0,42 $», «aún cuesta 0,02 $ más» o, sin precio, nil.
    public var savedText: String? {
        guard let saved else { return nil }
        return saved >= 0 ? "ahorró " + CatalogFormat.cost(saved) : "aún cuesta " + CatalogFormat.cost(-saved) + " más"
    }

    /// «Caché: ahorró 0,42 $» para el pie y el panel; sin precio, los tokens leídos.
    public var text: String {
        "Caché: " + (savedText ?? BudgetFormat.tokens(read) + " tokens leídos")
    }

    /// «0,58 $ sin caché · 0,16 $ con ella · 160.000 tokens leídos».
    public var detail: String {
        var parts: [String] = []
        if let withoutCache, let withCache {
            parts.append(CatalogFormat.cost(withoutCache) + " sin caché · " + CatalogFormat.cost(withCache) + " con ella")
        }
        parts.append(BudgetFormat.tokens(read) + " tokens leídos")
        if written > 0 { parts.append(BudgetFormat.tokens(written) + " escritos") }
        return parts.joined(separator: " · ")
    }

    fileprivate static func amount(_ value: Any?) -> Double? {
        guard let number = value as? NSNumber, CFGetTypeID(number) != CFBooleanGetTypeID() else { return nil }
        let double = number.doubleValue
        return double.isFinite && double >= 0 ? double : nil
    }
}

/// El evento `presupuesto`: los topes de la sesión y del mes (nil sin tope) y lo gastado.
public struct BudgetStatus: Equatable, Sendable {
    public struct Limit: Equatable, Sendable {
        public var cap: Double?
        public var spent: Double

        public init(cap: Double?, spent: Double) {
            self.cap = cap
            self.spent = spent
        }

        /// 0…1 del tope gastado; nil sin tope.
        public var fraction: Double? {
            guard let cap, cap > 0 else { return nil }
            return min(1, spent / cap)
        }

        /// «1,26 $ de 5,00 $» o, sin tope, «1,26 $».
        public var text: String {
            CatalogFormat.cost(spent) + (cap.map { " de " + CatalogFormat.cost($0) } ?? "")
        }
    }

    public enum Which: String, Sendable { case session = "sesion", month = "mes" }

    public var session: Limit
    public var month: Limit
    /// «2026-11»: el mes al que se refiere el gasto mensual.
    public var monthName: String?
    /// El aviso del 80 % que acaba de saltar, o el tope que paró el turno.
    public var warning: Which?
    public var exhausted: Which?

    public init(session: Limit, month: Limit, monthName: String? = nil, warning: Which? = nil, exhausted: Which? = nil) {
        self.session = session
        self.month = month
        self.monthName = monthName
        self.warning = warning
        self.exhausted = exhausted
    }

    init?(object: [String: Any]) {
        guard let session = Self.limit(object["sesion"]), let month = Self.limit(object["mes"]) else { return nil }
        self.init(session: session, month: month, monthName: (object["mes"] as? [String: Any])?["mes"] as? String,
                  warning: (object["aviso"] as? String).flatMap(Which.init(rawValue:)),
                  exhausted: (object["agotado"] as? String).flatMap(Which.init(rawValue:)))
    }

    /// Hay algún tope puesto.
    public var hasCap: Bool { session.cap != nil || month.cap != nil }

    private static func limit(_ value: Any?) -> Limit? {
        guard let object = value as? [String: Any] else { return nil }
        let cap = CacheSavings.amount(object["tope"]).flatMap { $0 > 0 ? $0 : nil }
        return Limit(cap: cap, spent: CacheSavings.amount(object["gastado"]) ?? 0)
    }
}

/// Topes en dólares de Ajustes ▸ General ▸ Presupuesto.
public enum BudgetFormat {
    /// Lo escrito en el campo de un tope.
    public enum Entry: Equatable, Sendable {
        /// Vacío o cero («0», «0,00», «0.0 $»): sin tope.
        case noCap
        case amount(Double)
        /// No es un importe: el campo lo dice y no cambia el tope.
        case invalid
    }

    public static func entry(_ text: String) -> Entry {
        let clean = text.replacingOccurrences(of: "$", with: "").replacingOccurrences(of: ",", with: ".")
            .trimmingCharacters(in: .whitespaces)
        if clean.isEmpty { return .noCap }
        guard let value = Double(clean), value.isFinite, value >= 0 else { return .invalid }
        return value == 0 ? .noCap : .amount(value)
    }

    /// Lo escrito en el campo («5», «12,50», «3 $»): un importe > 0, o nil (vacío, cero o no es un importe).
    public static func parse(_ text: String) -> Double? {
        if case let .amount(value) = entry(text) { return value }
        return nil
    }

    /// Para el campo: «5» o «12,5».
    public static func field(_ value: Double?) -> String {
        guard let value else { return "" }
        let text = value == value.rounded() ? String(Int(value)) : String(format: "%.2f", value)
        return text.replacingOccurrences(of: ".", with: ",")
    }

    /// Para `--presupuesto`: con punto decimal, como lo lee el motor en cualquier idioma, y sin
    /// redondear (el mismo valor que la orden `presupuesto` en vivo).
    public static func argument(_ value: Double) -> String {
        value == value.rounded() && abs(value) < 1e15 ? String(Int(value)) : String(value)
    }

    /// «160.000».
    public static func tokens(_ value: Int) -> String {
        let formatter = NumberFormatter()
        formatter.numberStyle = .decimal
        formatter.groupingSeparator = "."
        formatter.usesGroupingSeparator = true
        return formatter.string(from: NSNumber(value: value)) ?? String(value)
    }
}
