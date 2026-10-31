import SwiftUI
import AppKit

// MARK: - Audiobookshelf-Ansicht

struct ABSCover: View {
    @EnvironmentObject var abs: ABSStore
    let id: String
    var body: some View {
        CoverThumb(image: abs.covers[id]).task(id: id) { await abs.loadCover(id) }
    }
}

struct ABSRow: View {
    @EnvironmentObject var abs: ABSStore
    @EnvironmentObject var m: Model
    let item: ABSItem

    /// Rechts: Status der laufenden Übergabe, sonst Protokoll ("Auf dem Picco"), sonst "Neu"
    @ViewBuilder func badge(_ book: Book?) -> some View {
        if let r = item.disabledReason { Badge(text: r) }
        else if abs.downloading.contains(item.id) { WorkState(text: "Lädt…") }
        else if let b = book, !isSettled(b.status) { BookStatus(status: b.status) }
        else if let t = abs.transfers.record(for: item.id) {
            Badge(text: "Auf dem Picco · \(TransferLog.shortDate(t.date))", kind: .green, icon: "checkmark")
                .help(t.via == "manuell" ? "Von Hand markiert" : "Übertragen per \(t.via)")
        }
        else if let b = book, case .done = b.status { BookStatus(status: b.status) }   // z. B. nur auf den Mac kopiert
        else { Badge(text: "Neu") }
    }

    /// Fertig oder fehlgeschlagen/bereit: kein Arbeitsstatus mehr
    private func isSettled(_ s: Book.Status) -> Bool {
        switch s { case .queued, .running, .sending: return false; default: return true }
    }

    var body: some View {
        let on = Binding(get: { abs.selection.contains(item.id) },
                         set: { if $0 { abs.selection.insert(item.id) } else { abs.selection.remove(item.id) } })
        let book = m.book(forItem: item.id)
        var err = abs.itemErrors[item.id]
        if err == nil, let b = book, case .failed(let e) = b.status { err = e }
        return HStack(spacing: 10) {
            Toggle("", isOn: on).labelsHidden().disabled(!item.isEpub || busy(book))
            ABSCover(id: item.id)
            TitleBlock(title: item.title, author: item.author, error: err, reading: abs.progress[item.id])
            Spacer(minLength: 8)
            badge(book)
        }
        .padding(.vertical, 3)
        .opacity(item.isEpub ? 1 : 0.45)
        .contentShape(Rectangle())
        .onTapGesture { if item.isEpub && !busy(book) { on.wrappedValue.toggle() } }
        .contextMenu {
            let t = abs.targets(for: item.id)
            if t.contains(where: { abs.transfers.record(for: $0.id) == nil }) {
                Button("Als übertragen markieren") { abs.markTransferred(t) }
            }
            if t.contains(where: { abs.transfers.record(for: $0.id) != nil }) {
                Button("Als nicht übertragen markieren") { abs.markNotTransferred(t) }
            }
        }
    }

    private func busy(_ b: Book?) -> Bool {
        if abs.downloading.contains(item.id) { return true }
        if let b, !isSettled(b.status) { return true }
        return false
    }
}

struct ABSLogin: View {
    @EnvironmentObject var abs: ABSStore
    var body: some View {
        VStack(spacing: 14) {
            Image(systemName: "headphones").font(.system(size: 32, weight: .light)).foregroundStyle(.secondary)
            Text("Mit Audiobookshelf verbinden").font(.title3)
            VStack(alignment: .leading, spacing: 8) {
                TextField("Server-Adresse (z. B. http://nas:13378)", text: $abs.serverURL).textFieldStyle(.roundedBorder)
                SecureField("API-Key", text: $abs.draftKey).textFieldStyle(.roundedBorder)
                Text("Den Key erzeugst du in Audiobookshelf unter Einstellungen → API-Keys.")
                    .font(.caption).foregroundStyle(.secondary)
            }.frame(width: 340)
            if !abs.error.isEmpty { Text(abs.error).foregroundStyle(.red).font(.callout).multilineTextAlignment(.center) }
            HStack {
                if abs.loading { ProgressView().controlSize(.small) }
                Button("Verbinden") { Task { await abs.connect(url: abs.serverURL, key: abs.draftKey) } }
                    .keyboardShortcut(.defaultAction).buttonStyle(.glassProminent)
                    .disabled(abs.loading || abs.serverURL.isEmpty || abs.draftKey.isEmpty)
            }
        }
        .padding(20).frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}

struct ABSBrowser: View {
    @EnvironmentObject var abs: ABSStore

    var body: some View {
        if !abs.connected { ABSLogin() }
        else {
            ZStack {
                if !abs.error.isEmpty && abs.items.isEmpty {
                    Text(abs.error).foregroundStyle(.red).multilineTextAlignment(.center).padding(20)
                } else if abs.loading && abs.items.isEmpty {
                    ProgressView()
                } else if abs.visible.isEmpty {
                    Text(abs.items.isEmpty ? "Keine Bücher mit E-Book gefunden." : "Keine Treffer.").foregroundStyle(.secondary)
                } else {
                    List { ForEach(abs.visible) { ABSRow(item: $0) } }.listStyle(.inset)
                }
            }
            .frame(maxHeight: .infinity)
            .searchable(text: $abs.search, prompt: "Titel oder Autor")
            .toolbar {
                ToolbarItemGroup {
                    Menu {
                        Picker("Filter", selection: $abs.filter) {
                            ForEach(ABSFilter.allCases) { Text($0.rawValue).tag($0) }
                        }.pickerStyle(.inline)
                        Divider()
                        Picker("Lesestatus", selection: $abs.readFilter) {
                            ForEach(ABSReadFilter.allCases) { Text($0.rawValue).tag($0) }
                        }.pickerStyle(.inline)
                    } label: {
                        Label(abs.filter.rawValue, systemImage: abs.filter == .all && abs.readFilter == .all
                              ? "line.3.horizontal.decrease.circle" : "line.3.horizontal.decrease.circle.fill")
                    }.help("Filter")
                    Menu {
                        Picker("Sortieren nach", selection: $abs.sort) {
                            ForEach(ABSSort.allCases) { Text($0.rawValue).tag($0) }
                        }.pickerStyle(.inline)
                        Divider()
                        Picker("Reihenfolge", selection: $abs.ascending) {
                            Text("Aufsteigend").tag(true); Text("Absteigend").tag(false)
                        }.pickerStyle(.inline)
                    } label: {
                        Label(abs.sort.rawValue, systemImage: abs.ascending ? "arrow.up" : "arrow.down")
                    }.help("Sortierung")
                }
                ToolbarSpacer(.fixed)
                ToolbarItem {
                    Button { Task { await abs.refresh() } } label: { Label("Neu laden", systemImage: "arrow.clockwise") }
                        .help("Neu laden").disabled(abs.loading)
                }
            }
            .task { if abs.items.isEmpty { await abs.refresh() } }
            .onChange(of: abs.libraryID) { Task { await abs.loadItems() } }
        }
    }
}
