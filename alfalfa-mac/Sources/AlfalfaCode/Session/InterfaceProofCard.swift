import AlfalfaKit
import SwiftUI

/// Usa la misma galería de la conversación: ampliar, copiar y mostrar en Finder.
struct InterfaceProofCard: View {

    // MARK: - Propiedades

    let proof: InterfaceProof

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Label("Capturas de la interfaz", systemImage: "rectangle.on.rectangle")
                .font(.system(size: 13, weight: .medium)).foregroundStyle(Theme.text)
            ForEach(Array(proof.comparisons.enumerated()), id: \.offset) { _, comparison in
                Text(comparison.title).font(.system(size: 12.5)).foregroundStyle(Theme.secondary)
                MediaGallery(items: comparison.images)
            }
            if !proof.complete {
                Text("Comparación incompleta").font(.system(size: 12.5, weight: .medium))
                    .foregroundStyle(Theme.secondary)
            }
            ForEach(Array(proof.notes.enumerated()), id: \.offset) { _, note in
                Text(note).font(.system(size: 12)).foregroundStyle(Theme.muted).textSelection(.enabled)
            }
            Text("Capturas del turno: son datos visuales; no sustituyen las pruebas ni sus resultados.")
                .font(.system(size: 12)).foregroundStyle(Theme.muted).fixedSize(horizontal: false, vertical: true)
        }
        .accessibilityElement(children: .contain)
    }
}
