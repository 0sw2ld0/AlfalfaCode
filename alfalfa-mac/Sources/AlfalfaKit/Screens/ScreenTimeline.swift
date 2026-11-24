import Foundation

/// Cómo entran en la conversación las pantallas de un mensaje del agente (perfil `ui`).
///
/// Cada pantalla vive en un elemento `.screen` de la línea de tiempo. Si un mensaje posterior la cambia, se
/// actualiza donde está; con `placement` `latest` baja al mensaje actual y donde estaba queda una copia apagada
/// (`live: false`) con lo que se veía antes.
enum ScreenTimeline {

    /// Aplica los bloques de un mensaje. `live` dice, por pantalla, qué elemento es el que se puede usar;
    /// `counter` da los ids nuevos, como `Timeline.nextID()`.
    static func apply(_ updates: [ScreenUpdate], to items: inout [TimelineItem], live: inout [String: Int],
                      counter: inout Int) {
        var touched = Set<String>()
        for update in updates {
            let index = live[update.view].flatMap { id in items.firstIndex { $0.id == id } }
            let current = index.flatMap { screen(at: $0, in: items) } ?? Screen(name: update.view)
            guard let next = current.applying(update) else { continue }      // revisión vieja o repetida
            // Sólo baja una vez por mensaje: el `patch` y la foto del mismo mensaje van al mismo sitio.
            let moves = index == nil || (next.placement == .latest && !touched.contains(update.view))
            touched.insert(update.view)
            if let index, moves {
                items[index] = .screen(id: items[index].id, current, live: false)
            }
            if moves || index == nil {
                counter += 1
                items.append(.screen(id: counter, next, live: true))
                live[update.view] = counter
            } else if let index {
                items[index] = .screen(id: items[index].id, next, live: true)
            }
        }
    }

    private static func screen(at index: Int, in items: [TimelineItem]) -> Screen? {
        if case let .screen(_, screen, _) = items[index] { return screen }
        return nil
    }
}
