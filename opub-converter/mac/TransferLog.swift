import SwiftUI
import Combine

// MARK: - Übertragungsprotokoll

struct TransferRecord: Codable, Equatable {
    var date: Date
    var via: String      // "USB" | "WLAN" | "manuell" (von Hand markiert)
    var title: String
}

/// Merkt, welche ABS-Bücher auf dem Picco gelandet sind (Item-ID → Eintrag).
/// Datei: ~/Library/Application Support/de.forell.shelfcast/transfers.json
@MainActor
final class TransferLog: ObservableObject {
    static let shared = TransferLog(file: TransferLog.defaultFile)
    static var defaultFile: URL {
        FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("de.forell.shelfcast/transfers.json")
    }

    @Published private(set) var records: [String: TransferRecord] = [:]
    private let file: URL

    init(file: URL) {
        self.file = file
        load()
    }

    private static func coder() -> (JSONEncoder, JSONDecoder) {
        let e = JSONEncoder(); e.dateEncodingStrategy = .iso8601; e.outputFormatting = [.prettyPrinted, .sortedKeys]
        let d = JSONDecoder(); d.dateDecodingStrategy = .iso8601
        return (e, d)
    }

    /// Fehlende Datei = leer. Defekte Datei wird als .corrupt beiseitegelegt, damit sie nicht still überschrieben wird.
    private func load() {
        guard let data = try? Data(contentsOf: file) else { return }
        if let r = try? Self.coder().1.decode([String: TransferRecord].self, from: data) { records = r; return }
        let bad = file.appendingPathExtension("corrupt")
        try? FileManager.default.removeItem(at: bad)
        try? FileManager.default.moveItem(at: file, to: bad)
    }

    @discardableResult
    private func save() -> Bool {
        do {
            try FileManager.default.createDirectory(at: file.deletingLastPathComponent(), withIntermediateDirectories: true)
            try Self.coder().0.encode(records).write(to: file, options: .atomic)
            return true
        } catch { return false }
    }

    func record(for id: String) -> TransferRecord? { records[id] }

    func mark(_ id: String, via: String, title: String, date: Date = Date()) {
        // ISO-8601 speichert nur ganze Sekunden: gleich so ablegen, damit Laden dasselbe ergibt
        records[id] = TransferRecord(date: Date(timeIntervalSince1970: date.timeIntervalSince1970.rounded(.down)), via: via, title: title)
        save()
    }

    func unmark(_ ids: Set<String>) {
        for id in ids { records[id] = nil }
        save()
    }

    func reset() { records = [:]; save() }

    /// Kurzes deutsches Datum, z. B. "31.10." (mit Jahr, wenn nicht das laufende)
    static func shortDate(_ d: Date, now: Date = Date()) -> String {
        let f = DateFormatter()
        f.locale = Locale(identifier: "de_DE")
        let cal = Calendar.current
        f.dateFormat = cal.component(.year, from: d) == cal.component(.year, from: now) ? "dd.MM." : "dd.MM.yy"
        return f.string(from: d)
    }

    /// ABS-Downloads liegen in …/abs/<itemId>/<Titel>.epub; daraus die Item-ID.
    static func itemID(for url: URL, cacheRoot: URL) -> String? {
        let root = cacheRoot.standardizedFileURL.pathComponents
        let comps = url.standardizedFileURL.pathComponents
        guard comps.count > root.count + 1, Array(comps.prefix(root.count)) == root else { return nil }
        return comps[root.count]
    }
}

// MARK: - Filter und Sortierung (ABS-Liste)

enum ABSFilter: String, CaseIterable, Identifiable {
    case all = "Alle", open = "Nicht übertragen", sent = "Übertragen"
    var id: String { rawValue }
}

enum ABSReadFilter: String, CaseIterable, Identifiable {
    case all = "Alle", unread = "Ungelesen", reading = "Liest gerade", read = "Gelesen"
    var id: String { rawValue }
}

/// "Übertragen": nach Übertragungsdatum; nie übertragene Bücher zählen als ältestes.
/// Aufsteigend = nicht übertragene zuerst, absteigend = zuletzt übertragene zuerst.
enum ABSSort: String, CaseIterable, Identifiable {
    case title = "Titel", author = "Autor", added = "Neu hinzugefügt", transferred = "Übertragen", progress = "Lesefortschritt"
    var id: String { rawValue }
}

func absArrange(_ items: [ABSItem], search: String, filter: ABSFilter, sort: ABSSort, ascending: Bool,
                transferDate: (String) -> Date?,
                reading: ABSReadFilter = .all, progress: (String) -> ABSProgress? = { _ in nil }) -> [ABSItem] {
    let q = search.trimmingCharacters(in: .whitespaces).lowercased()
    var out = items
    if !q.isEmpty { out = out.filter { $0.title.lowercased().contains(q) || $0.author.lowercased().contains(q) } }
    switch filter {
    case .all: break
    case .open: out = out.filter { transferDate($0.id) == nil }
    case .sent: out = out.filter { transferDate($0.id) != nil }
    }
    switch reading {
    case .all: break
    case .unread: out = out.filter { (progress($0.id)?.state ?? .unread) == .unread }
    case .reading: out = out.filter { progress($0.id)?.state == .reading }
    case .read: out = out.filter { progress($0.id)?.state == .read }
    }
    func cmp(_ a: String, _ b: String) -> ComparisonResult { a.localizedStandardCompare(b) }
    func order(_ a: ABSItem, _ b: ABSItem) -> ComparisonResult {
        switch sort {
        case .title: return cmp(a.title, b.title)
        case .author:
            // Items ohne Autor ans Ende (bei aufsteigend)
            if a.sortAuthor.isEmpty != b.sortAuthor.isEmpty { return a.sortAuthor.isEmpty ? .orderedDescending : .orderedAscending }
            return cmp(a.sortAuthor, b.sortAuthor)
        case .added:
            return a.addedAt < b.addedAt ? .orderedAscending : (a.addedAt > b.addedAt ? .orderedDescending : .orderedSame)
        case .progress:
            let x = progress(a.id)?.sortValue ?? 0, y = progress(b.id)?.sortValue ?? 0
            return x < y ? .orderedAscending : (x > y ? .orderedDescending : .orderedSame)
        case .transferred:
            let x = transferDate(a.id) ?? .distantPast, y = transferDate(b.id) ?? .distantPast
            return x < y ? .orderedAscending : (x > y ? .orderedDescending : .orderedSame)
        }
    }
    return out.sorted { a, b in
        var r = order(a, b)
        if r == .orderedSame { return cmp(a.title, b.title) == .orderedAscending || (cmp(a.title, b.title) == .orderedSame && a.id < b.id) }
        if !ascending { r = r == .orderedAscending ? .orderedDescending : .orderedAscending }
        return r == .orderedAscending
    }
}
