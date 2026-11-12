import AlfalfaKit
import SwiftUI

/// Las partes de la ventana de Ajustes, en el orden de la lista lateral.
enum SettingsSection: String, CaseIterable, Identifiable {
    case general, proveedores, modelo, navegador, memoria, importar, actualizaciones

    // MARK: - Propiedades

    var id: String { rawValue }

    var title: String {
        switch self {
        case .general: return "General"
        case .proveedores: return "Proveedores"
        case .modelo: return "Modelo"
        case .navegador: return "Navegador"
        case .memoria: return "Memoria"
        case .importar: return "Importar"
        case .actualizaciones: return "Actualizaciones"
        }
    }

    var symbol: String {
        switch self {
        case .general: return "gearshape"
        case .proveedores: return "server.rack"
        case .modelo: return "cpu"
        case .navegador: return "globe"
        case .memoria: return "brain"
        case .importar: return "square.and.arrow.down.on.square"
        case .actualizaciones: return "arrow.down.circle"
        }
    }

    // MARK: - Métodos públicos

    /// Para revisar la interfaz sin clics: `ALFALFA_ABRIR_AJUSTES=modelo` abre Ajustes en esa parte
    /// al arrancar, y `ALFALFA_ABRIR_PROVEEDORES`, como antes, en Proveedores.
    static func requestedAtLaunch(_ environment: [String: String] = ProcessInfo.processInfo.environment) -> Self? {
        if let wanted = environment["ALFALFA_ABRIR_AJUSTES"] { return Self(rawValue: wanted) ?? .general }
        return environment["ALFALFA_ABRIR_PROVEEDORES"] == nil ? nil : .proveedores
    }
}

/// La ventana de Ajustes (⌘,), como la de Claude: una lista de partes a la izquierda.
struct SettingsView: View {

    // MARK: - Propiedades

    @EnvironmentObject private var model: AppModel
    @AppStorage(Preferences.Key.section) private var section = SettingsSection.general.rawValue

    var body: some View {
        HStack(spacing: 0) {
            sidebar
            Divider().overlay(Theme.border)
            ScrollView {
                detail
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(28)
            }
            .background(Theme.background)
        }
        .frame(width: 760, height: 560)
        .foregroundStyle(Theme.text)
        .preferredColorScheme(.dark)
        .navigationTitle("Ajustes")
        .modifier(UpdateAlert())
    }

    // MARK: - Propiedades privadas

    private var selected: SettingsSection { SettingsSection(rawValue: section) ?? .general }

    private var sidebar: some View {
        VStack(alignment: .leading, spacing: 2) {
            ForEach(SettingsSection.allCases) { item in
                Button { section = item.rawValue } label: {
                    HStack(spacing: 10) {
                        Image(systemName: item.symbol).frame(width: 18)
                        Text(item.title)
                        Spacer()
                    }
                    .padding(.horizontal, 10).frame(minHeight: 32)
                    .background(item == selected ? Theme.selection : .clear, in: RoundedRectangle(cornerRadius: 7))
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityAddTraits(item == selected ? .isSelected : [])
            }
            Spacer()
        }
        .padding(10).padding(.top, 8)
        .frame(width: 200)
        .background(Theme.sidebar)
    }

    @ViewBuilder private var detail: some View {
        switch selected {
        case .general: GeneralSettings()
        case .proveedores: ProvidersView()
        case .modelo: ModelSettings()
        case .navegador: BrowserSettings()
        case .memoria: MemorySettings()
        case .importar: ImportSettings()
        case .actualizaciones: UpdateSettings()
        }
    }
}
