import AppKit
import Foundation
import WebKit

/// Adaptador mínimo del hover de WebKit. No mueve el cursor del sistema ni usa perfiles ajenos.
enum BrowserPointer {
    /// Apple usa esta misma entrada para hover en WebKitTestRunner/EventSenderProxy.mm.
    /// Es SPI de WebKit: se comprueba su disponibilidad y se falla de forma explícita si cambia.
    /// https://github.com/WebKit/WebKit/blob/main/Tools/WebKitTestRunner/mac/EventSenderProxy.mm
    static func move(in view: WKWebView, to point: NSPoint, window: NSWindow) throws {
        guard window.isKeyWindow else {
            throw BrowserFailure("Activa la ventana de AlfalfaCode para pasar el puntero por su navegador.")
        }
        let selector = NSSelectorFromString("_simulateMouseMove:")
        guard view.responds(to: selector) else {
            throw BrowserFailure("Esta versión de WebKit no admite el hover del navegador de AlfalfaCode.")
        }
        guard let event = NSEvent.mouseEvent(with: .mouseMoved, location: point, modifierFlags: [],
                                             timestamp: ProcessInfo.processInfo.systemUptime,
                                             windowNumber: window.windowNumber, context: nil,
                                             eventNumber: 1, clickCount: 0, pressure: 0) else {
            throw BrowserFailure("No se pudo crear el movimiento del puntero del navegador.")
        }
        view.perform(selector, with: event)
    }
}
