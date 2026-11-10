import AlfalfaKit
import SwiftUI

struct TaskQueueEditor: View {
    let folder: String
    let save: ([String], String, Int) -> Void
    @Environment(\.dismiss) private var dismiss
    private struct Draft: Identifiable { let id = UUID(); var text = "" }
    @State private var drafts = [Draft(), Draft()]
    @State private var mode = "serie"
    @State private var parallelism = 2

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("Cola de encargos").font(.title2)
            Text(Presentation.abbreviate(folder)).foregroundStyle(Theme.muted)
            Picker("Ejecución", selection: $mode) {
                Text("Una tras otra").tag("serie")
                Text("En paralelo en worktrees").tag("paralelo")
            }.pickerStyle(.segmented)
            if mode == "paralelo" {
                Stepper("Hasta \(parallelism) a la vez", value: $parallelism, in: 1...4)
                Text("Para tareas independientes. El repositorio debe estar limpio: cada rama parte del mismo HEAD. "
                     + "Los worktrees se conservan para revisar los resultados; los archivos ignorados no se copian.")
                    .font(.system(size: 12)).foregroundStyle(Theme.secondary)
            } else {
                Text("Cada tarea tiene su propia sesión y ve los cambios que dejó la anterior en esta carpeta.")
                    .font(.system(size: 12)).foregroundStyle(Theme.secondary)
            }
            ScrollView {
                VStack(spacing: 12) {
                    ForEach($drafts) { $draft in
                        let id = draft.id
                        let index = drafts.firstIndex { $0.id == id } ?? 0
                        HStack(alignment: .top) {
                            Text("\(index + 1).").foregroundStyle(Theme.muted)
                            TextField("Describe este encargo completo", text: $draft.text, axis: .vertical)
                                .lineLimit(3...6).textFieldStyle(.roundedBorder)
                                .accessibilityLabel("Encargo \(index + 1)")
                            VStack {
                                Button {
                                    if let n = drafts.firstIndex(where: { $0.id == id }), n > 0 {
                                        drafts.swapAt(n, n - 1)
                                    }
                                } label: { Image(systemName: "arrow.up") }
                                    .disabled(index == 0).help("Subir encargo")
                                Button { drafts.removeAll { $0.id == id } } label: { Image(systemName: "minus") }
                                    .disabled(drafts.count == 1).help("Quitar encargo")
                            }
                        }
                    }
                }
            }
            HStack {
                Button("Añadir encargo") { drafts.append(Draft()) }.disabled(drafts.count >= 20)
                Spacer()
                Button("Cancelar") { dismiss() }
                Button("Guardar cola") {
                    save(drafts.map(\.text), mode, parallelism)
                    dismiss()
                }
                .keyboardShortcut(.defaultAction)
                .disabled(drafts.contains { $0.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                    || $0.text.count > 20000 })
            }
            Text("No empieza hasta que pulses «Iniciar cola». Conserva los permisos y comparte el presupuesto.")
                .font(.system(size: 12)).foregroundStyle(Theme.muted)
        }
        .padding(24).frame(width: 660, height: 570).background(Theme.background)
    }
}
