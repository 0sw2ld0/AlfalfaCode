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
    public var brainstorm: BrainstormConfiguration? = nil

    /// El título propio si lo hay; si no, el encargo.
    public var displayTitle: String? { title ?? task }

    /// Estados de una sesión con un turno en marcha.
    public static let runningStates: Set<String> = ["running", "waiting", "starting"]

    /// La sesión trabaja ahora en otro proceso (un encargo lanzado desde la terminal, otra app o un cliente PACAI):
    /// `--sesiones` sólo da estos estados si alguien tiene su cerrojo; si su proceso murió, la da por `interrupted`.
    /// La app no debe reanudarla ni escribirle mientras tanto: serían dos procesos sobre la misma sesión.
    public var isRunningElsewhere: Bool { Self.runningStates.contains(state) }
}
