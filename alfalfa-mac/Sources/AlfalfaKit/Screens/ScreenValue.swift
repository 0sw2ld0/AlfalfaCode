import Foundation

/// Un valor JSON de una pantalla del perfil `ui` de PACAI (props, estado, filas de una tabla). Es `Equatable` y
/// `Sendable`, a diferencia de lo que devuelve `JSONSerialization`.
public enum ScreenValue: Equatable, Sendable {
    case null
    case bool(Bool)
    case number(Double)
    case string(String)
    case array([ScreenValue])
    case object([String: ScreenValue])

    // MARK: - Propiedades

    /// El valor como lo escribe `JSONSerialization`.
    public var json: Any {
        switch self {
        case .null: return NSNull()
        case let .bool(value): return value
        case let .number(value): return value.rounded() == value && abs(value) < 1e15 ? Int(value) as Any : value
        case let .string(value): return value
        case let .array(values): return values.map(\.json)
        case let .object(values): return values.mapValues(\.json)
        }
    }

    /// El valor para enseñarlo en una celda o una etiqueta: los números sin «.0», el resto como texto plano.
    public var text: String {
        switch self {
        case .null: return ""
        case let .bool(value): return value ? "Sí" : "No"
        case let .number(value):
            return value.rounded() == value && abs(value) < 1e15 ? String(Int(value)) : String(value)
        case let .string(value): return value
        case let .array(values): return values.map(\.text).joined(separator: ", ")
        case .object: return ""
        }
    }

    public var string: String? {
        if case let .string(value) = self { return value }
        return nil
    }

    public var number: Double? {
        if case let .number(value) = self { return value }
        return nil
    }

    public var bool: Bool? {
        if case let .bool(value) = self { return value }
        return nil
    }

    public var array: [ScreenValue]? {
        if case let .array(values) = self { return values }
        return nil
    }

    public var object: [String: ScreenValue]? {
        if case let .object(values) = self { return values }
        return nil
    }

    // MARK: - Inicializadores

    /// Un valor de `JSONSerialization`. Distingue los booleanos de los números (los dos llegan como NSNumber).
    public init(json value: Any?) {
        switch value {
        case let number as NSNumber where CFGetTypeID(number) == CFBooleanGetTypeID():
            self = .bool(number.boolValue)
        case let number as NSNumber: self = .number(number.doubleValue)
        case let text as String: self = .string(text)
        case let values as [Any]: self = .array(values.map { ScreenValue(json: $0) })
        case let values as [String: Any]: self = .object(values.mapValues { ScreenValue(json: $0) })
        default: self = .null
        }
    }

    // MARK: - Métodos públicos

    /// Un objeto JSON como diccionario de valores; vacío si no lo es.
    public static func dictionary(_ value: Any?) -> [String: ScreenValue] {
        (value as? [String: Any] ?? [:]).mapValues { ScreenValue(json: $0) }
    }
}
