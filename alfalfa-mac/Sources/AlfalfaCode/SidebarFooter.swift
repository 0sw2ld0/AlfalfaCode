import AlfalfaKit
import SwiftUI

/// El pie de la barra lateral, como el de Claude: el nombre de Ajustes y el proveedor del modelo
/// elegido abren un menú con Ajustes, sus partes y las actualizaciones; la rueda abre Ajustes.
struct SidebarFooter: View {

    // MARK: - Propiedades

    @EnvironmentObject private var model: AppModel
    @Environment(\.openSettings) private var openSettings
    @AppStorage(Preferences.Key.displayName) private var savedName = ""

    var body: some View {
        HStack(spacing: 10) {
            accountMenu
            if busy { ProgressView().controlSize(.small).help("Buscando actualización de AlfalfaCode") }
            Button { open(.general) } label: {
                Image(systemName: "gearshape").font(.system(size: 15)).foregroundStyle(Theme.secondary)
                    .frame(width: 26, height: 26).contentShape(Rectangle())
            }
            .buttonStyle(.plain).help("Ajustes (⌘,)")
            .accessibilityLabel("Ajustes")
        }
        .padding(.horizontal, 14).padding(.vertical, 12)
        .overlay(alignment: .top) { Divider().overlay(Theme.border) }
    }

    // MARK: - Propiedades privadas

    private var name: String { Preferences.shownName(saved: savedName, system: NSFullUserName()) }

    private var busy: Bool { model.update == .checking || model.update == .updating }

    private var accountMenu: some View {
        Menu {
            Text("\(model.label(for: model.model)) · \(model.currentProviderName)")
            Button { open(.general) } label: { Label("Ajustes", systemImage: "gearshape") }
                .keyboardShortcut(",", modifiers: .command)
            Button { open(.proveedores) } label: { Label("Proveedores", systemImage: "server.rack") }
            Button { open(.modelo) } label: { Label("Modelo por defecto", systemImage: "cpu") }
            Divider()
            Button { model.checkForUpdate() } label: {
                Label(busy ? "Buscando actualización…" : "Buscar actualización", systemImage: "arrow.down.circle")
            }
            .disabled(busy)
            Button { Task { await model.reload() } } label: {
                Label("Actualizar sesiones y modelos", systemImage: "arrow.clockwise")
            }
        } label: {
            label
        }
        // Con el estilo de botón, el menú dibuja la etiqueta entera: el círculo, el nombre y la flecha.
        .menuStyle(.button).buttonStyle(.plain).menuIndicator(.hidden)
        .frame(maxWidth: .infinity, alignment: .leading)
        .help("Ajustes y actualizaciones")
        .accessibilityLabel("\(name), \(model.currentProviderName). Abre Ajustes y actualizaciones")
    }

    private var label: some View {
        HStack(spacing: 8) {
            Text(Preferences.initial(of: name))
                .font(.system(size: 11, weight: .semibold))
                .frame(width: 24, height: 24)
                .background(Theme.alfalfa, in: Circle()).foregroundStyle(Theme.dark)
            (Text(name) + Text(" · \(model.currentProviderName)").foregroundColor(Theme.muted))
                .lineLimit(1)
            Image(systemName: "chevron.down").font(.system(size: 10, weight: .semibold))
                .foregroundStyle(Theme.muted)
        }
        .contentShape(Rectangle())
    }

    // MARK: - Métodos privados

    /// Abre la ventana de Ajustes en una de sus partes.
    private func open(_ section: SettingsSection) {
        UserDefaults.standard.set(section.rawValue, forKey: Preferences.Key.section)
        openSettings()
    }
}
