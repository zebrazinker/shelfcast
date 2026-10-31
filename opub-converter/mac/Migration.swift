import Foundation

// MARK: - Einmalige Übernahme der Daten aus der Vorgänger-App (Bundle-ID de.forell.epubzuboox)

enum Migration {
    // Alte Bezeichner (Vorgänger-App); nur hier noch vorhanden.
    static let oldDomain = "de.forell.epubzuboox"
    static let oldKeychainService = "de.forell.epubzuboox.audiobookshelf"
    static let doneKey = "migratedFromLegacyApp"
    /// Alle UserDefaults-Schlüssel der App (siehe ABSStore)
    static let defaultsKeys = ["absServerURL", "absLibraryID", "absSort", "absSortAsc", "absFilter"]

    /// Läuft genau einmal (Flag in den neuen UserDefaults), vor dem Laden der Stores.
    /// Kopiert, löscht nichts Altes; ohne Altdaten passiert nichts. Der API-Key wird nie geloggt.
    /// Der Cache wird nicht übernommen (Neu-Download genügt).
    static func runOnce(defaults: UserDefaults = .standard,
                        oldDomain: String = Migration.oldDomain,
                        supportDir: URL = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0],
                        oldSupportName: String = Migration.oldDomain,
                        newSupportName: String = "de.forell.shelfcast",
                        oldService: String = Migration.oldKeychainService,
                        newService: String = ABSKeychain.service) {
        guard !defaults.bool(forKey: doneKey) else { return }

        // 1. UserDefaults: nur fehlende Schlüssel übernehmen
        if let old = UserDefaults.standard.persistentDomain(forName: oldDomain) {
            for k in defaultsKeys where defaults.object(forKey: k) == nil {
                if let v = old[k] { defaults.set(v, forKey: k) }
            }
        }

        // 2. Übertragungsprotokoll kopieren (nicht verschieben)
        let fm = FileManager.default
        let oldFile = supportDir.appendingPathComponent("\(oldSupportName)/transfers.json")
        let newDir = supportDir.appendingPathComponent(newSupportName)
        let newFile = newDir.appendingPathComponent("transfers.json")
        if fm.fileExists(atPath: oldFile.path), !fm.fileExists(atPath: newFile.path) {
            try? fm.createDirectory(at: newDir, withIntermediateDirectories: true)
            try? fm.copyItem(at: oldFile, to: newFile)
        }

        // 3. API-Key im Schlüsselbund kopieren (alten Eintrag behalten)
        if ABSKeychain.load(service: newService) == nil, let k = ABSKeychain.load(service: oldService) {
            ABSKeychain.save(k, service: newService)
        }

        defaults.set(true, forKey: doneKey)
    }
}
