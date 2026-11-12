import AlfalfaKit
import SwiftUI

struct BrowserSiteSettings: View {

    // MARK: - Propiedades

    @EnvironmentObject private var app: AppModel
    private let project: URL?
    @StateObject private var model: BrowserSiteSettingsModel
    @State private var allowed = ""
    @State private var denied = ""
    @State private var strict = false

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            Text(project.map { "Proyecto: " + $0.path } ?? "Elige una carpeta para configurar sus sitios.")
                .font(.system(size: 12, design: .monospaced)).foregroundStyle(Theme.secondary)
            if let error = model.error { Text(error).foregroundStyle(Theme.danger) }
            if let state = model.state {
                lists
                SettingsBlock(title: "Subrecursos",
                              detail: "Por defecto se controlan las navegaciones y redirecciones. El modo estricto "
                              + "también bloquea imágenes, scripts, marcos y peticiones de otros orígenes; puede "
                              + "impedir que funcionen sitios con servicios externos. "
                              + "La barrera de red local sigue activa.") {
                    Toggle("Bloquear también los subrecursos de sitios sin permiso", isOn: $strict)
                    if state.effectiveStrict && !state.strict {
                        Text("El proyecto exige el modo estricto; no se puede desactivar desde aquí.")
                            .foregroundStyle(Theme.muted).font(.system(size: 12))
                    }
                }
                if let limit = state.projectLimit {
                    Text("El proyecto limita los orígenes a: "
                         + (limit.isEmpty ? "ninguno" : limit.joined(separator: ", ")))
                        .font(.system(size: 12)).foregroundStyle(Theme.muted)
                }
                let additionalDenials = Set(state.effectiveDenied).subtracting(state.denied).sorted()
                if !additionalDenials.isEmpty {
                    Text("Además, el proyecto bloquea: " + additionalDenials.joined(separator: ", "))
                        .font(.system(size: 12)).foregroundStyle(Theme.muted)
                }
                HStack {
                    Button("Revisar cambios…") { model.propose(allowed: allowed, denied: denied, strict: strict) }
                    Button("Descartar cambios") { draft(state) }
                }
                .disabled(model.busy)
            }
        }
        .task(id: project) { model.start(launcher: app.launcher, directory: project) }
        .onChange(of: model.state) { _, state in if let state { draft(state) } }
        .onDisappear { model.stop() }
        .sheet(isPresented: Binding(get: { model.permission != nil },
                                    set: { if !$0 { model.answer(approved: false) } })) {
            confirmation
        }
    }

    // MARK: - Propiedades privadas

    private var lists: some View {
        SettingsBlock(title: "Permisos por sitio · sólo este proyecto",
                      detail: "Un origen por línea: https://ejemplo.com:443. Se distingue esquema y puerto. "
                      + "Los bloqueados tienen prioridad. Un archivo del proyecto sólo puede restringir estas reglas. "
                      + "Dar permiso a un sitio no autoriza enviar formularios, contraseñas "
                      + "ni acceder a la red local.") {
            editor("Permitidos siempre en este proyecto", text: $allowed)
            editor("Bloqueados en este proyecto", text: $denied)
        }
        .disabled(model.busy)
    }

    private var confirmation: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("Confirmar reglas de este proyecto").font(.headline)
            ScrollView { Text(model.permission?.diff ?? "").font(.system(size: 12, design: .monospaced)) }
            HStack {
                Button("Guardar reglas") { model.answer(approved: true) }
                Button("Cancelar") { model.answer(approved: false) }
            }
        }
        .padding(24).frame(width: 600, height: 440)
    }

    // MARK: - Inicializadores

    @MainActor
    init(project: URL?, model: BrowserSiteSettingsModel? = nil) {
        self.project = project
        _model = StateObject(wrappedValue: model ?? BrowserSiteSettingsModel())
    }

    // MARK: - Métodos privados

    private func editor(_ title: String, text: Binding<String>) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title).font(.system(size: 13, weight: .semibold))
            TextEditor(text: text).font(.system(size: 12, design: .monospaced))
                .frame(height: 72).scrollContentBackground(.hidden).padding(4)
                .background(Theme.field, in: RoundedRectangle(cornerRadius: 6)).accessibilityLabel(title)
        }
    }

    private func draft(_ state: BrowserSiteSettingsState) {
        allowed = state.allowed.joined(separator: "\n")
        denied = state.denied.joined(separator: "\n")
        strict = state.strict
    }
}
