import AlfalfaKit
import SwiftUI

/// Un fallo al preguntar a `alfalfa` qué se deshace.
struct UndoFailure: Error {
    let text: String
    init(_ text: String) { self.text = text }
}

/// Qué abre la hoja: volver a un mensaje (con sus tres opciones) o rehacer un deshacer.
enum UndoRequest {
    case goBack(turn: String, previous: String?, draft: String)
    case redo(step: String)
}

/// «Volver a este mensaje»: conversación y código, sólo conversación o sólo código. Antes de
/// tocar nada enseña qué archivos vuelven, cuáles cambiaste tú después (y se quedan salvo que
/// marques cada uno) y lo que el turno hizo fuera y no se puede deshacer.
struct UndoSheet: View {

    // MARK: - Propiedades

    let request: UndoRequest
    @ObservedObject var session: LiveSession
    let close: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text(isRedo ? "Rehacer el código" : "Volver a este mensaje").font(.system(size: 15, weight: .semibold))
            if case let .goBack(_, _, draft) = request, !draft.isEmpty {
                Text(draft).font(.system(size: 12.5)).foregroundStyle(Theme.muted).lineLimit(2)
            }
            if !isRedo { scopes }
            if scope.touchesCode || isRedo { files }
            Spacer(minLength: 0)
            footer
        }
        .padding(20).frame(width: 520).frame(minHeight: 300, maxHeight: 640)
        .task { await load() }
    }

    // MARK: - Propiedades privadas

    @EnvironmentObject private var app: AppModel
    @State private var scope: UndoScope = .all
    @State private var preview: UndoPreview?
    @State private var failure: String?
    @State private var force: Set<String> = []
    @State private var busy = false

    private var isRedo: Bool { if case .redo = request { return true } else { return false } }

    /// Sin instantánea no se ofrece tocar el código.
    private var codeAvailable: Bool { preview?.available ?? false }

    private var scopes: some View {
        VStack(alignment: .leading, spacing: 8) {
            ForEach(UndoScope.allCases) { option in
                let enabled = !option.touchesCode || codeAvailable || preview == nil
                Button { scope = option } label: {
                    HStack(alignment: .top, spacing: 10) {
                        Image(systemName: scope == option ? "largecircle.fill.circle" : "circle")
                            .foregroundStyle(scope == option ? Theme.alfalfa : Theme.muted).font(.system(size: 13))
                        VStack(alignment: .leading, spacing: 2) {
                            Text(option.title).font(.system(size: 13, weight: .medium))
                            Text(detail(option)).font(.system(size: 12)).foregroundStyle(Theme.muted)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                        Spacer(minLength: 0)
                    }
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain).disabled(!enabled).opacity(enabled ? 1 : 0.45)
                .accessibilityAddTraits(scope == option ? .isSelected : [])
            }
        }
    }

    private func detail(_ option: UndoScope) -> String {
        if option == .all, preview?.firstTurn == true {
            return "Es el primer mensaje: los archivos vuelven a como estaban y empiezas una sesión nueva con él."
        }
        return option.detail
    }

    @ViewBuilder private var files: some View {
        if let failure {
            Label(failure, systemImage: "exclamationmark.triangle").font(.system(size: 12.5))
                .foregroundStyle(Theme.warning).fixedSize(horizontal: false, vertical: true)
        } else if let preview {
            if !preview.available {
                Label(preview.reason ?? "No se puede deshacer el código de ese mensaje.",
                      systemImage: "exclamationmark.triangle")
                    .font(.system(size: 12.5)).foregroundStyle(Theme.warning)
                    .fixedSize(horizontal: false, vertical: true)
            } else {
                ScrollView {
                    VStack(alignment: .leading, spacing: 14) {
                        restorable(preview)
                        conflicts(preview)
                        if !preview.large.isEmpty {
                            note("Sin instantánea, por demasiado grandes: " + preview.large.joined(separator: ", "))
                        }
                        warnings(preview.warnings)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
                .frame(maxHeight: 360)
            }
        } else {
            HStack(spacing: 8) {
                ProgressView().controlSize(.small)
                Text("Mirando qué cambió desde ese mensaje…").font(.system(size: 12.5)).foregroundStyle(Theme.muted)
            }
        }
    }

    @ViewBuilder private func restorable(_ preview: UndoPreview) -> some View {
        if preview.nothingToDo {
            note(isRedo ? "No hay nada que devolver: los archivos ya están como antes de deshacer."
                 : "El código ya está como antes de ese mensaje: no hay archivos que cambiar.")
        } else if !preview.restorable.isEmpty {
            VStack(alignment: .leading, spacing: 4) {
                let count = preview.restorable.count
                Text(count == 1 ? "Vuelve 1 archivo" : "Vuelven \(count) archivos")
                    .font(.system(size: 12.5, weight: .medium))
                ForEach(preview.restorable, id: \.self) { file in
                    fileRow(file.path, file.actionLabel)
                }
            }
        }
    }

    @ViewBuilder private func conflicts(_ preview: UndoPreview) -> some View {
        if !preview.conflicts.isEmpty {
            VStack(alignment: .leading, spacing: 6) {
                Text("Se quedan como están salvo que los marques").font(.system(size: 12.5, weight: .medium))
                ForEach(preview.conflicts, id: \.self) { file in
                    Toggle(isOn: binding(file.path)) {
                        VStack(alignment: .leading, spacing: 1) {
                            Text(file.path).font(Theme.mono).lineLimit(1).truncationMode(.middle)
                            Text(file.reasonLabel + " · si lo marcas, " + file.actionLabel)
                                .font(.system(size: 11.5)).foregroundStyle(Theme.warning)
                        }
                    }
                    .toggleStyle(.checkbox).help("Restaurarlo igualmente: perderías lo que cambió después")
                }
            }
        }
    }

    @ViewBuilder private func warnings(_ rows: [UndoWarning]) -> some View {
        if !rows.isEmpty {
            VStack(alignment: .leading, spacing: 6) {
                Label("Esto no se deshace", systemImage: "exclamationmark.triangle")
                    .font(.system(size: 12.5, weight: .medium)).foregroundStyle(Theme.warning)
                ForEach(rows, id: \.kind) { warning in
                    VStack(alignment: .leading, spacing: 2) {
                        Text(warning.title).font(.system(size: 12.5))
                        ForEach(warning.commands.prefix(3), id: \.self) { command in
                            Text(command).font(Theme.mono).foregroundStyle(Theme.muted).lineLimit(1)
                                .truncationMode(.tail)
                        }
                    }
                }
            }
            .padding(10).frame(maxWidth: .infinity, alignment: .leading)
            .background(Theme.card, in: RoundedRectangle(cornerRadius: 8))
            .overlay(RoundedRectangle(cornerRadius: 8).stroke(Theme.cardBorder))
        }
    }

    private var footer: some View {
        HStack {
            if scope.touchesCode || isRedo, codeAvailable {
                Text(isRedo ? "Lo que cambiaste después de deshacer se queda."
                     : "Podrás rehacer el código desde la conversación.")
                    .font(.system(size: 11.5)).foregroundStyle(Theme.muted)
            }
            Spacer()
            Button("Cancelar", action: close).keyboardShortcut(.cancelAction)
            Button(isRedo ? "Rehacer" : "Volver") { confirm() }
                .keyboardShortcut(.defaultAction).disabled(!canConfirm)
        }
    }

    private var canConfirm: Bool {
        guard !busy else { return false }
        if !isRedo && !scope.touchesCode { return true }
        guard let preview, preview.available else { return false }
        // Rehacer o «Sólo código» sin nada que hacer no tienen sentido; volver con la conversación, sí.
        return !(preview.nothingToDo && (isRedo || scope == .code))
    }

    private func fileRow(_ path: String, _ action: String) -> some View {
        HStack(spacing: 6) {
            Text(path).font(Theme.mono).lineLimit(1).truncationMode(.middle)
            Text(action).font(.system(size: 11.5)).foregroundStyle(Theme.muted)
        }
    }

    private func note(_ text: String) -> some View {
        Text(text).font(.system(size: 12.5)).foregroundStyle(Theme.secondary)
            .fixedSize(horizontal: false, vertical: true)
    }

    private func binding(_ path: String) -> Binding<Bool> {
        Binding(get: { force.contains(path) }, set: { on in if on { force.insert(path) } else { force.remove(path) } })
    }

    // MARK: - Métodos privados

    private func load() async {
        guard let id = session.sessionID else { return }
        let result: Result<UndoPreview, UndoFailure>
        switch request {
        case let .goBack(turn, _, _): result = await app.undoPreview(sessionID: id, turn: turn)
        case let .redo(step): result = await app.undoPreview(sessionID: id, turn: nil, redo: step)
        }
        switch result {
        case let .success(value):
            preview = value
            if !value.available && !isRedo { scope = .conversation }
        case let .failure(error):
            failure = error.text
            if !isRedo { scope = .conversation }
        }
    }

    private func confirm() {
        guard session.sessionID != nil else { return }
        busy = true
        let chosen = Array(force).sorted()
        switch request {
        case let .goBack(turn, previous, draft):
            app.goBack(session: session, turn: turn, previous: previous, draft: draft, scope: scope, force: chosen)
            close()
        case let .redo(step):
            Task {
                await app.redo(session: session, step: step, force: chosen)
                close()
            }
        }
    }
}

/// La marca de un deshacer o rehacer del código en la conversación, con «Rehacer» mientras se pueda.
struct UndoMarker: View {

    // MARK: - Propiedades

    let step: UndoStep
    let redone: Bool
    @ObservedObject var session: LiveSession

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 10) {
                line
                Button { withAnimation(.easeOut(duration: 0.15)) { open.toggle() } } label: {
                    HStack(spacing: 6) {
                        Image(systemName: step.isRedo ? "arrow.uturn.forward" : "arrow.uturn.backward")
                            .font(.system(size: 11))
                        Text(title).font(.system(size: 12.5, weight: .medium))
                        Image(systemName: "chevron.down").font(.system(size: 9, weight: .bold))
                            .rotationEffect(.degrees(open ? 0 : -90))
                    }
                    .foregroundStyle(Theme.secondary).fixedSize().contentShape(Rectangle())
                }
                .buttonStyle(.plain).help(open ? "Ocultar los archivos" : "Ver qué archivos cambiaron")
                if canRedo {
                    Button("Rehacer") { showingRedo = true }
                        .buttonStyle(.link).font(.system(size: 12.5))
                        .help("Devolver el código que quitó este deshacer")
                }
                line
            }
            if open { details }
        }
        .sheet(isPresented: $showingRedo) {
            UndoSheet(request: .redo(step: step.id), session: session) { showingRedo = false }
        }
    }

    // MARK: - Propiedades privadas

    @State private var open = false
    @State private var showingRedo = false

    private var canRedo: Bool {
        !step.isRedo && !redone && session.sessionID != nil && !session.timeline.status.isRunning
            && !step.restored.isEmpty
    }

    private var line: some View { Rectangle().fill(Theme.border).frame(height: 1) }

    private var title: String {
        let count = step.restored.count
        let files = count == 1 ? "1 archivo" : "\(count) archivos"
        if step.isRedo { return "Código rehecho · " + files }
        return (redone ? "Código deshecho y rehecho · " : "Código deshecho · ") + files
    }

    private var details: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(step.text).font(.system(size: 12.5)).foregroundStyle(Theme.secondary)
                .fixedSize(horizontal: false, vertical: true).textSelection(.enabled)
            ForEach(step.restored.prefix(30), id: \.self) { path in
                Text(path).font(Theme.mono).foregroundStyle(Theme.muted).lineLimit(1).truncationMode(.middle)
            }
            if step.restored.count > 30 {
                Text("y \(step.restored.count - 30) más").font(.system(size: 11.5)).foregroundStyle(Theme.muted)
            }
        }
        .padding(14).frame(maxWidth: .infinity, alignment: .leading)
        .background(Theme.card, in: RoundedRectangle(cornerRadius: 10))
        .overlay(RoundedRectangle(cornerRadius: 10).stroke(Theme.cardBorder))
    }
}
