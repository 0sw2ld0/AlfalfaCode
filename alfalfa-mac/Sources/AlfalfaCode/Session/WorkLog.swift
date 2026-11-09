import AlfalfaKit
import SwiftUI

/// Las llamadas seguidas del agente en una fila plegada, como en la app de Claude:
/// «Ejecutó 3 comandos, leyó 2 archivos ›». Al abrirla, una fila por llamada con su
/// descripción; al abrir una fila, el comando con su `$` y la salida.
///
/// Mientras trabaja, el grupo sigue plegado y debajo se ve la llamada en curso con su
/// descripción: así lo hace Claude, y la conversación no salta con cada salida larga.
struct WorkLog: View {

    // MARK: - Propiedades

    let events: [ToolEvent]
    let running: Bool
    @Binding var expanded: Bool

    var body: some View {
        let entries = ToolCalls.entries(from: events)
        let calls = entries.compactMap { if case let .call(call) = $0 { return call } else { return nil } }
        let current = running ? calls.last { !$0.finished } : nil
        VStack(alignment: .leading, spacing: 0) {
            Button { withAnimation(.easeOut(duration: 0.15)) { expanded.toggle() } } label: {
                HStack(spacing: 6) {
                    Text(title(calls: calls, entries: entries))
                        .font(.system(size: 14)).foregroundStyle(Theme.muted).lineLimit(1)
                    Image(systemName: "chevron.right").font(.system(size: 11, weight: .semibold))
                        .foregroundStyle(Theme.muted).rotationEffect(.degrees(expanded ? 90 : 0))
                }
                .padding(.vertical, 6).contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityLabel(title(calls: calls, entries: entries))
            .accessibilityHint(expanded ? "Plegar las llamadas" : "Ver cada llamada")
            if expanded {
                VStack(alignment: .leading, spacing: 2) {
                    ForEach(entries) { entry in
                        switch entry {
                        case let .call(call): ToolCallRow(call: call, live: running && !call.finished)
                        case let .note(event): NoteRow(event: event)
                        }
                    }
                }
                .padding(.top, 4)
            } else if let current {
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    ProgressView().controlSize(.mini)
                    VStack(alignment: .leading, spacing: 1) {
                        Text(current.description + "…").font(.system(size: 13)).foregroundStyle(Theme.secondary)
                            .lineLimit(1)
                        if let command = current.command { CommandText(command: command) }
                    }
                }
                .padding(.vertical, 3)
                .accessibilityElement(children: .combine)
            }
        }
    }

    // MARK: - Métodos privados

    private func title(calls: [ToolCall], entries: [WorkEntry]) -> String {
        if !calls.isEmpty { return ToolCalls.summary(calls) }
        // Sólo avisos del sistema (un proceso en segundo plano terminó, un reintento).
        if case let .note(event)? = entries.first {
            return entries.count == 1 ? event.title : event.title + " · y \(entries.count - 1) más"
        }
        return running ? "Trabajando…" : "Sin acciones"
    }
}

/// Una llamada: su descripción y, al pulsarla, el comando y la salida.
struct ToolCallRow: View {

    // MARK: - Propiedades

    let call: ToolCall
    let live: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Button { withAnimation(.easeOut(duration: 0.12)) { open.toggle() } } label: {
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Image(systemName: icon).font(.system(size: 11)).foregroundStyle(Theme.muted).frame(width: 14)
                    // La descripción la escribe el modelo: el comando va siempre debajo, para que
                    // una orden que se aprobó sola no se esconda tras una frase.
                    VStack(alignment: .leading, spacing: 1) {
                        HStack(alignment: .firstTextBaseline, spacing: 8) {
                            Text(call.description).font(.system(size: 13)).foregroundStyle(Theme.secondary)
                                .lineLimit(1)
                            badge
                            Image(systemName: "chevron.right").font(.system(size: 9, weight: .semibold))
                                .foregroundStyle(Theme.muted).rotationEffect(.degrees(open ? 90 : 0))
                        }
                        if let command = call.command, !open { CommandText(command: command) }
                    }
                    Spacer(minLength: 0)
                }
                .padding(.vertical, 4).contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .help(call.command.map { "$ " + $0 } ?? call.description)
            .accessibilityLabel(call.description + (call.command.map { ", orden " + $0 } ?? "")
                                + (call.interrupted ? ", interrumpida" : call.failed ? ", falló" : ""))
            if open { detail.padding(.leading, 22).padding(.bottom, 6) }
        }
    }

    // MARK: - Propiedades privadas

    @State private var open = false

    /// En curso, interrumpida, fallida o con un código que el motor dio por bueno (en gris).
    @ViewBuilder private var badge: some View {
        if live {
            ProgressView().controlSize(.mini)
        } else if call.interrupted {
            Text("interrumpida").font(.system(size: 11.5)).foregroundStyle(Theme.warning)
        } else if call.failed {
            Text(call.exitCode.map { "código \($0)" } ?? "falló").font(.system(size: 11.5)).foregroundStyle(Theme.danger)
        } else if let code = call.exitCode, code != 0 {
            Text("código \(code)").font(.system(size: 11.5)).foregroundStyle(Theme.muted)
                .help("El código no es 0, pero el resultado es el esperado")
        }
    }

    @ViewBuilder private var detail: some View {
        VStack(alignment: .leading, spacing: 0) {
            Text(header).font(Theme.mono).foregroundStyle(Theme.text).textSelection(.enabled)
                .fixedSize(horizontal: false, vertical: true)
                .padding(10).frame(maxWidth: .infinity, alignment: .leading)
            if let output = call.output, !output.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                Divider().overlay(Theme.border)
                ScrollView {
                    Text(output).font(.system(size: 12, design: .monospaced)).foregroundStyle(Theme.secondary)
                        .textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
                        .padding(10).frame(maxWidth: .infinity, alignment: .leading)
                }
                .frame(maxHeight: 220)
            } else if !call.finished {
                Divider().overlay(Theme.border)
                Text("Sin salida todavía").font(.system(size: 12)).foregroundStyle(Theme.muted).padding(10)
            }
        }
        .background(Theme.field, in: RoundedRectangle(cornerRadius: 8))
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(Theme.border))
    }

    /// `$ git status` en las órdenes; en lo demás, la llamada tal como la pidió el modelo.
    private var header: String {
        if let command = call.command { return "$ " + command }
        return call.request ?? call.name
    }

    private var icon: String {
        switch call.kind {
        case .command: return "terminal"
        case .read, .list: return "doc.text"
        case .search: return "magnifyingglass"
        case .edit: return "pencil"
        case .web: return "globe"
        case .image: return "photo"
        case .app: return "macwindow"
        case .browser: return "safari"
        case .agent: return "person.2"
        case .plan: return "checklist"
        case .tools: return "wrench.and.screwdriver"
        case .other: return call.name == "wrap_up" ? "checkmark.circle" : "circle"
        }
    }
}

/// Un aviso del sistema entre llamadas; al pulsarlo, el texto entero.
private struct NoteRow: View {
    let event: ToolEvent
    @State private var open = false

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Button { open.toggle() } label: {
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Image(systemName: "info.circle").font(.system(size: 11)).foregroundStyle(Theme.muted).frame(width: 14)
                    Text(event.title).font(.system(size: 13)).foregroundStyle(Theme.muted).lineLimit(1)
                    Spacer(minLength: 0)
                }
                .padding(.vertical, 4).contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            if open {
                Text(event.text.count > 4000 ? String(event.text.prefix(4000)) + "…" : event.text)
                    .font(.system(size: 12, design: .monospaced)).foregroundStyle(Theme.secondary)
                    .textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
                    .padding(10).frame(maxWidth: .infinity, alignment: .leading)
                    .background(Theme.field, in: RoundedRectangle(cornerRadius: 8))
                    .padding(.leading, 22)
            }
        }
    }
}

/// La orden tal cual, pequeña y en monoespaciada, debajo de su descripción.
private struct CommandText: View {
    let command: String

    var body: some View {
        Text("$ " + command).font(.system(size: 11.5, design: .monospaced)).foregroundStyle(Theme.muted)
            .lineLimit(1).truncationMode(.middle)
    }
}
