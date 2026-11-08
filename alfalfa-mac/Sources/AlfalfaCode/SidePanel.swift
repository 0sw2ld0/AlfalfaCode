import AlfalfaKit
import SwiftUI

/// Panel derecho: Contexto, Entorno y Cambios, cada uno plegable como en Claude Code.
/// Lo plegado se recuerda entre sesiones.
struct SidePanel: View {
    let folder: String?
    let snapshot: GitSnapshot?
    let ollama: Bool?
    let timeline: Timeline?
    var canCompact = false
    var onCompact: (() -> Void)? = nil
    var onStopProcess: ((String) -> Void)? = nil

    @AppStorage("panel.contexto.abierto") private var contextOpen = true
    @AppStorage("panel.entorno.abierto") private var environmentOpen = true
    @AppStorage("panel.cambios.abierto") private var changesOpen = true
    @AppStorage("panel.contexto.mcp.abierto") private var mcpListOpen = false
    @AppStorage("panel.contexto.memoria.abierto") private var memoryListOpen = false
    @AppStorage("panel.procesos.abierto") private var processesOpen = true
    @AppStorage("panel.subagentes.abierto") private var subagentsOpen = true

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 0) {
                if let timeline {
                    PanelSection(title: "Contexto", open: $contextOpen) { context(timeline) }
                    separator
                    if !timeline.subagents.isEmpty {
                        PanelSection(title: "Subagentes", open: $subagentsOpen, trailing: { subagentCount(timeline) }) {
                            subagents(timeline)
                        }
                        separator
                    }
                    if !timeline.processes.isEmpty {
                        PanelSection(title: "Procesos", open: $processesOpen, trailing: { processCount(timeline) }) {
                            processes(timeline)
                        }
                        separator
                    }
                }
                PanelSection(title: "Entorno", open: $environmentOpen) { environment }
                separator
                PanelSection(title: "Cambios", open: $changesOpen, trailing: { changeTotals }) { changes }
            }
            .padding(.horizontal, 18).padding(.vertical, 14)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .background(Theme.background)
        .accessibilityLabel("Detalles")
    }

    private var separator: some View {
        Divider().overlay(Theme.border).padding(.vertical, 12)
    }

    // MARK: Contexto

    /// Como el panel de Claude Code: % usado, la barra por partes, cada parte con sus tokens,
    /// lo que no ocupa contexto (herramientas diferidas), las listas plegables y, al pie,
    /// cuánto falta para la compactación automática con el botón «Compactar sesión».
    private func context(_ timeline: Timeline) -> some View {
        let tools = timeline.items.reduce(0) { count, item in
            if case let .events(_, events) = item { return count + events.filter { $0.text.hasPrefix("Inicio ") }.count }
            return count
        }
        let messages = timeline.items.filter { if case .user = $0 { return true } else if case .agent = $0 { return true } else { return false } }.count
        let parts = ContextParts(timeline: timeline)
        let limit = timeline.contextLimit
        return VStack(alignment: .leading, spacing: 12) {
            if limit > 0 {
                HStack(alignment: .firstTextBaseline) {
                    Text("\(Int((timeline.contextFraction * 100).rounded()))% usado").font(.system(size: 15, weight: .medium))
                    Spacer()
                    Text("\(compact(timeline.contextUsed)) / \(compact(limit))")
                        .font(.system(size: 13)).foregroundStyle(Theme.muted)
                }
                ContextBar(parts: parts.bar, limit: limit).frame(height: 6)
                VStack(alignment: .leading, spacing: 7) {
                    ForEach(parts.rows) { row in
                        partRow(color: row.color, name: row.name, tokens: row.tokens, limit: limit, help: row.help)
                    }
                    partRow(color: Theme.border, name: "Espacio libre", tokens: parts.free, limit: limit,
                            help: "Lo que aún cabe antes de la reserva de compactación.")
                }
                if !parts.deferred.isEmpty {
                    VStack(alignment: .leading, spacing: 7) {
                        ForEach(parts.deferred) { row in
                            partRow(color: Theme.border.opacity(0.6), name: row.name, tokens: row.tokens, limit: 0,
                                    help: row.help)
                        }
                    }
                }
                lists(timeline)
                if parts.estimated {
                    Text("Reparto estimado por tamaño; el total lo mide Ollama.")
                        .font(.system(size: 11.5)).foregroundStyle(Theme.muted)
                }
            } else {
                Text("Aún sin medir: se mide en la primera respuesta del modelo.")
                    .font(.system(size: 13)).foregroundStyle(Theme.muted).fixedSize(horizontal: false, vertical: true)
            }
            compactFooter(timeline)
            VStack(alignment: .leading, spacing: 4) {
                stat("Mensajes", "\(messages)")
                stat("Acciones", "\(tools)")
                stat("Tokens gastados en la sesión", compact(timeline.totalTokens))
            }
            .padding(.top, 2)
        }
    }

    /// «Herramientas MCP 3» y «Archivos de memoria 1», plegables como en Claude Code.
    @ViewBuilder private func lists(_ timeline: Timeline) -> some View {
        let mcpTools = timeline.mcpContext?.tools ?? []
        let memory = timeline.contextDetails?.memoryFiles ?? []
        if !mcpTools.isEmpty || !memory.isEmpty {
            VStack(alignment: .leading, spacing: 6) {
                if !mcpTools.isEmpty {
                    ContextList(title: "Herramientas MCP", entries: mcpTools, open: $mcpListOpen) { $0 }
                }
                if !memory.isEmpty {
                    ContextList(title: "Archivos de memoria", entries: memory, open: $memoryListOpen) {
                        Presentation.abbreviate($0)
                    }
                }
            }
        }
    }

    @ViewBuilder private func compactFooter(_ timeline: Timeline) -> some View {
        if let onCompact {
            HStack(spacing: 10) {
                Group {
                    if timeline.compacting {
                        HStack(spacing: 6) { ProgressView().controlSize(.mini); Text("Compactando…") }
                    } else if let left = timeline.tokensUntilCompaction {
                        Text(left == 0 ? "Se compactará en la siguiente respuesta" : compact(left) + " hasta la compactación")
                    } else {
                        Text("Resume la conversación para liberar contexto")
                    }
                }
                .font(.system(size: 12.5)).foregroundStyle(Theme.muted).lineLimit(2)
                Spacer(minLength: 4)
                Button(action: onCompact) {
                    Text("Compactar sesión").font(.system(size: 12.5, weight: .medium))
                        .padding(.horizontal, 10).frame(height: 28)
                        .background(Theme.selection, in: RoundedRectangle(cornerRadius: 7))
                        .foregroundStyle(canCompact ? Theme.text : Theme.muted)
                }
                .buttonStyle(.plain).disabled(!canCompact)
                .help("Resume la conversación para el modelo y sigue desde ese resumen; el historial completo se conserva. "
                      + "Con /compactar y un texto detrás eliges qué conservar.")
            }
        }
    }

    private func partRow(color: Color, name: String, tokens: Int, limit: Int, help: String? = nil) -> some View {
        HStack(spacing: 10) {
            RoundedRectangle(cornerRadius: 3).fill(color).frame(width: 11, height: 11)
            Text(name).foregroundStyle(Theme.secondary).lineLimit(1)
            Spacer()
            Text(compact(tokens)).foregroundStyle(Theme.secondary).monospacedDigit()
            Text(limit > 0 ? percent(tokens, of: limit) : "—").foregroundStyle(Theme.muted).monospacedDigit()
                .frame(width: 48, alignment: .trailing)
        }
        .font(.system(size: 13))
        .help(help ?? "")
        .accessibilityElement(children: .combine)
    }

    private func percent(_ value: Int, of total: Int) -> String {
        guard total > 0 else { return "—" }
        return String(format: "%.1f%%", Double(value) * 100 / Double(total))
    }

    private func stat(_ name: String, _ value: String) -> some View {
        HStack { Text(name); Spacer(); Text(value).monospacedDigit() }.font(.system(size: 12.5)).foregroundStyle(Theme.secondary)
    }

    private func compact(_ value: Int) -> String { ContextFormat.tokens(value) }

    // MARK: Subagentes

    @ViewBuilder private func subagentCount(_ timeline: Timeline) -> some View {
        let working = timeline.workingSubagents.count
        if working > 0 {
            Text("\(working) trabajando").font(.system(size: 12, weight: .semibold)).foregroundStyle(Theme.alfalfa)
        }
    }

    /// Cada subagente con su tipo, modelo y estado; mientras trabaja, su paso actual, y al
    /// terminar, el principio de su informe. Sus pasos se despliegan debajo.
    private func subagents(_ timeline: Timeline) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            ForEach(timeline.subagents.reversed()) { item in
                VStack(alignment: .leading, spacing: 4) {
                    HStack(alignment: .firstTextBaseline, spacing: 8) {
                        Image(systemName: "circle.fill").font(.system(size: 7))
                            .foregroundStyle(item.working ? Theme.alfalfa
                                             : item.state == "terminado" ? Theme.muted : Theme.danger)
                        Text(item.name).foregroundStyle(Theme.text).lineLimit(1).truncationMode(.tail).help(item.name)
                    }
                    .font(.system(size: 12.5, weight: .medium))
                    TimelineView(.periodic(from: .now, by: 1)) { context in
                        Text(Self.subagentText(item, now: context.date.timeIntervalSince1970))
                    }
                    .font(.system(size: 12)).foregroundStyle(Theme.muted)
                    let detail = item.working ? item.activity : (item.report.isEmpty ? item.error : item.report)
                    if !detail.isEmpty {
                        Text(detail).font(.system(size: 11.5)).foregroundStyle(Theme.secondary)
                            .lineLimit(item.working ? 2 : 4).textSelection(.enabled)
                    }
                    let steps = timeline.subagentSteps[item.id] ?? []
                    if !steps.isEmpty {
                        DisclosureGroup("Pasos (\(steps.count))") {
                            VStack(alignment: .leading, spacing: 3) {
                                ForEach(Array(steps.suffix(40).enumerated()), id: \.offset) { _, step in
                                    Text(Self.stepText(step)).font(.system(size: 11, design: .monospaced))
                                        .foregroundStyle(Theme.secondary).lineLimit(3).textSelection(.enabled)
                                }
                            }
                            .frame(maxWidth: .infinity, alignment: .leading)
                        }
                        .font(.system(size: 11.5)).foregroundStyle(Theme.muted)
                    }
                }
            }
        }
    }

    static func subagentText(_ item: Subagent, now: Double) -> String {
        var parts = [item.id, item.type]
        if !item.model.isEmpty { parts.append(item.model.replacingOccurrences(of: ":cloud", with: "")) }
        var state = item.state
        if item.since > 0 { state += " · " + ToolEvent.durationText((item.until ?? now) - item.since) }
        parts.append(state)
        if item.steps > 0 { parts.append("\(item.steps) \(item.steps == 1 ? "acción" : "acciones")") }
        return parts.joined(separator: " · ")
    }

    static func stepText(_ step: AgentEvent) -> String {
        let first = step.text.split(separator: "\n", maxSplits: 1, omittingEmptySubsequences: true).first.map(String.init) ?? ""
        switch step.role {
        case "agent", "assistant": return "› " + first
        case "tool": return first.hasPrefix("Inicio ") ? "· " + String(first.dropFirst(7)) : "  " + first
        default: return "  " + first
        }
    }

    // MARK: Procesos

    @ViewBuilder private func processCount(_ timeline: Timeline) -> some View {
        let live = timeline.liveProcesses.count
        if live > 0 {
            Text("\(live) abierto\(live == 1 ? "" : "s")").font(.system(size: 12, weight: .semibold))
                .foregroundStyle(Theme.alfalfa)
        }
    }

    /// Como /ps de Codex: cada orden en segundo plano, cuánto lleva, sus últimas líneas y Detener.
    private func processes(_ timeline: Timeline) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            ForEach(timeline.processes.reversed()) { item in
                VStack(alignment: .leading, spacing: 4) {
                    HStack(alignment: .firstTextBaseline, spacing: 8) {
                        Image(systemName: "circle.fill").font(.system(size: 7))
                            .foregroundStyle(item.alive ? Theme.alfalfa : Theme.muted)
                        Text(item.command).font(Theme.mono).foregroundStyle(Theme.text).lineLimit(1).truncationMode(.middle)
                            .help(item.command)
                        Spacer(minLength: 4)
                        if item.alive, let onStopProcess {
                            Button("Detener") { onStopProcess(item.id) }
                                .buttonStyle(.plain).font(.system(size: 12, weight: .medium)).foregroundStyle(Theme.danger)
                        }
                    }
                    processState(item).font(.system(size: 12)).foregroundStyle(Theme.muted)
                    if !item.lastLines.isEmpty {
                        Text(item.lastLines.joined(separator: "\n")).font(.system(size: 11, design: .monospaced))
                            .foregroundStyle(Theme.secondary).lineLimit(3).textSelection(.enabled)
                    }
                }
            }
        }
    }

    private func processState(_ item: BackgroundProcess) -> some View {
        TimelineView(.periodic(from: .now, by: 1)) { context in
            Text(Self.processText(item, now: context.date.timeIntervalSince1970))
        }
    }

    static func processText(_ item: BackgroundProcess, now: Double) -> String {
        var state: String
        if item.alive {
            state = "abierto"
            if item.since > 0 { state += " · " + ToolEvent.durationText(now - item.since) }
        } else if item.stopped {
            state = "detenido"
        } else {
            state = "terminó con código " + (item.exitCode.map(String.init) ?? "?")
        }
        return item.id + " · " + state + (item.unsandboxed ? " · sin sandbox" : "")
    }

    // MARK: Entorno

    private var environment: some View {
        VStack(alignment: .leading, spacing: 8) {
            Label(folder.map(Presentation.abbreviate) ?? "Sin carpeta", systemImage: "folder")
                .lineLimit(2).truncationMode(.middle)
            Label {
                Text(branchText)
            } icon: {
                Image(systemName: "arrow.triangle.branch")
            }
            HStack(spacing: 6) {
                Image(systemName: "circle.fill").font(.system(size: 7))
                    .foregroundStyle(ollama == true ? Theme.alfalfa : (ollama == false ? Theme.danger : Theme.muted))
                Text("Ollama")
                Text(ollama == true ? "conectado" : (ollama == false ? "sin conexión" : "comprobando"))
                    .foregroundStyle(Theme.muted)
            }
        }
        .font(.system(size: 13)).foregroundStyle(Theme.secondary)
    }

    private var branchText: String {
        guard let snapshot, let branch = snapshot.branch else { return "sin repositorio git" }
        let count = snapshot.files.count
        return "\(branch) · " + (count == 0 ? "sin cambios" : "\(count) archivo\(count == 1 ? "" : "s") modificado\(count == 1 ? "" : "s")")
    }

    // MARK: Cambios

    @ViewBuilder private var changeTotals: some View {
        if let snapshot, !snapshot.files.isEmpty {
            HStack(spacing: 6) {
                Text("+\(snapshot.added)").foregroundStyle(Theme.alfalfa)
                Text("−\(snapshot.removed)").foregroundStyle(Theme.danger)
            }
            .font(.system(size: 12, weight: .semibold)).monospacedDigit()
        }
    }

    @ViewBuilder private var changes: some View {
        if let snapshot, !snapshot.files.isEmpty {
            VStack(alignment: .leading, spacing: 6) {
                ForEach(snapshot.files) { file in
                    FileDiff(file: file)
                }
                Text("Sin commit: tú decides en git.").font(.system(size: 12)).foregroundStyle(Theme.muted)
                    .padding(.top, 4)
            }
        } else {
            Text(snapshot == nil ? "Sin datos" : "Sin cambios en el árbol de trabajo")
                .font(.system(size: 13)).foregroundStyle(Theme.muted)
        }
    }
}

/// Sección del panel con título y flecha; un clic la pliega o la abre.
struct PanelSection<Content: View, Trailing: View>: View {
    let title: String
    @Binding var open: Bool
    var trailing: () -> Trailing
    var content: () -> Content

    init(title: String, open: Binding<Bool>, @ViewBuilder trailing: @escaping () -> Trailing = { EmptyView() },
         @ViewBuilder content: @escaping () -> Content) {
        self.title = title
        self._open = open
        self.trailing = trailing
        self.content = content
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Button { withAnimation(.easeOut(duration: 0.15)) { open.toggle() } } label: {
                HStack(spacing: 6) {
                    Text(title).font(.system(size: 14, weight: .semibold)).foregroundStyle(Theme.text)
                    Image(systemName: "chevron.down").font(.system(size: 10, weight: .bold))
                        .foregroundStyle(Theme.muted).rotationEffect(.degrees(open ? 0 : -90))
                    Spacer()
                    trailing()
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityLabel(title).accessibilityValue(open ? "abierta" : "plegada")
            if open { content() }
        }
    }
}

/// Cada parte del contexto, con su color en la barra y en la lista, como en Claude Code:
/// primero lo que se mide, luego lo añadido desde la última medida y la reserva que la
/// compactación automática nunca deja llenar.
struct ContextParts {
    struct Row: Identifiable {
        let name: String
        let tokens: Int
        let color: Color
        var help: String? = nil
        var id: String { name }
    }

    let rows: [Row]
    /// Lo que no ocupa contexto: el catálogo MCP diferido.
    let deferred: [Row]
    let free: Int
    let estimated: Bool

    /// Segmentos de la barra: todas las filas con tokens.
    var bar: [Row] { rows }

    init(timeline: Timeline) {
        var rows: [Row]
        if let parts = timeline.contextBreakdown {
            rows = [
                Row(name: "Mensajes", tokens: parts.messages, color: Color(hex: 0x5B8DEF)),
                Row(name: "Herramientas MCP", tokens: parts.mcp, color: Color(hex: 0xE07A3F),
                    help: "Esquemas MCP cargados y buscar_herramientas."),
                Row(name: "Herramientas", tokens: parts.tools, color: Color(hex: 0x3FB39A),
                    help: "Las herramientas propias de AlfalfaCode."),
                Row(name: "Instrucciones", tokens: parts.system, color: Color(hex: 0xD9A53B),
                    help: "Las instrucciones del arnés y del modo."),
                Row(name: "Complementos", tokens: parts.plugins, color: Color(hex: 0xB07CE0),
                    help: "Habilidades y órdenes de los complementos autorizados."),
                Row(name: "Instrucciones MCP", tokens: parts.mcpInstructions, color: Color(hex: 0xC98A6B),
                    help: "El resumen de una línea de cada servidor MCP."),
                Row(name: "Archivos de memoria", tokens: parts.memory, color: Color(hex: 0x8FA3B8),
                    help: "AGENTS.md del usuario y del proyecto."),
            ].filter { $0.tokens > 0 }
            estimated = true
        } else {
            rows = [Row(name: "Usado", tokens: timeline.contextTokens, color: Theme.alfalfa)]
            estimated = false
        }
        if timeline.contextSinceMeasure > 0 {
            rows.append(Row(name: "Desde la última medida", tokens: timeline.contextSinceMeasure, color: Color(hex: 0x7A8694),
                            help: "Lo añadido después de la última respuesta medida: la respuesta final y lo que devolvieron las herramientas."))
        }
        let reserve = timeline.compactReserve
        if reserve > 0 {
            rows.append(Row(name: "Reserva de compactación", tokens: reserve, color: Color(hex: 0x4A525C),
                            help: "La compactación automática salta antes de llegar aquí para que quepa el resumen."))
        }
        self.rows = rows
        free = max(0, timeline.contextLimit - timeline.contextUsed - reserve)
        if let mcp = timeline.mcpContext, mcp.deferredCount > 0 {
            deferred = [Row(name: "Herramientas MCP (diferidas)", tokens: mcp.deferredTokens, color: Theme.border,
                            help: "\(mcp.deferredCount) herramientas que no ocupan contexto hasta que el modelo las carga con buscar_herramientas.")]
        } else {
            deferred = []
        }
    }
}

/// Lista plegable del panel con su número a la derecha («Herramientas MCP  3 ›»).
struct ContextList: View {
    let title: String
    let entries: [ContextEntry]
    @Binding var open: Bool
    let label: (String) -> String

    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            Button { withAnimation(.easeOut(duration: 0.15)) { open.toggle() } } label: {
                HStack(spacing: 8) {
                    Image(systemName: "chevron.right").font(.system(size: 9, weight: .bold)).foregroundStyle(Theme.muted)
                        .rotationEffect(.degrees(open ? 90 : 0))
                    Text(title).foregroundStyle(Theme.secondary)
                    Spacer()
                    Text("\(entries.count)").foregroundStyle(Theme.muted).monospacedDigit()
                }
                .font(.system(size: 13)).contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityLabel(title).accessibilityValue("\(entries.count), " + (open ? "abierta" : "plegada"))
            if open {
                VStack(alignment: .leading, spacing: 4) {
                    ForEach(entries) { entry in
                        HStack(spacing: 8) {
                            Text(label(entry.name)).lineLimit(1).truncationMode(.middle)
                            Spacer(minLength: 6)
                            Text(ContextFormat.tokens(entry.tokens)).monospacedDigit()
                        }
                        .font(.system(size: 12)).foregroundStyle(Theme.muted)
                        .help(entry.name)
                    }
                }
                .padding(.leading, 17)
            }
        }
    }
}

/// Barra del contexto por partes, sobre el total que admite el modelo.
struct ContextBar: View {
    let parts: [ContextParts.Row]
    let limit: Int

    var body: some View {
        GeometryReader { proxy in
            HStack(spacing: 1.5) {
                ForEach(parts) { part in
                    Rectangle().fill(part.color)
                        .frame(width: max(2, proxy.size.width * CGFloat(part.tokens) / CGFloat(max(limit, 1))))
                }
                Spacer(minLength: 0)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Theme.border)
            .clipShape(Capsule())
        }
        .accessibilityHidden(true)
    }
}

/// Diferencias de un archivo; se pliega pulsando su nombre.
struct FileDiff: View {
    let file: DiffFile
    @State private var open = true

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Button { open.toggle() } label: {
                HStack(spacing: 5) {
                    Image(systemName: "chevron.down").font(.system(size: 9, weight: .bold)).foregroundStyle(Theme.muted)
                        .rotationEffect(.degrees(open ? 0 : -90))
                    Text(file.path).font(Theme.mono).foregroundStyle(Theme.text).lineLimit(1).truncationMode(.middle)
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            if open {
                VStack(alignment: .leading, spacing: 0) {
                    ForEach(file.lines) { line in
                        Text(line.text.isEmpty ? " " : line.text)
                            .font(.system(size: 11.5, design: .monospaced))
                            .foregroundStyle(color(line.kind))
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .background(background(line.kind))
                            .lineLimit(1).truncationMode(.tail)
                    }
                }
                .textSelection(.enabled)
            }
        }
    }

    private func color(_ kind: DiffLine.Kind) -> Color {
        switch kind {
        case .added: return Theme.alfalfaLight
        case .removed: return Theme.danger
        case .meta: return Theme.muted
        case .context: return Theme.secondary
        }
    }

    private func background(_ kind: DiffLine.Kind) -> Color {
        switch kind {
        case .added: return Theme.alfalfa.opacity(0.12)
        case .removed: return Theme.danger.opacity(0.12)
        default: return .clear
        }
    }
}
