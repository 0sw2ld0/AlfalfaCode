import AlfalfaKit
import SwiftUI

/// «Presupuesto de la sesión  1,22 $ de 20,00 $ ✎» en el panel derecho. Al pulsarlo se cambia
/// el tope ahí mismo, sin ir a Ajustes. El cambio vale sólo para esta sesión (también al
/// reanudarla); el de Ajustes ▸ General ▸ Presupuesto sigue igual para las demás y las nuevas.
struct SessionBudgetRow: View {
    let budget: BudgetStatus?
    /// Lo gastado si el motor aún no mandó `presupuesto` (sesión sin topes ni caché).
    let spent: Double?
    /// El tope propio de la sesión; nil si sigue el de Ajustes.
    let sessionCap: SessionCap?
    let onChange: (SessionCap?) -> Void

    @State private var editing = false
    @State private var text = ""
    @FocusState private var focused: Bool

    var body: some View {
        HStack(spacing: 6) {
            Text("Presupuesto de la sesión").lineLimit(1)
            Spacer(minLength: 4)
            Button { open() } label: {
                HStack(spacing: 5) {
                    Text(value).monospacedDigit().lineLimit(1)
                    Image(systemName: "pencil").font(.system(size: 10.5, weight: .semibold)).foregroundStyle(Theme.muted)
                }
                .fixedSize()
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .help("Cambiar el tope de gasto de esta sesión")
            .accessibilityLabel("Presupuesto de la sesión: " + value + ". Cambiar")
            .popover(isPresented: $editing, arrowEdge: .leading) { editor }
        }
        .font(.system(size: 12.5)).foregroundStyle(Theme.secondary)
    }

    // MARK: - Propiedades privadas

    private var generalCap: Double? { Preferences().sessionBudget }

    /// El tope que vale ahora: el que dice el motor y, si aún no lo dijo, el guardado.
    private var cap: Double? {
        if let budget { return budget.session.cap }
        return sessionCap.map(\.value) ?? generalCap
    }

    private var spentNow: Double { budget?.session.spent ?? spent ?? 0 }

    private var value: String {
        // Sin tope basta decirlo: lo gastado ya está en «Coste estimado de la sesión».
        guard let cap else { return "Sin tope" }
        // «1,77 / 5,00 $»: cabe en una línea con el ancho normal del panel.
        let spent = CatalogFormat.cost(spentNow)
        return (spent.hasSuffix(" $") ? String(spent.dropLast(2)) : spent) + " / " + CatalogFormat.cost(cap)
    }

    private var entry: SessionCap? { SessionCap(entry: text) }

    private var editor: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Tope de esta sesión").font(.system(size: 13, weight: .semibold))
            HStack(spacing: 6) {
                TextField("Sin tope", text: $text).textFieldStyle(.roundedBorder).frame(width: 90)
                    .multilineTextAlignment(.trailing)
                    .focused($focused)
                    .onSubmit(apply)
                    .accessibilityLabel("Tope de esta sesión en dólares")
                Text("$").foregroundStyle(Theme.secondary)
                Spacer(minLength: 8)
                Button("Poner", action: apply).keyboardShortcut(.defaultAction).disabled(entry == nil)
            }
            if entry == nil {
                Text("Escribe un importe, como 5 o 12,50").font(.system(size: 11.5)).foregroundStyle(Theme.danger)
            }
            HStack(spacing: 8) {
                Button("Quitar tope") { finish(.noCap) }.disabled(cap == nil)
                if sessionCap != nil {
                    Button("Usar el de Ajustes") { finish(nil) }
                        .help("Vuelve al tope de Ajustes ▸ General ▸ Presupuesto: "
                              + (generalCap.map { CatalogFormat.cost($0) } ?? "sin tope"))
                }
            }
            Text("Sólo para esta sesión, también al reanudarla. El tope de Ajustes ▸ General ▸ Presupuesto "
                 + "(" + (generalCap.map { CatalogFormat.cost($0) } ?? "sin tope") + ") sigue igual para las demás.")
                .font(.system(size: 11.5)).foregroundStyle(Theme.muted)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(14).frame(width: 280)
    }

    // MARK: - Métodos privados

    private func open() {
        text = BudgetFormat.field(cap)
        editing = true
        focused = true
    }

    private func apply() {
        guard let entry else { return }
        finish(entry)
    }

    private func finish(_ cap: SessionCap?) {
        onChange(cap)
        editing = false
    }
}
