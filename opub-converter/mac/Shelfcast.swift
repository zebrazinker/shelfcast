import SwiftUI
import AppKit
import UniformTypeIdentifiers

// MARK: - Modell

struct Book: Identifiable {
    enum Status { case idle, queued, running, sending(Int), done(String), failed(String) }
    let id = UUID()
    let url: URL
    var title: String
    var author = ""
    var size: Int64
    var cover: NSImage?
    var status: Status = .idle
}

func resourcePath(_ name: String) -> String {
    Bundle.main.resourcePath.map { $0 + "/" + name } ?? name
}

func runPython(_ script: String, _ args: [String]) -> Data {
    let p = Process()
    p.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
    p.arguments = ["-I", resourcePath(script)] + args
    let pipe = Pipe()
    p.standardOutput = pipe
    p.standardError = FileHandle.nullDevice
    do { try p.run() } catch { return Data() }
    let d = pipe.fileHandleForReading.readDataToEndOfFile()  // vor wait lesen (Pipe-Puffer)
    p.waitUntilExit()
    return d
}

/// Gleiche Regel wie droplet_helper.find_device_books: erstes /Volumes/* mit schreibbarem Ordner "books".
func findDevice() -> (volume: String, books: String)? {
    let fm = FileManager.default
    guard let names = try? fm.contentsOfDirectory(atPath: "/Volumes") else { return nil }
    for n in names.sorted() {
        if n.hasPrefix(".") || n == "Macintosh HD" || n.lowercased().contains("recovery") { continue }
        let p = "/Volumes/" + n
        if URL(fileURLWithPath: p).resolvingSymlinksInPath().path == "/" { continue }
        guard let subs = try? fm.contentsOfDirectory(atPath: p) else { continue }
        for s in subs.sorted() where s.lowercased() == "books" {
            var isDir: ObjCBool = false
            let sp = p + "/" + s
            if fm.fileExists(atPath: sp, isDirectory: &isDir), isDir.boolValue, fm.isWritableFile(atPath: sp) {
                return (n, sp)
            }
        }
    }
    return nil
}

// MARK: - Zustand

@MainActor
final class Model: ObservableObject {
    static let shared = Model()
    @Published var books: [Book] = []
    @Published var running = false
    @Published var picking = false
    @Published var targeted = false
    @Published var summary = ""
    @Published var done = 0          // Fortschritt des laufenden Durchgangs
    @Published var total = 0
    @Published var current = ""
    @Published var device: (volume: String, books: String)? = findDevice()
    @Published var wifiDevices: [WifiDevice] = []
    @Published var wifiName: String?           // gewähltes Gerät, falls mehrere im Netz
    @Published var manualDest: Dest?           // Klick in der Seitenleiste; gilt, solange das Ziel verfügbar ist
    @Published var source: Source = .files
    @Published var fileSelection: Set<UUID> = []
    @Published var showSettings = false
    @Published var pendingDup: [URL] = []      // Duplikate, auf Rückfrage wartend
    private var observers: [NSObjectProtocol] = []
    private let browser = WifiBrowser()

    enum Source: Hashable { case files, abs }
    enum Dest { case usb, wifi, local }

    /// Das gewählte WLAN-Gerät, sonst das erste gefundene
    var wifiDevice: WifiDevice? { wifiDevices.first { $0.name == wifiName } ?? wifiDevices.first }

    func available(_ d: Dest) -> Bool {
        switch d {
        case .usb: return device != nil
        case .wifi: return wifiDevice != nil
        case .local: return true
        }
    }

    /// Manuelle Wahl, solange verfügbar; sonst USB vor WLAN vor Mac.
    var dest: Dest {
        if let d = manualDest, available(d) { return d }
        if device != nil { return .usb }
        return wifiDevice != nil ? .wifi : .local
    }

    var destLabel: String {
        switch dest {
        case .usb: return "Picco per USB"
        case .wifi: return "Picco per WLAN"
        case .local: return "Mac (Downloads/Boox)"
        }
    }

    init() {
        browser.start { [weak self] ds in Task { @MainActor in self?.wifiDevices = ds } }
        let c = NSWorkspace.shared.notificationCenter
        for n in [NSWorkspace.didMountNotification, NSWorkspace.didUnmountNotification, NSWorkspace.didRenameVolumeNotification] {
            observers.append(c.addObserver(forName: n, object: nil, queue: .main) { [weak self] _ in
                // Ordner erscheint evtl. kurz nach dem Mount
                Task { @MainActor in
                    self?.device = findDevice()
                    try? await Task.sleep(nanoseconds: 1_500_000_000)
                    self?.device = findDevice()
                }
            })
        }
    }

    var pending: Int {
        books.filter { b in switch b.status { case .idle, .failed: return true; default: return false } }.count
    }

    func remove(_ id: UUID) {
        if let b = books.first(where: { $0.id == id }) {
            switch b.status { case .queued, .running, .sending: return; default: break }
        }
        books.removeAll { $0.id == id }; fileSelection.remove(id); summary = ""
    }

    func remove(_ ids: Set<UUID>) { for id in ids { remove(id) } }

    /// ABS-Buch in der Liste (Cache-Pfad enthält die Item-ID)
    func book(forItem id: String) -> Book? { books.first { ABSStore.itemID(for: $0.url) == id } }

    /// Nimmt eine heruntergeladene ABS-Datei ohne Duplikat-Rückfrage auf und stellt sie in die Warteschlange.
    func enqueue(_ u: URL) -> UUID {
        if let b = books.first(where: { $0.url.path == u.path }) {
            if let i = books.firstIndex(where: { $0.id == b.id }) { books[i].status = .queued }
            return b.id
        }
        let id = append(u)
        if let i = books.firstIndex(where: { $0.id == id }) { books[i].status = .queued }
        return id
    }

    func clear() { books = []; fileSelection = []; summary = "" }

    func add(_ urls: [URL]) {
        let epubs = urls.filter { $0.pathExtension.lowercased() == "epub" }
        let known = Set(books.map { $0.url.path })
        var dups: [URL] = []
        for u in epubs { if known.contains(u.path) { dups.append(u) } else { append(u) } }
        if !dups.isEmpty { pendingDup = dups }
    }

    func resolveDup(addAnyway: Bool) {
        if addAnyway { for u in pendingDup { append(u) } }
        pendingDup = []
    }

    @discardableResult
    private func append(_ u: URL) -> UUID {
        let size = (try? u.resourceValues(forKeys: [.fileSizeKey]).fileSize).flatMap { Int64($0) } ?? 0
        let b = Book(url: u, title: u.deletingPathExtension().lastPathComponent, size: size)
        books.append(b)
        if !running { summary = "" }
        let id = b.id
        Task.detached {
            let d = runPython("bookinfo.py", [u.path])
            let j = (try? JSONSerialization.jsonObject(with: d)) as? [String: Any] ?? [:]
            var img: NSImage?
            if let s = j["cover"] as? String, let raw = Data(base64Encoded: s) { img = NSImage(data: raw) }
            let cov = img
            let t = (j["title"] as? String) ?? "", a = (j["author"] as? String) ?? ""
            await MainActor.run {
                guard let i = self.books.firstIndex(where: { $0.id == id }) else { return }
                if !t.isEmpty { self.books[i].title = t }
                self.books[i].author = a
                self.books[i].cover = cov
            }
        }
        return id
    }

    func convert() { Task { await convertAsync() } }

    /// Eins-Schritt-Versand aus Audiobookshelf: laden → in die Liste → konvertieren/senden.
    /// Die Bücher erscheinen danach auch unter "Dateien" (bis "Liste leeren").
    func sendABS(_ abs: ABSStore) async {
        guard !running, !abs.sending else { return }
        var ids = Set<UUID>()
        await abs.downloadSelected { _, url in ids.insert(self.enqueue(url)) }
        summary = ""
        if !ids.isEmpty { await convertAsync(only: ids) }
        abs.sendTotal = 0
    }

    /// `only`: nur diese Bücher (auch bereits eingereihte); sonst alle offenen der Liste.
    func convertAsync(only: Set<UUID>? = nil) async {
        guard !running else { return }
        let todo: [(UUID, URL)] = books.compactMap { b in
            if let only { return only.contains(b.id) ? (b.id, b.url) : nil }
            switch b.status { case .idle, .failed: return (b.id, b.url); default: return nil }
        }
        guard !todo.isEmpty else { return }
        running = true; summary = ""; done = 0; total = todo.count; current = ""
        let dest = self.dest, usb = device, wifi = wifiDevice
        let local = NSString("~/Downloads/Boox").expandingTildeInPath
        let ids = todo
        await Task.detached {
            if dest == .wifi, let w = wifi {
                // Vor dem Durchgang prüfen, ob der Picco antwortet
                if !(await wifiPing(w.endpoint)) {
                    await MainActor.run {
                        // Schon eingereihte (ABS-)Bücher wieder freigeben
                        for (id, _) in ids {
                            if let i = self.books.firstIndex(where: { $0.id == id }), case .queued = self.books[i].status { self.books[i].status = .idle }
                        }
                        self.summary = wifiUnreachable; self.running = false
                    }
                    return
                }
            }
            await MainActor.run {
                for (id, _) in ids { if let i = self.books.firstIndex(where: { $0.id == id }) { self.books[i].status = .queued } }
            }
            var ok = 0, bad = 0
            for (id, url) in ids {
                // erst .running setzen (auf dem MainActor abwarten), dann Python starten
                let present = await MainActor.run { () -> Bool in
                    guard let i = self.books.firstIndex(where: { $0.id == id }) else { return false }  // zwischenzeitlich entfernt
                    self.books[i].status = .running; self.current = self.books[i].title
                    return true
                }
                if !present { await MainActor.run { self.total -= 1 }; continue }
                let st: Book.Status
                if dest == .wifi, let w = wifi {
                    st = await self.sendViaWifi(id, url, w)
                } else {
                    let target = dest == .usb ? usb?.books : nil
                    let d = runPython("droplet_helper.py", ["--one", url.path, target ?? local, target != nil ? "device" : "local"])
                    let j = (try? JSONSerialization.jsonObject(with: d)) as? [String: Any] ?? [:]
                    if j["ok"] as? Bool == true, let p = j["path"] as? String { st = .done(p) }
                    else { st = .failed((j["error"] as? String) ?? "Unbekannter Fehler") }
                }
                if case .done = st { ok += 1 } else { bad += 1 }
                await MainActor.run {
                    if let i = self.books.firstIndex(where: { $0.id == id }) {
                        self.books[i].status = st
                        // Auf dem Picco gelandet (USB/WLAN, nicht Downloads/Boox) und aus ABS: merken
                        if case .done = st, dest != .local, let item = ABSStore.itemID(for: url) {
                            TransferLog.shared.mark(item, via: dest == .wifi ? "WLAN" : "USB", title: self.books[i].title)
                        }
                    }
                    self.done += 1
                }
            }
            let where_: String
            switch dest {
            case .usb: where_ = "\(usb?.volume ?? "")/books"
            case .wifi: where_ = "Picco (WLAN)"
            case .local: where_ = "Downloads/Boox"
            }
            let msg = bad == 0 ? "Fertig: \(ok) Buch/Bücher nach \(where_) \(dest == .wifi ? "gesendet" : "kopiert")."
                               : "\(ok) konvertiert, \(bad) fehlgeschlagen (Ziel: \(where_))."
            await MainActor.run { self.summary = msg; self.running = false; self.current = "" }
        }.value
    }

    /// Konvertiert in ein Temp-Verzeichnis, sendet per WLAN und räumt danach auf.
    nonisolated private func sendViaWifi(_ id: UUID, _ url: URL, _ w: WifiDevice) async -> Book.Status {
        let tmp = NSTemporaryDirectory() + "BooxPicco-" + UUID().uuidString
        defer { try? FileManager.default.removeItem(atPath: tmp) }
        let d = runPython("droplet_helper.py", ["--one", url.path, tmp, "local"])
        let j = (try? JSONSerialization.jsonObject(with: d)) as? [String: Any] ?? [:]
        guard j["ok"] as? Bool == true, let p = j["path"] as? String else {
            return .failed((j["error"] as? String) ?? "Unbekannter Fehler")
        }
        await MainActor.run { if let i = self.books.firstIndex(where: { $0.id == id }) { self.books[i].status = .sending(0) } }
        let last = LastPercent()
        do {
            try await wifiUpload(w.endpoint, file: URL(fileURLWithPath: p)) { f in
                let pct = Int(f * 100)
                guard last.set(pct) else { return }
                Task { @MainActor in
                    if let i = self.books.firstIndex(where: { $0.id == id }), case .sending = self.books[i].status {
                        self.books[i].status = .sending(pct)
                    }
                }
            }
            return .done("An Picco gesendet")
        } catch let e as WifiError {
            return .failed(e.message)
        } catch {
            return .failed(wifiUnreachable)
        }
    }
}

/// Merkt den zuletzt gemeldeten Prozentwert (verhindert Update-Flut).
final class LastPercent: @unchecked Sendable {
    private var v = -1
    private let l = NSLock()
    func set(_ n: Int) -> Bool { l.lock(); defer { l.unlock() }; if n == v { return false }; v = n; return true }
}

// MARK: - App

/// Erscheinungsbild der App (UserDefaults-Schlüssel `appearance`), wirkt app-weit über NSApp.appearance.
enum AppearanceMode: String, CaseIterable, Identifiable {
    case system, light, dark
    var id: String { rawValue }
    var label: String { switch self { case .system: "System"; case .light: "Hell"; case .dark: "Dunkel" } }
    var nsAppearance: NSAppearance? {
        switch self { case .system: nil; case .light: NSAppearance(named: .aqua); case .dark: NSAppearance(named: .darkAqua) }
    }
}

@MainActor
final class AppearanceStore: ObservableObject {
    static let shared = AppearanceStore()
    @Published var mode: AppearanceMode {
        didSet { UserDefaults.standard.set(mode.rawValue, forKey: "appearance"); apply() }
    }
    private init() { mode = AppearanceMode(rawValue: UserDefaults.standard.string(forKey: "appearance") ?? "") ?? .system }
    func apply() { NSApp.appearance = mode.nsAppearance }
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationWillFinishLaunching(_ notification: Notification) {
        MainActor.assumeIsolated { AppearanceStore.shared.apply() }
    }
    func application(_ application: NSApplication, open urls: [URL]) {
        Task { @MainActor in Model.shared.source = .files; Model.shared.add(urls) }
    }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
}

@main
struct ShelfcastApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) var delegate
    @StateObject private var model = Model.shared
    init() { Migration.runOnce() }   // vor dem ersten Zugriff auf ABSStore/TransferLog
    var body: some Scene {
        Window("Shelfcast", id: "main") {
            ContentView().environmentObject(model).environmentObject(ABSStore.shared)
        }
    }
}
