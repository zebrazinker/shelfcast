import Foundation
import Network

// WLAN-Übertragung zum Boox Picco ("Lokale Netzwerkübertragung"):
// Bonjour-Suche (_nanoreader._tcp) + minimales HTTP/1.1 über NWConnection.
// NWConnection mit requiredInterfaceType (.wifi, Ersatz .wiredEthernet) umgeht ein VPN (utun),
// das das Heimnetz-Subnetz sonst in den Tunnel routen würde.

struct WifiDevice: Equatable {
    let name: String
    let endpoint: NWEndpoint
}

struct WifiError: Error { let message: String }

/// Live-Suche nach dem Picco im lokalen Netz.
final class WifiBrowser {
    private let browser = NWBrowser(for: .bonjour(type: "_nanoreader._tcp", domain: "local."), using: .tcp)

    func start(_ onChange: @escaping @Sendable ([WifiDevice]) -> Void) {
        browser.browseResultsChangedHandler = { results, _ in
            let all = results.compactMap { r -> WifiDevice? in
                if case let .service(name, _, _, _) = r.endpoint { return WifiDevice(name: name, endpoint: r.endpoint) }
                return nil
            }.sorted { $0.name < $1.name }
            onChange(all)
        }
        browser.start(queue: .main)
    }
}

private final class Flag: @unchecked Sendable { var v = false }

/// Eine einzelne Verbindung mit async-Wrappern.
private final class Conn {
    let c: NWConnection
    let q = DispatchQueue(label: "wifi.conn")
    init(_ endpoint: NWEndpoint, _ iface: NWInterface.InterfaceType) {
        let p = NWParameters.tcp
        p.requiredInterfaceType = iface
        c = NWConnection(to: endpoint, using: p)
    }

    func open(timeout: Double) async throws {
        try await withCheckedThrowingContinuation { (k: CheckedContinuation<Void, Error>) in
            let f = Flag()
            c.stateUpdateHandler = { s in
                guard !f.v else { return }
                switch s {
                case .ready: f.v = true; k.resume()
                case .failed(let e): f.v = true; k.resume(throwing: e)
                case .cancelled: f.v = true; k.resume(throwing: WifiError(message: "Zeitüberschreitung"))
                default: break   // .waiting (z. B. Netz nicht erreichbar): Timeout entscheidet
                }
            }
            c.start(queue: q)
            q.asyncAfter(deadline: .now() + timeout) { [c] in if !f.v { c.cancel() } }
        }
    }

    func send(_ d: Data) async throws {
        try await withCheckedThrowingContinuation { (k: CheckedContinuation<Void, Error>) in
            c.send(content: d, completion: .contentProcessed { e in
                if let e { k.resume(throwing: e) } else { k.resume() }
            })
        }
    }

    private func chunk() async throws -> (Data, Bool) {
        try await withCheckedThrowingContinuation { (k: CheckedContinuation<(Data, Bool), Error>) in
            c.receive(minimumIncompleteLength: 1, maximumLength: 65536) { d, _, done, e in
                if let e { k.resume(throwing: e) } else { k.resume(returning: (d ?? Data(), done)) }
            }
        }
    }

    /// Liest die Antwort bis Verbindungsende bzw. Content-Length (HEAD: nur Kopf).
    func response(head: Bool) async throws -> (status: Int, body: String) {
        var buf = Data()
        let sep = Data("\r\n\r\n".utf8)
        while true {
            let (d, done) = try await chunk()
            buf.append(d)
            if let r = buf.range(of: sep) {
                let header = String(decoding: buf[..<r.lowerBound], as: UTF8.self)
                let lines = header.components(separatedBy: "\r\n")
                let status = Int(lines[0].split(separator: " ").dropFirst().first ?? "") ?? 0
                var len: Int?
                for l in lines.dropFirst() where l.lowercased().hasPrefix("content-length:") {
                    len = Int(l.dropFirst(15).trimmingCharacters(in: .whitespaces))
                }
                let body = buf[r.upperBound...]
                if head || done || (len != nil && body.count >= len!) {
                    return (status, String(decoding: body, as: UTF8.self))
                }
            } else if done { throw WifiError(message: "Ungültige Antwort vom Picco") }
        }
    }

    func close() { c.cancel() }
}

let wifiUnreachable = "Picco nicht erreichbar – ist „Lokale Netzwerkübertragung“ geöffnet? Läuft ein VPN?"

/// Verbindet erst über WLAN, dann über Ethernet; öffnet nie über ein VPN-Interface.
private func connect(_ ep: NWEndpoint, timeout: Double) async throws -> Conn {
    for iface in [NWInterface.InterfaceType.wifi, .wiredEthernet] {
        let c = Conn(ep, iface)
        do { try await c.open(timeout: timeout); return c } catch { c.close() }
    }
    throw WifiError(message: wifiUnreachable)
}

/// HEAD /ping → 200
func wifiPing(_ ep: NWEndpoint) async -> Bool {
    let c: Conn
    do { c = try await connect(ep, timeout: 6) } catch { return false }
    defer { c.close() }
    let req = "HEAD /ping HTTP/1.1\r\nHost: picco.local\r\nConnection: close\r\n\r\n"
    do {
        try await c.send(Data(req.utf8))
        return try await c.response(head: true).status == 200
    } catch { return false }
}

/// POST /upload?fileType=0 (multipart, Feld "file"). Kein "Expect: 100-continue" (Gerät antwortet sonst 408).
/// progress liefert 0…1 (auf beliebigem Thread).
func wifiUpload(_ ep: NWEndpoint, file: URL, progress: @escaping @Sendable (Double) -> Void) async throws {
    let data = try Data(contentsOf: file, options: .mappedIfSafe)
    let boundary = "----BooxPicco" + UUID().uuidString
    let name = file.lastPathComponent.replacingOccurrences(of: "\"", with: "%22")
    let pre = Data("--\(boundary)\r\nContent-Disposition: form-data; name=\"file\"; filename=\"\(name)\"\r\nContent-Type: application/octet-stream\r\n\r\n".utf8)
    let post = Data("\r\n--\(boundary)--\r\n".utf8)
    let head = "POST /upload?fileType=0 HTTP/1.1\r\nHost: picco.local\r\nContent-Type: multipart/form-data; boundary=\(boundary)\r\nContent-Length: \(pre.count + data.count + post.count)\r\nConnection: close\r\n\r\n"

    let c = try await connect(ep, timeout: 8)
    defer { c.close() }
    try await c.send(Data(head.utf8) + pre)
    let step = 256 * 1024
    var off = 0
    while off < data.count {
        let end = min(off + step, data.count)
        try await c.send(data.subdata(in: off..<end))
        off = end
        progress(Double(off) / Double(max(data.count, 1)))
    }
    try await c.send(post)
    let r = try await c.response(head: false)
    guard r.status == 200 else {
        throw WifiError(message: "Picco meldet Fehler \(r.status)" + (r.body.isEmpty ? "" : ": \(r.body.prefix(80))"))
    }
}
