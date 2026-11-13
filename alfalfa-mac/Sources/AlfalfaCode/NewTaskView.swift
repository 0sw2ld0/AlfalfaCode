import AlfalfaKit
import SwiftUI

struct Chip<Content: View>: View {
    @ViewBuilder var content: Content
    var body: some View {
        content.font(.system(size: 12.5)).foregroundStyle(Theme.secondary)
    }
}

/// Pantalla «Nuevo encargo».
struct NewTaskView: View {
    @EnvironmentObject private var model: AppModel
    @FocusState private var focused: Bool
    @State private var importingOllama = false
    @State private var planningQueue = false
    @State private var showingBrowserFirstRun = false
    @AppStorage("ajustes.navegador.primerInicioVisto") private var browserFirstRunSeen = false
    /// Al empezar con imágenes que el modelo no ve: cambiar de modelo o quitarlas.
    @State private var askingAboutImages = false
    @Environment(\.openSettings) private var openSettings

    var body: some View {
        HStack(spacing: 0) {
            VStack(spacing: 0) {
                header
                    // Cada vez que se abre, la rama y los cambios de la carpeta de ahora: sin esto el
                    // panel enseñaba los del arranque de la app (una rama que ya no existía).
                    .task { await model.refreshNewPanel() }
                Spacer(minLength: 0)
                VStack(spacing: 22) {
                    LeafIcon(size: 34)
                    if let firstRun = model.firstRun {
                        FirstRunCard(firstRun: firstRun, openProviders: openProviders)
                    }
                    folders
                    ollamaButton
                    Button { planningQueue = true } label: { Label("Cola de encargos", systemImage: "list.number") }
                        .disabled(model.folder == nil || model.launcher == nil || model.firstRun != nil)
                        .sheet(isPresented: $planningQueue) {
                            TaskQueueEditor(folder: model.folder ?? "") { tasks, mode, parallelism in
                                model.startQueue(tasks: tasks, mode: mode, parallelism: parallelism)
                            }
                        }
                    resumeList
                }
                Spacer(minLength: 0)
                if let error = model.loadError {
                    Text(error).font(.system(size: 12)).foregroundStyle(Theme.warning).padding(.bottom, 8)
                }
                composer
            }
            .frame(maxWidth: .infinity)
            Divider().overlay(Theme.border)
            SidePanel(folder: model.folder, snapshot: model.newSnapshot, ollama: model.newOllama, timeline: nil,
                      providerID: ModelProvider.id(of: model.model, in: model.models),
                      providerName: model.currentProviderName)
                .frame(width: 300)
        }
        .onChange(of: shouldShowBrowserFirstRun, initial: true) { _, show in
            if show { showingBrowserFirstRun = true }
        }
        .sheet(isPresented: $showingBrowserFirstRun, onDismiss: { browserFirstRunSeen = true }, content: {
            BrowserFirstRunCard().environmentObject(model)
        })
    }

    private var shouldShowBrowserFirstRun: Bool {
        !browserFirstRunSeen && model.firstRun == nil && model.launcher != nil
            && model.providers.contains(where: \.configured) && model.connecting == nil
    }

    private var header: some View {
        Text("Nuevo encargo").font(.system(size: 15, weight: .semibold))
            .frame(maxWidth: .infinity, minHeight: 48)
            .padding(.top, 4)
            .overlay(alignment: .bottom) { Divider().overlay(Theme.border) }
    }

    private var folders: some View {
        VStack(alignment: .leading, spacing: 4) {
            ForEach(model.folderChoices, id: \.self) { path in
                Button { model.select(folder: path) } label: {
                    HStack(spacing: 10) {
                        Image(systemName: "folder").foregroundStyle(Theme.secondary)
                        Text(URL(fileURLWithPath: path).lastPathComponent)
                        Spacer()
                        if model.folder == path {
                            Image(systemName: "checkmark").foregroundStyle(Theme.alfalfa).accessibilityLabel("Elegida")
                        }
                    }
                    .padding(.horizontal, 12).frame(minHeight: 36)
                    .background(model.folder == path ? Theme.selection : .clear, in: RoundedRectangle(cornerRadius: 8))
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .help(path)
            }
            Button { model.chooseFolder() } label: {
                HStack(spacing: 6) {
                    Text("Abrir otra carpeta")
                    Image(systemName: "chevron.down").font(.system(size: 10))
                }
                .foregroundStyle(Theme.alfalfaLight)
                .padding(.horizontal, 12).frame(minHeight: 32)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
        }
        .frame(width: 360)
        .accessibilityElement(children: .contain)
        .accessibilityLabel("Carpeta")
    }

    /// Como /import de Codex, pero desde la app de Ollama: trae un chat como contexto.
    private var ollamaButton: some View {
        Button { importingOllama = true } label: {
            Label("Cargar chat de Ollama", systemImage: "square.and.arrow.down")
                .foregroundStyle(model.folder == nil ? Theme.muted : Theme.secondary)
                .padding(.horizontal, 12).frame(minHeight: 32)
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .disabled(model.folder == nil)
        .help(model.folder == nil ? "Elige antes una carpeta" : "Continúa aquí una conversación de la app de Ollama")
        .sheet(isPresented: $importingOllama) { OllamaImportView().environmentObject(model) }
    }

    private var resumeList: some View {
        VStack(spacing: 4) {
            ForEach(model.resumable) { summary in
                Button { model.resume(summary) } label: {
                    HStack(spacing: 8) {
                        Image(systemName: "arrow.clockwise").foregroundStyle(Theme.warning)
                        Text("Reanudar «\(summary.task ?? summary.id)»").lineLimit(1)
                    }
                    .foregroundStyle(Theme.secondary)
                    .padding(.horizontal, 12).frame(minHeight: 32)
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .help("Sesión interrumpida: continúa desde \(summary.cwd ?? "")")
            }
        }
    }

    private var composer: some View {
        VStack(spacing: 10) {
            AttachmentStrip(images: $model.draftImages, blocked: !allowsImages)
                .frame(maxWidth: .infinity, alignment: .leading)
            ImageNoticeLine(notice: model.newImageNotice)
            HStack(spacing: 10) {
                Button { ImagePicker.choose(into: &model.draftImages, withImages: allowsImages) } label: {
                    Image(systemName: "plus").foregroundStyle(Theme.secondary)
                        .frame(width: 22, height: 22).contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .help(ImageSupport.attachHelp(allowsImages: allowsImages))
                .accessibilityLabel(allowsImages ? "Adjuntar imágenes o archivos" : "Adjuntar archivos")
                TextField("Describe una tarea o haz una pregunta", text: $model.draft, axis: .vertical)
                    .textFieldStyle(.plain).lineLimit(1...5).focused($focused)
                    .accessibilityLabel("Encargo")
                    .onChange(of: model.draft) { old, new in
                        guard let dropped = DroppedImages.extract(old: old, new: new) else { return }
                        model.draft = dropped.text
                        if !allowsImages, dropped.images.contains(where: Attachments.isImage) {
                            model.newImageNotice.show()
                        }
                        ImagePicker.add(dropped.images, to: &model.draftImages, withImages: allowsImages)
                    }
                    .onSubmit(start)
                Button(action: start) {
                    Image(systemName: "arrow.up").fontWeight(.bold).foregroundStyle(Theme.dark)
                        .frame(width: 30, height: 30)
                        .background(canStart ? Theme.alfalfa : Theme.muted.opacity(0.5), in: Circle())
                }
                .buttonStyle(.plain).disabled(!canStart).accessibilityLabel("Empezar")
            }
            .padding(.horizontal, 14).padding(.vertical, 10)
            .background(Theme.field, in: RoundedRectangle(cornerRadius: 14))
            .overlay(RoundedRectangle(cornerRadius: 14).stroke(Theme.border))
            .onDrop(of: [.fileURL], isTargeted: nil) { providers in
                ImagePicker.drop(providers, into: $model.draftImages, withImages: allowsImages) {
                    model.newImageNotice.show()
                }
            }
            .confirmationDialog(ImageChoiceButtons.title, isPresented: $askingAboutImages) {
                ImageChoiceButtons(alternatives: model.imageAlternatives(to: model.model)) { choice in
                    model.model = choice.model
                    model.startTask()
                } dropImages: {
                    model.draftImages.removeAll(where: Attachments.isImage)
                    model.startTask()
                }
            } message: {
                Text(ImageChoiceButtons.message(model: model.label(for: model.model),
                                                alternatives: model.imageAlternatives(to: model.model)))
            }

            HStack(spacing: 16) {
                Chip {
                    Label(model.folder.map { URL(fileURLWithPath: $0).lastPathComponent } ?? "Sin carpeta", systemImage: "folder")
                }
                modelMenu
                effortMenu
                ModeMenu(mode: model.mode) { model.mode = $0 }
                Toggle(isOn: $model.readOnly) { Text("Solo lectura") }
                    .toggleStyle(ReadOnlyToggleStyle())
                Spacer()
            }
            .padding(.horizontal, 6)
        }
        .frame(maxWidth: 720)
        .padding(.horizontal, 32).padding(.bottom, 24)
    }

    /// Con un modelo que se sabe que no ve imágenes no se adjuntan; si no se sabe, sí.
    private var allowsImages: Bool { model.allowsImages(model.model) }

    /// Empezar; con imágenes que el modelo elegido no ve, antes se pregunta qué hacer.
    private func start() {
        guard canStart else { return }
        if model.draftImages.contains(where: Attachments.isImage), !allowsImages {
            askingAboutImages = true
            return
        }
        model.startTask()
    }

    private var canStart: Bool {
        model.launcher != nil && model.folder != nil
            && (!model.draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || !model.draftImages.isEmpty)
    }

    private var modelMenu: some View {
        Menu {
            ModelMenuItems(app: model, groups: model.modelGroups, selected: model.model) { name in
                model.choose(name) { model.model = $0 }
            }
            Divider()
            Button("Proveedores…") { openProviders() }
        } label: {
            Text(model.label(for: model.model)).font(.system(size: 12.5, design: .monospaced))
        }
        .menuStyle(.borderlessButton).fixedSize().accessibilityLabel("Modelo")
    }

    private var effortMenu: some View {
        Menu {
            ForEach(Presentation.efforts, id: \.value) { effort in
                Button { model.effort = effort.value } label: {
                    if effort.value == model.effort { Label(effort.label, systemImage: "checkmark") } else { Text(effort.label) }
                }
            }
        } label: {
            Text(Presentation.effortLabel(model.effort)).font(.system(size: 12.5))
        }
        .menuStyle(.borderlessButton).fixedSize().accessibilityLabel("Esfuerzo")
    }
}

// MARK: - Métodos privados

extension NewTaskView {
    /// «Proveedores…» abre la misma parte de la ventana de Ajustes.
    private func openProviders() {
        UserDefaults.standard.set(SettingsSection.proveedores.rawValue, forKey: Preferences.Key.section)
        openSettings()
    }
}

struct ReadOnlyToggleStyle: ToggleStyle {
    func makeBody(configuration: Configuration) -> some View {
        Button { configuration.isOn.toggle() } label: {
            HStack(spacing: 5) {
                Image(systemName: configuration.isOn ? "lock.fill" : "lock.open")
                configuration.label
            }
            .font(.system(size: 12.5))
            .foregroundStyle(configuration.isOn ? Theme.alfalfaLight : Theme.secondary)
            .padding(.horizontal, 8).padding(.vertical, 3)
            .background(configuration.isOn ? Theme.alfalfa.opacity(0.18) : .clear, in: Capsule())
        }
        .buttonStyle(.plain)
        .accessibilityValue(configuration.isOn ? "activado" : "desactivado")
        .help("En solo lectura el agente no puede editar archivos ni ejecutar órdenes")
    }
}
