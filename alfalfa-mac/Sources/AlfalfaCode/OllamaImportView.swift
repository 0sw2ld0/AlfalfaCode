import AlfalfaKit
import SwiftUI

/// «Cargar chat de Ollama»: lista los chats de la app de Ollama (sólo títulos y
/// recuentos), y el elegido se copia en una sesión nueva de la carpeta actual con
/// su conversación como contexto. La base de Ollama sólo se lee.
struct OllamaImportView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss
    @State private var chats: [OllamaChat] = []
    @State private var search = ""
    @State private var selected: String?
    @State private var loading = true
    @State private var importing = false
    @State private var error: String?

    private var shown: [OllamaChat] { chats.filter { $0.matches(search) } }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            VStack(alignment: .leading, spacing: 4) {
                Text("Cargar chat de Ollama").font(.system(size: 16, weight: .semibold))
                Text("El chat se copia en una sesión nueva de «\(folderName)» y sigue desde ahí con su conversación como contexto. Ollama no cambia.")
                    .font(.system(size: 12.5)).foregroundStyle(Theme.muted).fixedSize(horizontal: false, vertical: true)
            }
            HStack(spacing: 8) {
                Image(systemName: "magnifyingglass").foregroundStyle(Theme.muted)
                TextField("Buscar por título o modelo", text: $search).textFieldStyle(.plain)
            }
            .padding(.horizontal, 12).frame(height: 34)
            .background(Theme.field, in: RoundedRectangle(cornerRadius: 9))
            .overlay(RoundedRectangle(cornerRadius: 9).stroke(Theme.border))
            content.frame(maxWidth: .infinity, maxHeight: .infinity)
            if let error {
                Text(error).font(.system(size: 12.5)).foregroundStyle(Theme.warning).fixedSize(horizontal: false, vertical: true)
            }
            HStack {
                Spacer()
                Button("Cancelar", role: .cancel) { dismiss() }.keyboardShortcut(.cancelAction)
                Button(importing ? "Cargando…" : "Cargar") { load() }
                    .keyboardShortcut(.defaultAction)
                    .disabled(selected == nil || importing || model.folder == nil)
            }
        }
        .padding(20)
        .frame(width: 560, height: 520)
        .background(Theme.background)
        .task { await refresh() }
    }

    @ViewBuilder private var content: some View {
        if loading {
            ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity)
        } else if chats.isEmpty {
            Text(error == nil ? "No hay chats en la app de Ollama." : "")
                .foregroundStyle(Theme.muted).frame(maxWidth: .infinity, maxHeight: .infinity)
        } else {
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 2) {
                    ForEach(shown) { chat in
                        Button { selected = chat.id } label: { row(chat) }
                            .buttonStyle(.plain)
                            .simultaneousGesture(TapGesture(count: 2).onEnded { selected = chat.id; load() })
                    }
                }
            }
        }
    }

    private func row(_ chat: OllamaChat) -> some View {
        HStack(spacing: 12) {
            Image(systemName: "bubble.left.and.bubble.right").foregroundStyle(Theme.secondary).frame(width: 18)
            VStack(alignment: .leading, spacing: 2) {
                Text(chat.title).font(.system(size: 13.5, weight: .medium)).lineLimit(1)
                Text(detail(chat)).font(.system(size: 12)).foregroundStyle(Theme.muted).lineLimit(1)
            }
            Spacer(minLength: 0)
            if chat.date > 0 {
                Text(AgentActions.format(Date(timeIntervalSince1970: chat.date)))
                    .font(.system(size: 12)).foregroundStyle(Theme.muted)
            }
        }
        .padding(.horizontal, 12).frame(height: 48)
        .background(selected == chat.id ? Theme.selection : .clear, in: RoundedRectangle(cornerRadius: 9))
        .contentShape(Rectangle())
    }

    private func detail(_ chat: OllamaChat) -> String {
        let count = chat.messages == 1 ? "1 mensaje" : "\(chat.messages) mensajes"
        return chat.models.isEmpty ? count : count + " · " + chat.models.map(Presentation.shortModel).joined(separator: ", ")
    }

    private var folderName: String {
        model.folder.map { URL(fileURLWithPath: $0).lastPathComponent } ?? "la carpeta elegida"
    }

    private func refresh() async {
        loading = true
        let result = await model.ollamaChats()
        chats = result.chats
        error = result.error
        loading = false
    }

    private func load() {
        guard let id = selected, let chat = chats.first(where: { $0.id == id }), !importing else { return }
        importing = true
        Task {
            if let text = await model.importOllama(chat) {
                error = text
                importing = false
            } else {
                dismiss()
            }
        }
    }
}
