import AlfalfaKit
import AppKit
import SwiftUI

/// «Reportar un error»: un formulario corto. El motor lo manda al servicio Alfalfa, firmado si
/// este Mac tiene un código de cortesía. Si no hay servicio o no lo acepta, se ofrece escribir
/// al correo de soporte con el texto ya puesto.
struct ReportView: View {

    // MARK: - Propiedades

    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss
    @State private var text = ""
    @State private var contact = ""
    @State private var diagnostics = true
    @State private var sending = false
    @State private var result: ReportResult?

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            VStack(alignment: .leading, spacing: 4) {
                Text("Reportar un error").font(.system(size: 16, weight: .semibold))
                Text("Cuéntanos qué pasó y qué esperabas. Lo leemos en CuyCoders.")
                    .font(.system(size: 12.5)).foregroundStyle(Theme.muted)
            }
            if let result { outcome(result) } else { form }
        }
        .padding(20)
        .frame(width: 520)
        .background(Theme.background)
    }

    // MARK: - Propiedades privadas

    private var trimmed: String { text.trimmingCharacters(in: .whitespacesAndNewlines) }

    private var form: some View {
        VStack(alignment: .leading, spacing: 12) {
            TextEditor(text: $text)
                .font(.system(size: 13)).scrollContentBackground(.hidden)
                .padding(8).frame(height: 150)
                .background(Theme.field, in: RoundedRectangle(cornerRadius: 9))
                .overlay(RoundedRectangle(cornerRadius: 9).stroke(Theme.border))
                .overlay(alignment: .topLeading) {
                    if text.isEmpty {
                        Text("Qué pasó, qué estabas haciendo y qué esperabas")
                            .font(.system(size: 13)).foregroundStyle(Theme.muted)
                            .padding(.horizontal, 13).padding(.vertical, 8).allowsHitTesting(false)
                    }
                }
                .accessibilityLabel("Qué pasó")
            TextField("Tu correo, si quieres respuesta (opcional)", text: $contact)
                .textFieldStyle(.plain).padding(.horizontal, 10).frame(height: 32)
                .background(Theme.field, in: RoundedRectangle(cornerRadius: 9))
                .overlay(RoundedRectangle(cornerRadius: 9).stroke(Theme.border))
            Toggle(isOn: $diagnostics) {
                VStack(alignment: .leading, spacing: 2) {
                    Text("Adjuntar diagnóstico").font(.system(size: 13))
                    Text("Versión, sistema, qué proveedores están configurados y el último error. "
                         + "Nunca tus conversaciones ni tus claves.")
                        .font(.system(size: 11.5)).foregroundStyle(Theme.muted)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            .toggleStyle(.checkbox)
            HStack {
                Spacer()
                Button("Cancelar", role: .cancel) { dismiss() }.keyboardShortcut(.cancelAction)
                Button(sending ? "Enviando…" : "Enviar") { send() }
                    .keyboardShortcut(.defaultAction)
                    .disabled(trimmed.isEmpty || sending)
            }
        }
    }

    private func outcome(_ result: ReportResult) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            if result.sent {
                let number = result.id.map { " (#\($0))" } ?? ""
                Label("Reporte enviado\(number). Gracias.", systemImage: "checkmark.circle.fill")
                    .foregroundStyle(Theme.alfalfaLight).font(.system(size: 13.5, weight: .medium))
                Text(contact.isEmpty ? "Si quieres contarnos más, escribe a \(result.support)."
                                     : "Te responderemos a \(contact.trimmingCharacters(in: .whitespaces)).")
                    .font(.system(size: 12.5)).foregroundStyle(Theme.muted)
            } else {
                Label("No se pudo enviar desde la app", systemImage: "exclamationmark.triangle.fill")
                    .foregroundStyle(Theme.warning).font(.system(size: 13.5, weight: .medium))
                Text((result.reason.map { $0 + " " } ?? "")
                     + "Puedes mandarlo por correo a \(result.support): se abre con tu texto ya puesto.")
                    .font(.system(size: 12.5)).foregroundStyle(Theme.muted)
                    .fixedSize(horizontal: false, vertical: true)
                    .textSelection(.enabled)
            }
            HStack {
                Spacer()
                if result.sent {
                    Button("Listo") { dismiss() }.keyboardShortcut(.defaultAction)
                } else {
                    Button("Cerrar", role: .cancel) { dismiss() }.keyboardShortcut(.cancelAction)
                    Button("Escribir a \(result.support)") { email(result.support); dismiss() }
                        .keyboardShortcut(.defaultAction)
                }
            }
        }
    }

    // MARK: - Métodos privados

    private func send() {
        sending = true
        Task {
            result = await model.report(text: trimmed, contact: contact.trimmingCharacters(in: .whitespaces),
                                        diagnostics: diagnostics)
            sending = false
        }
    }

    /// Abre el programa de correo con el reporte ya escrito. No se envía nada solo.
    private func email(_ address: String) {
        var parts = URLComponents()
        parts.scheme = "mailto"
        parts.path = address
        parts.queryItems = [URLQueryItem(name: "subject", value: "Error en AlfalfaCode"),
                            URLQueryItem(name: "body", value: trimmed)]
        if let url = parts.url { NSWorkspace.shared.open(url) }
    }
}
