import Foundation

/// Cuota mensual de un proveedor con límite (el servicio Alfalfa).
public struct Quota: Equatable, Identifiable, Sendable {
    /// Qué parte de la cuota: `openai` (GPT-6 Luna) u `ollama` (los modelos de Ollama).
    public var provider: String
    public var label: String
    /// El % usado del mes y cuándo vuelve a cero (`AAAA-MM-DD`).
    public var usedPercent: Double
    public var renews: String?
    /// El tope de hoy, si el código tiene uno: vuelve a cero a medianoche UTC.
    public var daily: QuotaWindow?
    public var id: String { provider }

    /// Lo que más aprieta: el de hoy o el del mes.
    public var tightestPercent: Double { max(usedPercent, daily?.usedPercent ?? 0) }

    public init(provider: String, label: String? = nil, usedPercent: Double, renews: String? = nil,
                daily: QuotaWindow? = nil) {
        self.provider = provider
        self.label = label ?? provider
        self.usedPercent = usedPercent
        self.renews = renews
        self.daily = daily
    }
}

/// Una ventana de cuota: % usado y cuándo vuelve a cero (fecha ISO, con hora si es la de hoy).
public struct QuotaWindow: Equatable, Sendable {
    public var usedPercent: Double
    public var renews: String?

    public init(usedPercent: Double, renews: String? = nil) {
        self.usedPercent = usedPercent
        self.renews = renews
    }
}

extension Quota {
    /// Una fila de `cuotas` del servicio: `{proveedor, nombre, usado_pct, renueva, dia?}`.
    init?(row: [String: Any]) {
        guard let provider = Self.text(row["proveedor"]), let used = (row["usado_pct"] as? NSNumber)?.doubleValue
        else { return nil }
        self.init(provider: provider, label: Self.text(row["nombre"]), usedPercent: used,
                  renews: Self.text(row["renueva"]), daily: QuotaWindow(row: row["dia"]))
    }

    static func text(_ value: Any?) -> String? {
        guard let string = value as? String, !string.trimmingCharacters(in: .whitespaces).isEmpty else { return nil }
        return string
    }
}

extension QuotaWindow {
    /// `{"usado_pct": 90, "renueva": "…"}` del servicio; nil si falta o no es una ventana.
    init?(row: Any?) {
        guard let row = row as? [String: Any], let used = (row["usado_pct"] as? NSNumber)?.doubleValue
        else { return nil }
        self.init(usedPercent: used, renews: Quota.text(row["renueva"]))
    }
}
