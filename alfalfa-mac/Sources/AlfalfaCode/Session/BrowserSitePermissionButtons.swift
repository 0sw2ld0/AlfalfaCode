import AlfalfaKit
import SwiftUI

struct BrowserSitePermissionButtons: View {
    let request: PermissionRequest
    @ObservedObject var session: LiveSession

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Este permiso sólo permite visitar ese origen (esquema, servidor y puerto). "
                 + "Formularios, contraseñas, descargas y red local conservan sus permisos.")
                .font(.system(size: 12)).foregroundStyle(Theme.muted)
            HStack(spacing: 10) {
                choice("Una vez", "una_vez")
                choice("Este sitio · sesión", "sesion")
                choice("Siempre · este proyecto", "proyecto")
                Button("Rechazar") { session.answer(request.id, approved: false) }
            }
            .buttonStyle(.bordered)
            .disabled(session.answered.contains(request.id))
        }
    }

    private func choice(_ label: String, _ scope: String) -> some View {
        Button(label) { session.answerSitePermission(request.id, scope: scope) }
    }
}
