import SwiftUI
import AppKit
import Security
import Combine

// MARK: - Schlüsselbund (API-Key)

/// API-Key im macOS-Schlüsselbund (generisches Passwort). Der Key wird nie geloggt.
enum ABSKeychain {
    static let service = "de.forell.shelfcast.audiobookshelf"
    static let account = "apiKey"

    private static func query(_ service: String) -> [String: Any] {
        [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service, kSecAttrAccount as String: account]
    }

    static func load(service: String = service) -> String? {
        var q = query(service)
        q[kSecReturnData as String] = true
        q[kSecMatchLimit as String] = kSecMatchLimitOne
        var out: CFTypeRef?
        guard SecItemCopyMatching(q as CFDictionary, &out) == errSecSuccess, let d = out as? Data else { return nil }
        return String(data: d, encoding: .utf8)
    }

    @discardableResult
    static func save(_ key: String, service: String = service) -> Bool {
        let data = Data(key.utf8)
        if SecItemUpdate(query(service) as CFDictionary, [kSecValueData as String: data] as CFDictionary) == errSecSuccess { return true }
        var q = query(service)
        q[kSecValueData as String] = data
        return SecItemAdd(q as CFDictionary, nil) == errSecSuccess
    }

    static func delete() { SecItemDelete(query(service) as CFDictionary) }
}

// MARK: - Client

struct ABSLibrary: Identifiable, Hashable { let id: String; let name: String }

struct ABSItem: Identifiable {
    let id: String
    let title: String
    let author: String
    let format: String        // "epub", "pdf", … (leer = unbekannt)
    var addedAt: Double = 0   // ms-Zeitstempel (LibraryItem.addedAt)
    var authorLF = ""         // "Nachname, Vorname" (authorNameLF), nur zum Sortieren
    var sortAuthor: String { authorLF.isEmpty ? author : authorLF }
    var isEpub: Bool { format.lowercased() == "epub" }
    /// Kurzer Grund, falls nicht konvertierbar
    var disabledReason: String? {
        isEpub ? nil : (format.isEmpty ? "Format unbekannt" : "Nur EPUB möglich (\(format.uppercased()))")
    }
}

/// Lesestand eines Buchs (aus /api/me -> mediaProgress)
struct ABSProgress: Equatable {
    var finished = false
    var fraction = 0.0            // 0...1; E-Book-Fortschritt, sonst Hörbuch-Fortschritt
    var lastUpdate = 0.0
    var percent: Int { Int((fraction * 100).rounded()) }
    enum State { case unread, reading, read }
    var state: State { finished ? .read : (fraction > 0 ? .reading : .unread) }
    /// Sortierwert: ungelesen 0 < liest (Anteil) < gelesen 2
    var sortValue: Double { finished ? 2 : fraction }

    /// Parst `mediaProgress` aus /api/me; nur Bücher (episodeId == null), pro Item der jüngste Eintrag.
    static func parse(me: [String: Any]) -> [String: ABSProgress] {
        var out: [String: ABSProgress] = [:]
        for e in (me["mediaProgress"] as? [[String: Any]]) ?? [] {
            guard let id = e["libraryItemId"] as? String, !id.isEmpty, e["episodeId"] is NSNull || e["episodeId"] == nil else { continue }
            func num(_ k: String) -> Double? { (e[k] as? NSNumber).flatMap { $0 is Bool ? nil : $0.doubleValue } }
            let eb = num("ebookProgress") ?? 0, au = num("progress") ?? 0
            let p = ABSProgress(finished: (e["isFinished"] as? Bool) ?? false,
                                fraction: min(1, max(0, eb > 0 ? eb : au)),
                                lastUpdate: num("lastUpdate") ?? 0)
            if let old = out[id], old.lastUpdate > p.lastUpdate { continue }
            out[id] = p
        }
        return out
    }
}

struct ABSError: LocalizedError {
    let message: String
    var errorDescription: String? { message }
}

/// Kleiner Audiobookshelf-REST-Client (Bearer-Auth).
struct ABSClient: Sendable {
    let base: URL
    let key: String

    /// "server:13378" -> URL; ohne Schema: http für IP/lokale Namen, sonst https.
    static func normalize(_ raw: String) -> URL? {
        var s = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        while s.hasSuffix("/") { s.removeLast() }
        guard !s.isEmpty else { return nil }
        if !s.contains("://") {
            let host = s.split(separator: ":").first.map(String.init) ?? s
            let local = !host.contains(".") || host.hasSuffix(".local") || host.hasSuffix(".lan")
                || host.allSatisfy { $0.isNumber || $0 == "." }
            s = (local ? "http://" : "https://") + s
        }
        guard let u = URL(string: s), let h = u.host, !h.isEmpty, ["http", "https"].contains(u.scheme?.lowercased()) else { return nil }
        return u
    }

    /// Base64 des Filterwerts "ebook" (ABS erwartet base64, dann URL-codiert)
    static var ebookFilter: String {
        "ebooks." + (Data("ebook".utf8).base64EncodedString().addingPercentEncoding(withAllowedCharacters: .alphanumerics) ?? "")
    }

    private func request(_ path: String, query: String? = nil) -> URLRequest {
        var c = URLComponents(url: base, resolvingAgainstBaseURL: false)!
        c.path = (c.path.hasSuffix("/") ? String(c.path.dropLast()) : c.path) + path
        c.percentEncodedQuery = query
        var r = URLRequest(url: c.url!)
        r.setValue("Bearer \(key)", forHTTPHeaderField: "Authorization")
        r.timeoutInterval = 30
        return r
    }

    private func fail(_ e: Error) -> ABSError {
        if let e = e as? ABSError { return e }
        switch (e as? URLError)?.code {
        case .cannotFindHost, .dnsLookupFailed: return ABSError(message: "Server nicht gefunden. Prüfe die Adresse.")
        case .cannotConnectToHost, .timedOut, .networkConnectionLost, .notConnectedToInternet:
            return ABSError(message: "Server nicht erreichbar. Prüfe Adresse, Port und Netzwerk.")
        case .secureConnectionFailed, .serverCertificateUntrusted, .serverCertificateHasBadDate, .serverCertificateHasUnknownRoot:
            return ABSError(message: "Sichere Verbindung fehlgeschlagen. Zertifikat prüfen oder http:// verwenden.")
        default: return ABSError(message: "Verbindungsfehler: \(e.localizedDescription)")
        }
    }

    private func check(_ resp: URLResponse) throws {
        guard let h = resp as? HTTPURLResponse else { throw ABSError(message: "Ungültige Antwort vom Server.") }
        switch h.statusCode {
        case 200..<300: return
        case 401: throw ABSError(message: "API-Key ungültig (401). Prüfe den Key in den ABS-Einstellungen.")
        case 403: throw ABSError(message: "Zugriff verweigert (403).")
        case 404: throw ABSError(message: "Nicht gefunden (404). Ist das ein Audiobookshelf-Server?")
        default: throw ABSError(message: "Serverfehler (HTTP \(h.statusCode)).")
        }
    }

    private func json(_ r: URLRequest) async throws -> [String: Any] {
        do {
            let (d, resp) = try await URLSession.shared.data(for: r)
            try check(resp)
            guard let j = try? JSONSerialization.jsonObject(with: d) as? [String: Any] else {
                throw ABSError(message: "Antwort nicht erkannt – das ist kein Audiobookshelf-Server.")
            }
            return j
        } catch { throw fail(error) }
    }

    /// Bibliotheken vom Typ "book"; dient zugleich als Verbindungstest.
    func libraries() async throws -> [ABSLibrary] {
        let j = try await json(request("/api/libraries"))
        guard let arr = j["libraries"] as? [[String: Any]] else {
            throw ABSError(message: "Antwort nicht erkannt – das ist kein Audiobookshelf-Server.")
        }
        return arr.compactMap { l in
            guard l["mediaType"] as? String == "book", let id = l["id"] as? String else { return nil }
            return ABSLibrary(id: id, name: (l["name"] as? String) ?? id)
        }
    }

    /// Lesestand des Benutzers pro Item; Fehler -> leer (kein Status), nie ein Abbruch.
    func progress() async -> [String: ABSProgress] {
        guard let j = try? await json(request("/api/me")) else { return [:] }
        return ABSProgress.parse(me: j)
    }

    /// Alle Items mit E-Book, seitenweise (limit/page, page beginnt bei 0).
    func items(library: String) async throws -> [ABSItem] {
        var out: [ABSItem] = []
        let limit = 200
        var page = 0
        while true {
            let q = "filter=\(Self.ebookFilter)&sort=media.metadata.title&minified=1&limit=\(limit)&page=\(page)"
            let j = try await json(request("/api/libraries/\(library)/items", query: q))
            guard let res = j["results"] as? [[String: Any]] else { throw ABSError(message: "Antwort nicht erkannt.") }
            for r in res {
                guard let id = r["id"] as? String else { continue }
                let media = r["media"] as? [String: Any] ?? [:]
                let meta = media["metadata"] as? [String: Any] ?? [:]
                out.append(ABSItem(id: id, title: (meta["title"] as? String) ?? "Ohne Titel",
                                   author: (meta["authorName"] as? String) ?? "",
                                   format: (media["ebookFormat"] as? String) ?? "",
                                   addedAt: (r["addedAt"] as? Double) ?? 0,
                                   authorLF: (meta["authorNameLF"] as? String) ?? ""))
            }
            let total = (j["total"] as? Int) ?? out.count
            if res.count < limit || out.count >= total { break }
            page += 1
        }
        return out
    }

    func cover(_ id: String) async -> NSImage? {
        guard let (d, resp) = try? await URLSession.shared.data(for: request("/api/items/\(id)/cover", query: "width=160")),
              (resp as? HTTPURLResponse)?.statusCode == 200 else { return nil }
        return NSImage(data: d)
    }

    /// Lädt das E-Book nach `dest` (über eine Temp-Datei, damit nie eine halbe Datei liegen bleibt).
    func download(_ id: String, to dest: URL) async throws {
        do {
            let (tmp, resp) = try await URLSession.shared.download(for: request("/api/items/\(id)/ebook"))
            defer { try? FileManager.default.removeItem(at: tmp) }
            try check(resp)
            // EPUB ist ein ZIP ("PK")
            guard let h = try? FileHandle(forReadingFrom: tmp), (try? h.read(upToCount: 2)) == Data("PK".utf8) else {
                throw ABSError(message: "Heruntergeladene Datei ist kein EPUB.")
            }
            try? h.close()
            let fm = FileManager.default
            try fm.createDirectory(at: dest.deletingLastPathComponent(), withIntermediateDirectories: true)
            try? fm.removeItem(at: dest)
            try fm.moveItem(at: tmp, to: dest)
        } catch { throw fail(error) }
    }
}

// MARK: - Zustand

@MainActor
final class ABSStore: ObservableObject {
    static let shared = ABSStore()
    private let urlKey = "absServerURL", libKey = "absLibraryID"
    private let sortKey = "absSort", ascKey = "absSortAsc", filterKey = "absFilter", readFilterKey = "absReadFilter"

    @Published var connected = false
    @Published var serverURL: String
    @Published var libraries: [ABSLibrary] = []
    @Published var libraryID: String { didSet { UserDefaults.standard.set(libraryID, forKey: libKey) } }
    @Published var items: [ABSItem] = []
    @Published var progress: [String: ABSProgress] = [:]
    @Published var search = ""
    @Published var sort: ABSSort { didSet { UserDefaults.standard.set(sort.rawValue, forKey: sortKey) } }
    @Published var ascending: Bool { didSet { UserDefaults.standard.set(ascending, forKey: ascKey) } }
    @Published var filter: ABSFilter { didSet { UserDefaults.standard.set(filter.rawValue, forKey: filterKey) } }
    @Published var readFilter: ABSReadFilter { didSet { UserDefaults.standard.set(readFilter.rawValue, forKey: readFilterKey) } }
    let transfers = TransferLog.shared
    private var transfersSub: AnyCancellable?
    @Published var selection: Set<String> = []
    @Published var confirmReset = false               // Rückfrage "Status zurücksetzen"
    @Published var downloading: Set<String> = []
    @Published var itemErrors: [String: String] = [:]
    @Published var loading = false
    @Published var error = ""
    private var client: ABSClient?
    @Published var covers: [String: NSImage] = [:]   // Cover-Cache im Speicher
    @Published var sending = false                    // Download-Phase von "An Picco senden" läuft
    @Published var sendDone = 0
    @Published var sendTotal = 0
    @Published var draftKey = ""                      // Eingabe im Login-Formular, nur bis zum Verbinden

    init() {
        serverURL = UserDefaults.standard.string(forKey: urlKey) ?? ""
        libraryID = UserDefaults.standard.string(forKey: libKey) ?? ""
        let ud = UserDefaults.standard
        sort = ud.string(forKey: sortKey).flatMap(ABSSort.init(rawValue:)) ?? .title
        ascending = ud.object(forKey: ascKey) as? Bool ?? true
        filter = ud.string(forKey: filterKey).flatMap(ABSFilter.init(rawValue:)) ?? .all
        readFilter = ud.string(forKey: readFilterKey).flatMap(ABSReadFilter.init(rawValue:)) ?? .all
        // Änderungen am Protokoll (auch aus Model) aktualisieren die Liste live
        transfersSub = transfers.objectWillChange.sink { [weak self] _ in self?.objectWillChange.send() }
        if let k = ABSKeychain.load(), let u = ABSClient.normalize(serverURL) {
            client = ABSClient(base: u, key: k)
            connected = true   // optimistisch; Fehler zeigt sich beim Laden
        }
    }

    static var cacheRoot: URL {
        FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("de.forell.shelfcast/abs", isDirectory: true)
    }
    var cacheDir: URL { Self.cacheRoot }

    /// Item-ID eines ABS-Downloads (nil für andere Dateien)
    static func itemID(for url: URL) -> String? { TransferLog.itemID(for: url, cacheRoot: cacheRoot) }

    var visible: [ABSItem] {
        absArrange(items, search: search, filter: filter, sort: sort, ascending: ascending,
                   transferDate: { self.transfers.record(for: $0)?.date },
                   reading: readFilter, progress: { self.progress[$0] })
    }

    /// Rechtsklick: gilt für alle angehakten Bücher, wenn die Zeile angehakt ist, sonst nur für sie.
    func targets(for id: String) -> [ABSItem] {
        let ids = selection.contains(id) ? selection : [id]
        return items.filter { ids.contains($0.id) }
    }

    func markTransferred(_ list: [ABSItem]) {
        for i in list where transfers.record(for: i.id) == nil { transfers.mark(i.id, via: "manuell", title: i.title) }
    }

    func markNotTransferred(_ list: [ABSItem]) { transfers.unmark(Set(list.map { $0.id })) }

    /// Verbindung testen und bei Erfolg speichern.
    func connect(url: String, key: String) async {
        let k = key.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let u = ABSClient.normalize(url) else { error = "Ungültige Server-Adresse."; return }
        guard !k.isEmpty else { error = "Bitte einen API-Key eingeben."; return }
        loading = true; error = ""
        defer { loading = false }
        let c = ABSClient(base: u, key: k)
        do {
            let libs = try await c.libraries()
            guard !libs.isEmpty else { error = "Keine Bücher-Bibliothek auf dem Server gefunden."; return }
            guard ABSKeychain.save(k) else { error = "API-Key konnte nicht im Schlüsselbund gespeichert werden."; return }
            UserDefaults.standard.set(u.absoluteString, forKey: urlKey)
            serverURL = u.absoluteString
            client = c; connected = true; draftKey = ""
            apply(libs)
            await loadItems()
        } catch { self.error = error.localizedDescription }
    }

    func disconnect() {
        ABSKeychain.delete()
        client = nil; connected = false
        libraries = []; items = []; progress = [:]; selection = []; itemErrors = [:]; search = ""; error = ""
        covers = [:]
    }

    private func apply(_ libs: [ABSLibrary]) {
        libraries = libs
        if !libs.contains(where: { $0.id == libraryID }) { libraryID = libs.first?.id ?? "" }
    }

    /// Beim Öffnen des Tabs: Bibliotheken und Items laden.
    func refresh() async {
        guard let c = client else { return }
        loading = true; error = ""
        defer { loading = false }
        do {
            apply(try await c.libraries())
            items = try await c.items(library: libraryID)
            progress = await c.progress()
            selection = selection.filter { id in items.contains { $0.id == id } }
        } catch { self.error = error.localizedDescription }
    }

    func loadItems() async {
        guard let c = client, !libraryID.isEmpty else { return }
        loading = true; error = ""
        defer { loading = false }
        do { items = try await c.items(library: libraryID); progress = await c.progress(); selection = [] }
        catch { self.error = error.localizedDescription }
    }

    func loadCover(_ id: String) async {
        guard covers[id] == nil, let i = await client?.cover(id) else { return }
        covers[id] = i
    }

    /// Dateiname aus dem Titel; Unterordner pro Item verhindert Kollisionen.
    private func destination(_ item: ABSItem) -> URL {
        var name = item.title.components(separatedBy: CharacterSet(charactersIn: "/\\:\0").union(.controlCharacters)).joined(separator: " ")
        name = name.trimmingCharacters(in: .whitespaces).trimmingCharacters(in: CharacterSet(charactersIn: "."))
        if name.isEmpty { name = "Buch" }
        return cacheDir.appendingPathComponent(item.id, isDirectory: true)
            .appendingPathComponent(String(name.prefix(120)) + ".epub")
    }

    /// Angehakte, ladbare EPUBs
    var pickedEpubs: [ABSItem] { items.filter { selection.contains($0.id) && $0.isEpub && !downloading.contains($0.id) } }

    /// Lädt die angehakten Bücher nacheinander in den Cache. `onFile` meldet jede fertige Datei sofort
    /// (Model nimmt sie in seine Liste). Fortschritt in sendDone/sendTotal; Fehler stehen in itemErrors.
    func downloadSelected(onFile: (ABSItem, URL) -> Void) async {
        let todo = pickedEpubs
        guard let c = client, !todo.isEmpty, !sending else { return }
        sending = true; sendDone = 0; sendTotal = todo.count
        for it in todo {
            let dest = destination(it)
            itemErrors[it.id] = nil
            downloading.insert(it.id)
            do {
                try await c.download(it.id, to: dest)
                selection.remove(it.id)
                onFile(it, dest)
            } catch { itemErrors[it.id] = error.localizedDescription }
            downloading.remove(it.id)
            sendDone += 1
        }
        sending = false
    }
}
