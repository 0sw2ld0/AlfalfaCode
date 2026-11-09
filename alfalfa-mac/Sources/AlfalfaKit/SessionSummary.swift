import Foundation

/// Una fila de `alfalfa --sesiones --json`.
public struct SessionSummary: Equatable, Identifiable, Sendable {
    public var id: String
    public var task: String?
    public var cwd: String?
    public var state: String
    public var model: String?
    public var effort: String?
    public var date: Double
    public var tokens: Int
    /// Marcas de la app (`--marcar`): título propio, fijada y archivada.
    public var title: String?
    public var pinned: Bool = false
    public var archived: Bool = false
    public var mode: String?
    /// La sesión que propuso esta (`padre`): sale debajo de ella en la barra lateral.
    public var parent: String?
    /// Coste estimado en USD con los precios de models.dev; nil sin precio conocido.
    public var cost: Double?

    /// El título propio si lo hay; si no, el encargo.
    public var displayTitle: String? { title ?? task }
}
