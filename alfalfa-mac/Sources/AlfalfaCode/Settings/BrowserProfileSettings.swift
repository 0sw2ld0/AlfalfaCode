import AlfalfaKit
import SwiftUI

/// La selección y el borrado sólo los puede iniciar la persona desde Ajustes.
struct BrowserProfileSettings: View {

    // MARK: - Propiedades

    let project: URL?
    @ObservedObject var profiles: BrowserProfiles
    let canChange: Bool
    let change: (BrowserProfileMode, URL) -> String?
    let clear: (URL) async -> String?

    var body: some View {
        SettingsBlock(title: "Datos del navegador de este proyecto",
                      detail: "Aislado empieza limpio en cada sesión. Guardado conserva tus accesos sólo para "
                      + "esta carpeta, separado de Safari y de otros proyectos. "
                      + "Entra a mano con el relevo del agente.") {
            if let project {
                Text(Presentation.abbreviate(project.path)).font(Theme.mono).foregroundStyle(Theme.secondary)
                    .lineLimit(2).textSelection(.enabled)
                Picker("Perfil", selection: Binding(get: { profiles.mode(forProject: project) }, set: { mode in
                    notice = nil
                    error = change(mode, project)
                })) {
                    ForEach(BrowserProfileMode.allCases, id: \.self) { mode in Text(mode.title).tag(mode) }
                }
                .pickerStyle(.segmented).disabled(!canChange || profiles.isClearing(project))
                Text("Cambiar el modo cierra las páginas de este proyecto. No se copian los datos del modo aislado. "
                     + "Volver a Aislado deja de usar el perfil guardado; para borrarlo usa el botón de abajo.")
                    .font(.system(size: 11.5)).foregroundStyle(Theme.muted)
                Button("Borrar los datos del navegador de este proyecto…", role: .destructive) {
                    confirmingProject = project
                }
                .disabled(!canChange || profiles.isClearing(project) || !profiles.hasProfile(forProject: project))
                if profiles.isClearing(project) {
                    ProgressView("Borrando los datos de este proyecto…").controlSize(.small)
                } else if !canChange {
                    Text("Espera a que termine o detén el agente de este proyecto para cambiar estos ajustes.")
                        .font(.system(size: 11.5)).foregroundStyle(Theme.muted)
                }
            } else {
                Text("Elige una carpeta o abre una sesión para configurar el navegador de ese proyecto.")
                    .font(.system(size: 12)).foregroundStyle(Theme.muted)
            }
            if let error { Text(error).font(.system(size: 12)).foregroundStyle(Theme.danger) }
            if let notice { Text(notice).font(.system(size: 12)).foregroundStyle(Theme.alfalfa) }
        }
        .confirmationDialog("¿Borrar los datos del navegador de este proyecto?", isPresented: Binding(
            get: { confirmingProject != nil }, set: { if !$0 { confirmingProject = nil } })) {
            if let target = confirmingProject {
                Button("Borrar los datos", role: .destructive) { Task { await erase(target) } }
            }
            Button("Cancelar", role: .cancel) { confirmingProject = nil }
        } message: {
            Text("Se cerrarán las páginas y las sesiones web de " + (confirmingProject?.path ?? "este proyecto")
                 + ". Se borrarán sus cookies, almacenamiento y cachés. Los otros proyectos no se borran.")
        }
        .onChange(of: project) { _, _ in error = nil; notice = nil }
    }

    // MARK: - Propiedades privadas

    @State private var confirmingProject: URL?
    @State private var error: String?
    @State private var notice: String?

    // MARK: - Métodos privados

    private func erase(_ project: URL) async {
        confirmingProject = nil
        notice = nil
        error = await clear(project)
        if error == nil { notice = "Datos borrados. La próxima visita empieza sin las sesiones web anteriores." }
    }
}
