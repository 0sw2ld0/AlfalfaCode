import AlfalfaKit
import SwiftUI

struct BrowserExportCard: View {

    // MARK: - Propiedades

    let offer: BrowserExportOffer
    let blocked: String?
    let save: @MainActor (String, String) -> Void
    @State private var path: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Label("Prueba de navegador", systemImage: "checkmark.rectangle")
                .font(.system(size: 13, weight: .semibold)).foregroundStyle(Theme.text)
            switch offer {
            case let .manual(reason):
                Text(reason).foregroundStyle(Theme.secondary)
            case let .ready(id, suggestedPath, steps, variables):
                Text("\(steps) pasos comprobados. Configura las variables del entorno antes de ejecutar la prueba.")
                    .foregroundStyle(Theme.secondary)
                DisclosureGroup("Variables del entorno") {
                    VStack(alignment: .leading, spacing: 6) {
                        ForEach(variables) { variable in
                            Text(variable.id).font(.system(size: 11, design: .monospaced)).textSelection(.enabled)
                            Text("Paso \(variable.step) · \(variable.use)" + (variable.role.map { " · \($0)" } ?? ""))
                                .foregroundStyle(Theme.secondary)
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
                let destination = Binding(get: { path ?? suggestedPath }, set: { path = $0 })
                HStack {
                    TextField("Ruta de la prueba", text: destination)
                        .font(.system(size: 11, design: .monospaced))
                    Button("Guardar prueba de Playwright") { save(id, destination.wrappedValue) }
                }
                .disabled(blocked != nil)
                if let blocked { Text(blocked).foregroundStyle(Theme.secondary) }
            }
        }
        .font(.system(size: 12)).foregroundStyle(Theme.text)
        .padding(12).frame(maxWidth: .infinity, alignment: .leading)
        .background(Theme.selection, in: RoundedRectangle(cornerRadius: 8))
    }

    // MARK: - Inicializadores

    init(offer: BrowserExportOffer, blocked: String?, save: @escaping @MainActor (String, String) -> Void) {
        self.offer = offer
        self.blocked = blocked
        self.save = save
    }
}
