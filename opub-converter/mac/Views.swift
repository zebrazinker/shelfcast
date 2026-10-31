import SwiftUI
import AppKit
import UniformTypeIdentifiers

// MARK: - Bausteine

/// Drehendes Symbol; TimelineView animiert zuverlässig auch in wiederverwendeten List-Zellen.
struct Spinner: View {
    var body: some View {
        TimelineView(.animation) { ctx in
            Image(systemName: "arrow.triangle.2.circlepath").font(.caption).foregroundStyle(.secondary)
                .rotationEffect(.degrees(ctx.date.timeIntervalSinceReferenceDate.truncatingRemainder(dividingBy: 1) * 360))
        }
    }
}

/// Kleine Kapsel rechts in der Zeile. Grün nur für "Auf dem Picco".
struct Badge: View {
    enum Kind { case neutral, green }
    let text: String
    var kind: Kind = .neutral
    var icon: String?
    var body: some View {
        HStack(spacing: 3) {
            if let icon { Image(systemName: icon).font(.system(size: 9, weight: .bold)) }
            Text(text).lineLimit(1)
        }
        .font(.caption)
        .foregroundStyle(kind == .green ? Color.green : Color.secondary)
        .padding(.horizontal, 8).padding(.vertical, 2)
        .background(Capsule().fill(kind == .green ? Color.green.opacity(0.14) : Color.secondary.opacity(0.14)))
    }
}

/// Fortschritt einer Zeile: "Lädt…", "Konvertiert…", "Sendet… 40 %" mit kleinem Indikator.
struct WorkState: View {
    let text: String
    var progress: Double?
    var body: some View {
        HStack(spacing: 6) {
            Text(text).font(.caption).foregroundStyle(.secondary).monospacedDigit()
            if let p = progress { ProgressView(value: p).frame(width: 36) } else { Spinner() }
        }
    }
}

/// Statusanzeige einer Buch-Zeile (Dateien und ABS); nil, wenn nichts anzuzeigen ist.
struct BookStatus: View {
    let status: Book.Status
    var body: some View {
        switch status {
        case .idle: Badge(text: "Bereit")
        case .queued: WorkState(text: "Wartet…")
        case .running: WorkState(text: "Konvertiert…")
        case .sending(let p): WorkState(text: "Sendet… \(p) %", progress: Double(p) / 100)
        case .done(let path):
            if path.hasPrefix("/Volumes/") || path == "An Picco gesendet" { Badge(text: "Auf dem Picco", kind: .green, icon: "checkmark") }
            else { Badge(text: "In Downloads/Boox", icon: "checkmark").help(path) }
        case .failed: Badge(text: "Fehlgeschlagen")
        }
    }
}

struct CoverThumb: View {
    let image: NSImage?
    var body: some View {
        Group {
            if let image { Image(nsImage: image).resizable().scaledToFill() }
            else { Image(systemName: "book.closed").font(.caption).foregroundStyle(.secondary) }
        }
        .frame(width: 30, height: 42)
        .background(Color.secondary.opacity(0.12)).clipShape(RoundedRectangle(cornerRadius: 3))
        .overlay(RoundedRectangle(cornerRadius: 3).stroke(Color.primary.opacity(0.1), lineWidth: 0.5))
    }
}

/// Titel/Autor-Spalte, optional mit Fehlerzeile
struct TitleBlock: View {
    let title: String, author: String
    var error: String?
    var reading: ABSProgress?
    var body: some View {
        VStack(alignment: .leading, spacing: 1) {
            Text(title).fontWeight(.medium).lineLimit(1).truncationMode(.tail)
            HStack(spacing: 8) {
                if !author.isEmpty { Text(author).font(.callout).foregroundStyle(.secondary).lineLimit(1) }
                if let r = reading, r.state != .unread {
                    Label(r.state == .read ? "Gelesen" : "\(r.percent) %",
                          systemImage: r.state == .read ? "book.closed.fill" : "book")
                        .font(.caption).foregroundStyle(.tertiary).lineLimit(1).fixedSize()
                        .help(r.state == .read ? "Gelesen" : "Liest · \(r.percent) %")
                }
            }
            if let error { Text(error).font(.caption).foregroundStyle(.red).lineLimit(2) }
        }
    }
}

// MARK: - Dateien

struct DropZone: View {
    @EnvironmentObject var m: Model
    var body: some View {
        VStack(spacing: 12) {
            Image(systemName: "books.vertical").font(.system(size: 34, weight: .light))
            Text("EPUBs hierher ziehen").font(.title3)
            Button("Dateien auswählen…") { m.picking = true }.buttonStyle(.glass)
        }
        .foregroundStyle(.secondary)
        // Dezente Glasfläche hinter dem Inhalt; der gestrichelte Rahmen bleibt das Ablageziel
        .padding(.horizontal, 48).padding(.vertical, 32)
        .glassEffect(.regular, in: .rect(cornerRadius: 20))
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(
            RoundedRectangle(cornerRadius: 12)
                .strokeBorder(style: StrokeStyle(lineWidth: 1.5, dash: [6, 5]))
                .foregroundStyle(m.targeted ? Color.accentColor : Color.secondary.opacity(0.4))
                .padding(24)
        )
    }
}

struct FileRow: View {
    @EnvironmentObject var m: Model
    let book: Book
    var body: some View {
        HStack(spacing: 10) {
            CoverThumb(image: book.cover)
            if case .failed(let e) = book.status { TitleBlock(title: book.title, author: book.author, error: e) }
            else { TitleBlock(title: book.title, author: book.author) }
            Spacer(minLength: 8)
            Text(ByteCountFormatter.string(fromByteCount: book.size, countStyle: .file))
                .font(.caption).foregroundStyle(.secondary).monospacedDigit()
            BookStatus(status: book.status)
        }
        .padding(.vertical, 3)
        .tag(book.id)
        .contextMenu {
            let ids = m.fileSelection.contains(book.id) ? m.fileSelection : [book.id]
            Button("Entfernen") { m.remove(ids) }
        }
    }
}

struct FilesView: View {
    @EnvironmentObject var m: Model
    var body: some View {
        Group {
            if m.books.isEmpty { DropZone() }
            else {
                List(selection: $m.fileSelection) {
                    ForEach(m.books) { FileRow(book: $0) }
                }
                .listStyle(.inset)
                .onDeleteCommand { m.remove(m.fileSelection) }
            }
        }
        .toolbar {
            ToolbarItem {
                Button { m.picking = true } label: { Label("Dateien hinzufügen", systemImage: "plus") }
                    .help("EPUB-Dateien hinzufügen")
            }
        }
    }
}

// MARK: - Seitenleiste

struct DeviceRow: View {
    @EnvironmentObject var m: Model
    let dest: Model.Dest
    let title: String, icon: String
    let hint: String
    var body: some View {
        let ok = m.available(dest), on = m.dest == dest
        HStack(spacing: 8) {
            Image(systemName: icon).frame(width: 18).foregroundStyle(on ? Color.accentColor : Color.secondary)
            VStack(alignment: .leading, spacing: 1) {
                Text(title).lineLimit(1)
                Text(hint).font(.caption).foregroundStyle(.secondary).lineLimit(2).fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 0)
            if dest == .wifi && m.wifiDevices.count > 1 {
                Menu {
                    Picker("Gerät", selection: Binding(get: { m.wifiDevice?.name ?? "" }, set: { m.wifiName = $0; m.manualDest = .wifi })) {
                        ForEach(m.wifiDevices, id: \.name) { Text($0.name).tag($0.name) }
                    }.pickerStyle(.inline)
                } label: { Image(systemName: "chevron.up.chevron.down").font(.caption) }
                    .menuStyle(.borderlessButton).menuIndicator(.hidden).fixedSize()
            }
            if on { Image(systemName: "checkmark").font(.caption.bold()).foregroundStyle(Color.accentColor) }
        }
        .padding(.vertical, 3).padding(.horizontal, 6)
        .background(RoundedRectangle(cornerRadius: 6).fill(on ? Color.accentColor.opacity(0.13) : Color.clear))
        .opacity(ok ? 1 : 0.5)
        .contentShape(Rectangle())
        .onTapGesture { if ok && !m.running { m.manualDest = dest } }
        .help(ok ? "" : hint)
    }
}

struct Sidebar: View {
    @EnvironmentObject var m: Model
    var body: some View {
        List(selection: Binding<Model.Source?>(get: { m.source }, set: { if let s = $0 { m.source = s } })) {
            Section("Quellen") {
                Label("Dateien", systemImage: "doc").tag(Model.Source.files)
                Label("Audiobookshelf", systemImage: "headphones").tag(Model.Source.abs)
            }
            Section("Gerät") {
                DeviceRow(dest: .usb, title: "Picco · USB", icon: "cable.connector",
                          hint: m.device.map { "\($0.volume)" } ?? "Nicht angeschlossen")
                    .listRowInsets(EdgeInsets(top: 1, leading: 6, bottom: 1, trailing: 6))
                DeviceRow(dest: .wifi, title: "Picco · WLAN", icon: "wifi",
                          hint: m.wifiDevice?.name ?? "„Lokale Netzwerkübertragung“ öffnen")
                    .listRowInsets(EdgeInsets(top: 1, leading: 6, bottom: 1, trailing: 6))
                DeviceRow(dest: .local, title: "Mac · Downloads/Boox", icon: "laptopcomputer", hint: "Immer verfügbar")
                    .listRowInsets(EdgeInsets(top: 1, leading: 6, bottom: 1, trailing: 6))
            }
        }
        .listStyle(.sidebar)
        .safeAreaInset(edge: .bottom, spacing: 0) {
            // Kein eigener Hintergrund: die Seitenleiste bringt ihr Glas selbst mit
            Button { m.showSettings = true } label: {
                Label("Einstellungen", systemImage: "gearshape").frame(maxWidth: .infinity, alignment: .leading)
            }
            .buttonStyle(.plain).foregroundStyle(.secondary).padding(.horizontal, 14).padding(.vertical, 10)
        }
    }
}

// MARK: - Einstellungen

struct SettingsView: View {
    @EnvironmentObject var m: Model
    @EnvironmentObject var abs: ABSStore
    @ObservedObject private var appearance = AppearanceStore.shared
    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("Einstellungen").font(.title2.bold())
            Picker("Erscheinungsbild", selection: $appearance.mode) {
                ForEach(AppearanceMode.allCases) { Text($0.label).tag($0) }
            }.pickerStyle(.segmented)
            GroupBox("Audiobookshelf") {
                VStack(alignment: .leading, spacing: 8) {
                    if abs.connected {
                        LabeledContent("Server", value: abs.serverURL)
                        if abs.libraries.count > 1 {
                            Picker("Bibliothek", selection: $abs.libraryID) {
                                ForEach(abs.libraries) { Text($0.name).tag($0.id) }
                            }
                        }
                        HStack {
                            Label("Verbunden", systemImage: "checkmark.circle").foregroundStyle(.secondary)
                            Spacer()
                            Button("Abmelden") { abs.disconnect() }.buttonStyle(.glass)
                        }
                    } else {
                        Text("Nicht verbunden. Die Anmeldung findest du unter „Audiobookshelf“.").foregroundStyle(.secondary)
                    }
                }.frame(maxWidth: .infinity, alignment: .leading).padding(6)
            }
            GroupBox("Übertragungsstatus") {
                HStack {
                    Text("\(abs.transfers.records.count) Bücher als „Auf dem Picco“ markiert").foregroundStyle(.secondary)
                    Spacer()
                    Button("Übertragen-Status zurücksetzen…") { abs.confirmReset = true }
                        .buttonStyle(.glass).disabled(abs.transfers.records.isEmpty)
                }.padding(6)
            }
            HStack {
                Text("Shelfcast \(Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "")")
                    .font(.caption).foregroundStyle(.secondary)
                Spacer()
                Button("Fertig") { m.showSettings = false }.keyboardShortcut(.defaultAction).buttonStyle(.glassProminent)
            }
        }
        .padding(20).frame(width: 460)
        .confirmationDialog("Übertragungsstatus aller Bücher zurücksetzen?", isPresented: $abs.confirmReset) {
            Button("Zurücksetzen", role: .destructive) { abs.transfers.reset() }
            Button("Abbrechen", role: .cancel) {}
        } message: {
            Text("Alle Markierungen „Auf dem Picco“ werden gelöscht. Die Bücher selbst bleiben unverändert.")
        }
    }
}

// MARK: - Fußleiste

struct BottomBar: View {
    @EnvironmentObject var m: Model
    @EnvironmentObject var abs: ABSStore

    var isABS: Bool { m.source == .abs }
    var busy: Bool { m.running || abs.sending }
    var count: Int { isABS ? abs.pickedEpubs.count : m.books.count }
    var allDone: Bool { !m.books.isEmpty && m.pending == 0 }

    var body: some View {
        // Schwebende Glasleiste über der Liste; Status, Fortschritt und Aktionen teilen sich einen Container
        GlassEffectContainer(spacing: 10) {
            HStack(spacing: 10) {
                VStack(alignment: .leading, spacing: 3) {
                    if busy {
                        progress
                    } else {
                        let n = isABS ? "\(count) ausgewählt" : "\(count) \(count == 1 ? "Buch" : "Bücher")"
                        Text("\(n) · Ziel: \(m.destLabel)").lineLimit(1).truncationMode(.middle)
                        if !m.summary.isEmpty {
                            Text(m.summary).font(.caption).foregroundStyle(.secondary).lineLimit(2)
                        }
                    }
                }
                .font(.callout).foregroundStyle(.secondary)
                .padding(.horizontal, 16).padding(.vertical, 10)
                .frame(maxWidth: .infinity, alignment: .leading)
                .glassEffect(.regular, in: .rect(cornerRadius: 22))
                if !isABS && allDone && !busy {
                    Button("Liste leeren") { m.clear() }.buttonStyle(.glass).controlSize(.large)
                }
                Button(m.dest == .local ? "Konvertieren" : "An Picco senden") {
                    if isABS { Task { await m.sendABS(abs) } } else { m.convert() }
                }
                .keyboardShortcut(.defaultAction).buttonStyle(.glassProminent).controlSize(.large)
                .disabled(busy || (isABS ? count == 0 : m.pending == 0))
            }
        }
        .padding(.horizontal, 16).padding(.bottom, 12)
    }

    @ViewBuilder var progress: some View {
        if abs.sending {
            ProgressView(value: Double(abs.sendDone), total: Double(max(abs.sendTotal * 2, 1)))
            Text("Lädt \(min(abs.sendDone + 1, abs.sendTotal)) von \(abs.sendTotal)…")
        } else {
            // Eins-Schritt-Versand aus ABS: Laden und Senden zählen je ein Teilstück
            let flow = abs.sendTotal > 0
            ProgressView(value: Double((flow ? abs.sendDone : 0) + m.done), total: Double(max(flow ? abs.sendTotal * 2 : m.total, 1)))
            Text("\(m.dest == .local ? "Konvertiere" : "Sende") \(min(m.done + 1, m.total)) von \(m.total): \(m.current)…")
                .lineLimit(1).truncationMode(.middle)
        }
    }
}

// MARK: - Hauptfenster

struct ContentView: View {
    @EnvironmentObject var m: Model
    @EnvironmentObject var abs: ABSStore

    var body: some View {
        NavigationSplitView {
            Sidebar().navigationSplitViewColumnWidth(min: 170, ideal: 190, max: 260)
        } detail: {
            ZStack {
                if m.source == .abs { ABSBrowser() } else { FilesView() }
                if m.targeted && !m.books.isEmpty {
                    RoundedRectangle(cornerRadius: 8).stroke(Color.accentColor, lineWidth: 3).padding(4).allowsHitTesting(false)
                }
            }
            .frame(maxHeight: .infinity)
            // Die Liste läuft unter der Glasleiste weiter (Inset wird zum Scroll-Rand)
            .safeAreaInset(edge: .bottom, spacing: 0) {
                if m.source == .files || abs.connected { BottomBar() }
            }
            .scrollEdgeEffectStyle(.soft, for: .bottom)
            .navigationTitle(m.source == .abs ? "Audiobookshelf" : "Dateien")
            // Ablegen funktioniert in beiden Ansichten; Dateien landen unter "Dateien"
            .onDrop(of: [.fileURL], isTargeted: $m.targeted) { providers in
                for p in providers {
                    _ = p.loadObject(ofClass: URL.self) { url, _ in
                        if let url { Task { @MainActor in m.source = .files; m.add([url]) } }
                    }
                }
                return true
            }
        }
        .frame(minWidth: 760, minHeight: 480)
        .sheet(isPresented: $m.showSettings) { SettingsView() }
        .fileImporter(isPresented: $m.picking, allowedContentTypes: [UTType(filenameExtension: "epub") ?? .data],
                      allowsMultipleSelection: true) { r in if case .success(let u) = r { m.source = .files; m.add(u) } }
        .alert("Bereits hinzugefügt", isPresented: Binding(get: { !m.pendingDup.isEmpty }, set: { _ in })) {
            Button("Überspringen", role: .cancel) { m.resolveDup(addAnyway: false) }
            Button("Trotzdem hinzufügen") { m.resolveDup(addAnyway: true) }
        } message: {
            Text(m.pendingDup.map { $0.lastPathComponent }.joined(separator: "\n"))
        }
    }
}
