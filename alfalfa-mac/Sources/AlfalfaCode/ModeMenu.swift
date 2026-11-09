import AlfalfaKit
import SwiftUI

/// Selector de modo de permisos, como el de Claude Code: Auto, Manual, Aceptar
/// ediciones y Plan, y aparte «Omitir permisos», que se confirma antes de activarse.
struct ModeMenu: View {
    let mode: String
    var help = "Modo de permisos"
    let choose: (String) -> Void

    @State private var open = false
    @State private var confirming = false

    private var current: PermissionMode { PermissionMode.named(mode) }

    var body: some View {
        Button { open.toggle() } label: {
            HStack(spacing: 4) {
                Image(systemName: current.symbol).font(.system(size: 11))
                Text(current.title).font(.system(size: 12.5))
                Image(systemName: "chevron.down").font(.system(size: 9, weight: .semibold))
            }
            .foregroundStyle(current == .omitir ? Theme.danger : Theme.secondary)
            .padding(.horizontal, 8).frame(height: 24)
            .background(current == .auto ? Color.clear : Theme.selection, in: RoundedRectangle(cornerRadius: 6))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain).help(help).accessibilityLabel("Modo: " + current.title)
        .popover(isPresented: $open, arrowEdge: .top) { panel }
        .alert("¿Omitir permisos?", isPresented: $confirming) {
            Button("Habilitar", role: .destructive) { choose(PermissionMode.omitir.rawValue) }
            Button("Cancelar", role: .cancel) {}
        } message: {
            Text("El agente ejecutará cualquier orden sin preguntarte, también fuera del sandbox, con todos los permisos de tu usuario y con red. Sólo las reglas prohibidas lo frenan.")
        }
    }

    private var panel: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text("Modo").font(.system(size: 12.5)).foregroundStyle(Theme.muted)
                .padding(.horizontal, 12).padding(.top, 10).padding(.bottom, 4)
            ForEach(Array([PermissionMode.auto, .manual, .ediciones, .plan].enumerated()), id: \.element) { index, option in
                Button { pick(option) } label: { row(option, number: index + 1) }
                    .buttonStyle(.plain)
                    .keyboardShortcut(KeyEquivalent(Character(String(index + 1))), modifiers: [])
            }
            Divider().overlay(Theme.border).padding(.vertical, 4)
            Button { pick(.omitir) } label: {
                HStack {
                    Text(PermissionMode.omitir.title).font(.system(size: 14))
                        .foregroundStyle(current == .omitir ? Theme.danger : Theme.text)
                    Spacer()
                    Text(current == .omitir ? "Activo" : "Habilitar").font(.system(size: 13))
                        .foregroundStyle(current == .omitir ? Theme.danger : Theme.secondary)
                }
                .padding(.horizontal, 12).frame(height: 36).contentShape(Rectangle())
            }
            .buttonStyle(.plain)
        }
        .padding(6)
        .frame(width: 330)
        .background(Theme.card)
    }

    private func row(_ option: PermissionMode, number: Int) -> some View {
        HStack(alignment: .top, spacing: 10) {
            VStack(alignment: .leading, spacing: 3) {
                Text(option.title).font(.system(size: 14)).foregroundStyle(Theme.text)
                Text(option.summary).font(.system(size: 12.5)).foregroundStyle(Theme.muted)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 8)
            if option == current {
                Image(systemName: "checkmark").font(.system(size: 13, weight: .semibold)).foregroundStyle(Theme.link)
            }
            Text("\(number)").font(.system(size: 12.5)).foregroundStyle(Theme.muted).monospacedDigit()
        }
        .padding(.horizontal, 12).padding(.vertical, 9)
        .background(option == current ? Theme.selection : .clear, in: RoundedRectangle(cornerRadius: 9))
        .contentShape(Rectangle())
    }

    private func pick(_ option: PermissionMode) {
        open = false
        if option.needsConfirmation && current != option {
            confirming = true
        } else {
            choose(option.rawValue)
        }
    }
}
