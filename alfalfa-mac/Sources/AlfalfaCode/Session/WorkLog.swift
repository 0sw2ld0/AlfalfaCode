import AlfalfaKit
import SwiftUI

/// Las llamadas seguidas del agente en una fila plegada, como en la app de Claude:
/// «Ejecutó 3 comandos, leyó 2 archivos · 1min 7s ›». Al abrirla, una fila por llamada con su
/// descripción y lo que tardó; al abrir una fila, el comando con su `$` y la salida.
///
/// Mientras trabaja, la fila es la viva: la hoja de alfalfa animada, lo que hace ahora en
/// gerundio («Leyendo WorkLog.swift», «Pensando» entre llamadas) y el tiempo que lleva. La
/// conversación no salta con cada salida larga: las llamadas se ven al abrirla.
struct WorkLog: View, Equatable {

    static func == (lhs: WorkLog, rhs: WorkLog) -> Bool {
        lhs.events == rhs.events && lhs.running == rhs.running && lhs.waiting == rhs.waiting
            && lhs.preparing == rhs.preparing && lhs.expanded == rhs.expanded
    }

    // MARK: - Propiedades

    let events: [ToolEvent]
    let running: Bool
    /// Una llamada del grupo espera el permiso de la persona.
    var waiting = false
    /// «Preparando deshacer…» mientras el motor guarda la instantánea del turno.
    var preparing: String?
    @Binding var expanded: Bool

    var body: some View {
        let entries = ToolCalls.entries(from: events)
        let calls = entries.compactMap { if case let .call(call) = $0 { return call } else { return nil } }
        VStack(alignment: .leading, spacing: 0) {
            if running {
                LiveActivityRow(activity: LiveActivity.of(group: events, waiting: waiting, preparing: preparing), expanded: $expanded)
            } else {
                summaryRow(title: title(calls: calls, entries: entries), onlyNotes: calls.isEmpty)
            }
            if expanded {
                VStack(alignment: .leading, spacing: 2) {
                    ForEach(entries) { entry in
                        switch entry {
                        case let .call(call): ToolCallRow(call: call, live: running && !call.finished).equatable()
                        case let .note(event): NoteRow(event: event)
                        }
                    }
                }
                .padding(.leading, 24).padding(.top, 2)
            }
        }
    }

    // MARK: - Métodos privados

    /// La fila de un grupo terminado: la hoja quieta, el resumen y lo que duró. Un grupo con
    /// sólo avisos del sistema no es trabajo del agente: lleva el icono de aviso.
    private func summaryRow(title: String, onlyNotes: Bool) -> some View {
        let total = ToolEvent.duration(of: events)
        return Button { withAnimation(.easeOut(duration: 0.15)) { expanded.toggle() } } label: {
            HStack(spacing: 8) {
                if onlyNotes {
                    Image(systemName: "info.circle").font(.system(size: 12)).foregroundStyle(Theme.muted)
                        .frame(width: 16, height: 16)
                } else {
                    AlfalfaActivityIcon(phase: .done, size: 16)
                }
                Text(title).font(.system(size: 14)).foregroundStyle(Theme.muted).lineLimit(1)
                if let total {
                    Text(ElapsedTime.text(total)).font(.system(size: 13)).monospacedDigit()
                        .foregroundStyle(Theme.muted).fixedSize()
                }
                Image(systemName: "chevron.right").font(.system(size: 10, weight: .semibold))
                    .foregroundStyle(Theme.muted).rotationEffect(.degrees(expanded ? 90 : 0))
            }
            .padding(.vertical, 6).contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel(title + (total.map { ", " + ElapsedTime.spoken($0) } ?? ""))
        .accessibilityHint(expanded ? "Plegar las llamadas" : "Ver cada llamada")
    }

    private func title(calls: [ToolCall], entries: [WorkEntry]) -> String {
        if !calls.isEmpty { return ToolCalls.summary(calls) }
        // Sólo avisos del sistema (un proceso en segundo plano terminó, un reintento).
        if case let .note(event)? = entries.first {
            return entries.count == 1 ? event.title : event.title + " · y \(entries.count - 1) más"
        }
        return "Sin acciones"
    }
}

/// Una llamada: su descripción y, al pulsarla, el comando y la salida.
struct ToolCallRow: View, Equatable {

    static func == (lhs: ToolCallRow, rhs: ToolCallRow) -> Bool { lhs.call == rhs.call && lhs.live == rhs.live }

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
                            duration
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
            .accessibilityLabel(spokenLabel)
            if open { detail.padding(.leading, 22).padding(.bottom, 6) }
        }
    }

    // MARK: - Propiedades privadas

    @State private var open = false

    /// Descripción, orden, cómo acabó y lo que tardó, para VoiceOver.
    private var spokenLabel: String {
        var parts = [call.description]
        if let command = call.command { parts.append("orden " + command) }
        if call.interrupted { parts.append("interrumpida") } else if call.failed { parts.append("falló") }
        if let seconds = call.duration, seconds >= 1 { parts.append(ElapsedTime.spoken(seconds)) }
        return parts.joined(separator: ", ")
    }

    /// Lo que tardó o, en curso, lo que lleva («5s»), con las horas de los eventos.
    @ViewBuilder private var duration: some View {
        if live, let start = call.startTime {
            LiveDuration(since: start)
        } else if let seconds = call.duration, seconds >= 1 {
            DurationText(seconds: seconds)
        }
    }

    /// En curso, interrumpida, fallida o con un código que el motor dio por bueno (en gris).
    @ViewBuilder private var badge: some View {
        if live {
            EmptyView()
        } else if call.interrupted {
            Text("interrumpida").font(.system(size: 11.5)).foregroundStyle(Theme.warning)
        } else if call.failed {
            Text(call.exitCode.map { "código \($0)" } ?? "falló").font(.system(size: 11.5)).foregroundStyle(Theme.danger)
        } else if let code = call.exitCode, code != 0 {
            Text("código \(code)").font(.system(size: 11.5)).foregroundStyle(Theme.muted)
                .help("El código no es 0, pero el resultado es el esperado")
        } else if let diagnostics = call.diagnostics, let label = diagnostics.label {
            // Lo vio el servidor de lenguaje al editar: el agente lo tiene en el resultado.
            Text(label).font(.system(size: 11.5))
                .foregroundStyle(diagnostics.errors > 0 ? Theme.warning : Theme.muted)
                .help(diagnostics.incomplete
                      ? "Algún servidor de lenguaje no contestó a tiempo: puede haber más. No es una comprobación."
                      : "Diagnósticos nuevos tras la edición. Orientan, pero no son una comprobación.")
        }
    }

    /// Siempre acotado (TextPreview): un parche de miles de líneas colgaba la app al medirse.
    /// Lo entero se ve con «Ver todo», en un NSTextView.
    @ViewBuilder private var detail: some View {
        VStack(alignment: .leading, spacing: 0) {
            if let edit = EditText.of(call) {
                let preview = TextPreview(edit)
                DiffBlock(diff: preview.shown, maxHeight: 260)
                    .padding(6)
                if preview.truncated { seeAll(preview, title: call.description, text: edit) }
            } else {
                let preview = TextPreview(header, maxLines: 30)
                Text(preview.shown).font(Theme.mono).foregroundStyle(Theme.text).textSelection(.enabled)
                    .lineLimit(preview.shownLines)
                    .padding(10).frame(maxWidth: .infinity, alignment: .leading)
                if preview.truncated { seeAll(preview, title: call.description, text: header) }
            }
            if let output = call.output, !output.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                Divider().overlay(Theme.border)
                let preview = TextPreview(output)
                ScrollView {
                    Text(preview.shown).font(.system(size: 12, design: .monospaced)).foregroundStyle(Theme.secondary)
                        .textSelection(.enabled)
                        .padding(10).frame(maxWidth: .infinity, alignment: .leading)
                }
                .frame(maxHeight: 220)
                if preview.truncated { seeAll(preview, title: "Salida · " + call.description, text: output) }
            } else if !call.finished {
                Divider().overlay(Theme.border)
                Text("Sin salida todavía").font(.system(size: 12)).foregroundStyle(Theme.muted).padding(10)
            }
        }
        .background(Theme.field, in: RoundedRectangle(cornerRadius: 8))
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(Theme.border))
    }

    @State private var fullText: FullText?

    private struct FullText: Identifiable {
        let id = UUID()
        let title: String
        let text: String
    }

    private func seeAll(_ preview: TextPreview, title: String, text: String) -> some View {
        Button {
            fullText = FullText(title: title, text: text)
        } label: {
            Text(preview.hiddenLines > 0 ? "Ver todo (\(preview.totalLines) líneas)" : "Ver todo")
                .font(.system(size: 12, weight: .medium)).foregroundStyle(Theme.link)
                .padding(.horizontal, 10).padding(.vertical, 6)
        }
        .buttonStyle(.plain)
        .sheet(item: $fullText) { item in LargeTextSheet(title: item.title, text: item.text) }
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
                Text(TextPreview(event.text).shown)
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

/// Lo que tardó una llamada, pequeño y en gris («5s»).
private struct DurationText: View {
    let seconds: Double

    var body: some View {
        Text(ElapsedTime.text(seconds)).font(.system(size: 11.5)).monospacedDigit().foregroundStyle(Theme.muted)
            .fixedSize()
    }
}

/// Lo que lleva la llamada en curso; se para cuando la ventana no se ve.
private struct LiveDuration: View {
    let since: Double
    @State private var visible = true

    var body: some View {
        SecondTicker(since: since, active: visible) { now in
            DurationText(seconds: max(0, now.timeIntervalSince1970 - since))
        }
        .background(WindowVisibilityReader(visible: $visible))
    }
}
