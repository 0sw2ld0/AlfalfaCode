import Foundation

/// El menú de clic derecho del título de una carpeta en la barra lateral.
public enum FolderMenu {
    /// Sólo las carpetas de verdad: ni «Fijadas» ni las sesiones sin carpeta.
    public static func applies(folder: String, title: String?) -> Bool {
        title == nil && folder.hasPrefix("/")
    }

    /// Lo que dice la confirmación de «Quitar de la lista…».
    public static func removalMessage(name: String, sessions: Int, running: Int) -> String {
        var text = sessions == 0 ? "«\(name)» deja de salir en las carpetas recientes."
            : "Se archiva" + (sessions == 1 ? " su sesión" : "n sus \(sessions) sesiones")
                + " y «\(name)» deja de salir en las carpetas recientes."
        if sessions > 0 { text += " Las recuperas con «Mostrar archivadas»." }
        if running > 0 {
            text += running == 1 ? " La que está trabajando se queda." : " Las \(running) que están trabajando se quedan."
        }
        return text + " La carpeta y sus archivos no se tocan."
    }
}
