import AlfalfaKit
import AppKit
import ObjectiveC

/// Un texto largo que va a entrar en el cuadro de escribir se queda fuera y se adjunta como archivo.
///
/// El `TextField` de SwiftUI tarda en meter un registro largo: con 180 KB, 4 s el editor del campo y otros
/// 12 s SwiftUI antes de avisar del cambio (casi un minuto en total). Mirar sólo ⌘V no bastaba: Edición →
/// Pegar, el clic derecho, arrastrar texto o pegar con formato lo metían igual. Todos esos caminos pasan por
/// `NSTextView.shouldChangeText(inRanges:replacementStrings:)` del editor del campo: aquí se para el texto
/// antes de que entre (en 0,06 s).
enum PasteGuard {

    // MARK: - Métodos

    /// Una vez al arrancar. `attach` devuelve true si el texto se adjuntó; entonces no entra en el cuadro.
    @MainActor
    static func install(_ attach: @escaping @MainActor (String) -> Bool) {
        guard handler == nil else { return }
        handler = attach
        let original = #selector(NSTextView.shouldChangeText(inRanges:replacementStrings:))
        let guarded = #selector(NSTextView.alfalfa_shouldChangeText(inRanges:replacementStrings:))
        guard let a = class_getInstanceMethod(NSTextView.self, original),
              let b = class_getInstanceMethod(NSTextView.self, guarded) else { return }
        method_exchangeImplementations(a, b)
    }

    // MARK: - Propiedades privadas

    @MainActor fileprivate static var handler: (@MainActor (String) -> Bool)?
}

extension NSTextView {
    /// Tras `PasteGuard.install`, ocupa el lugar de `shouldChangeText(inRanges:replacementStrings:)`: llamarse
    /// a sí mismo es llamar al original.
    @objc fileprivate func alfalfa_shouldChangeText(inRanges ranges: [NSValue], replacementStrings strings: [String]?)
        -> Bool {
        // Sólo en el editor de un campo de texto (el cuadro es un `TextField`) y con texto largo: escribir no paga nada más.
        if isFieldEditor, let strings, let long = strings.first(where: Attachments.isLongPaste),
           MainActor.assumeIsolated({ PasteGuard.handler?(long) ?? false }) {
            return false
        }
        return alfalfa_shouldChangeText(inRanges: ranges, replacementStrings: strings)
    }
}
