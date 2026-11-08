import AlfalfaKit
import SwiftUI

/// Una fila del menú de «/».
struct CommandSuggestion {

    // MARK: - Tipos

    enum Action {
        case command(SlashCommand)
        case argument(SlashCommand, String)
    }

    // MARK: - Propiedades

    let id: String
    let symbol: String
    let title: String
    let detail: String
    let enabled: Bool
    let action: Action
}

// MARK: - Identifiable

extension CommandSuggestion: Identifiable {}

/// Menú de órdenes sobre el compositor, como el de Codex: icono, nombre y descripción;
/// ↑↓ para moverse, ↩ para ejecutar, ⇥ para completar y Esc para cerrar.
struct CommandPopup: View {

    // MARK: - Propiedades

    let suggestions: [CommandSuggestion]
    let selection: Int
    let choose: (CommandSuggestion) -> Void

    var body: some View {
        ScrollViewReader { proxy in
            ScrollView {
                VStack(alignment: .leading, spacing: 2) {
                    ForEach(Array(suggestions.enumerated()), id: \.element.id) { index, option in
                        row(option, selected: index == selection)
                    }
                }
                .padding(6)
            }
            .frame(height: min(CGFloat(suggestions.count) * 36 + 12, 8 * 36 + 12))
            .onChange(of: selection) { _, now in
                if let option = suggestions[safe: now] { proxy.scrollTo(option.id) }
            }
        }
        .background(Theme.card, in: RoundedRectangle(cornerRadius: 18))
        .overlay(RoundedRectangle(cornerRadius: 18).stroke(Theme.border))
        .accessibilityLabel("Órdenes")
    }

    // MARK: - Métodos privados

    private func row(_ option: CommandSuggestion, selected: Bool) -> some View {
        Button { choose(option) } label: {
            HStack(spacing: 12) {
                Image(systemName: option.symbol).font(.system(size: 14))
                    .foregroundStyle(Theme.secondary).frame(width: 20)
                Text(option.title).font(.system(size: 14, weight: .medium))
                    .foregroundStyle(option.enabled ? Theme.text : Theme.muted)
                Text(option.detail).font(.system(size: 13.5)).foregroundStyle(Theme.muted)
                    .lineLimit(1).truncationMode(.tail)
                Spacer(minLength: 0)
            }
            .padding(.horizontal, 12).frame(height: 34)
            .background(selected ? Theme.selection : .clear, in: RoundedRectangle(cornerRadius: 10))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain).id(option.id)
        .opacity(option.enabled ? 1 : 0.6)
    }
}
