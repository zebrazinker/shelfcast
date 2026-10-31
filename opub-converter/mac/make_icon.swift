import AppKit

// Shelfcast: Buecherregal, dessen letzter Buchruecken zum E-Reader wird,
// plus Sende-Chevron und Punkte. Flach, ohne Verlaeufe, in Petrol-Toenen.
// Zeichnet in einem 1024er-Koordinatensystem (Ursprung unten links) und skaliert.
func hex(_ h: UInt32, _ a: CGFloat = 1) -> CGColor {
    CGColor(colorSpace: CGColorSpaceCreateDeviceRGB(), components: [CGFloat((h >> 16) & 255) / 255, CGFloat((h >> 8) & 255) / 255, CGFloat(h & 255) / 255, a])!
}
func rr(_ r: CGRect, _ rad: CGFloat) -> CGPath { CGPath(roundedRect: r, cornerWidth: rad, cornerHeight: rad, transform: nil) }
func squircle(_ r: CGRect, n: CGFloat = 5) -> CGPath {
    let p = CGMutablePath(); let a = r.width / 2, b = r.height / 2
    for i in 0...360 {
        let t = CGFloat(i) * .pi / 180
        let c = cos(t), s = sin(t)
        let x = r.midX + a * (c < 0 ? -1 : 1) * pow(abs(c), 2 / n)
        let y = r.midY + b * (s < 0 ? -1 : 1) * pow(abs(s), 2 / n)
        i == 0 ? p.move(to: CGPoint(x: x, y: y)) : p.addLine(to: CGPoint(x: x, y: y))
    }
    p.closeSubpath(); return p
}
func fill(_ c: CGContext, _ p: CGPath, _ col: CGColor) { c.addPath(p); c.setFillColor(col); c.fillPath() }
func line(_ c: CGContext, _ a: CGPoint, _ b: CGPoint, _ w: CGFloat, _ col: CGColor) {
    c.move(to: a); c.addLine(to: b); c.setStrokeColor(col); c.setLineWidth(w); c.setLineCap(.round); c.strokePath()
}

func draw(_ c: CGContext, small: Bool) {
    let base = CGRect(x: 100, y: 100, width: 824, height: 824)
    c.saveGState(); c.addPath(squircle(base)); c.clip()
    c.setFillColor(hex(0x10303A)); c.fill(base)
    // Regalbrett
    fill(c, rr(CGRect(x: 160, y: 250, width: 640, height: 22), 11), hex(0x17414E))
    // Buchruecken
    fill(c, rr(CGRect(x: 190, y: 272, width: 96, height: 340), 22), hex(0x1F5463))
    fill(c, rr(CGRect(x: 300, y: 272, width: 88, height: 420), 22), hex(0x2B6C7E))
    c.saveGState(); c.translateBy(x: 400, y: 272); c.rotate(by: -0.10)
    fill(c, rr(CGRect(x: 0, y: 0, width: 80, height: 390), 20), hex(0x3A869A)); c.restoreGState()
    // E-Reader (vierter Ruecken)
    let dev = CGRect(x: 505, y: 272, width: 190, height: 380)
    fill(c, rr(dev, 36), hex(0xD4E6EA))
    fill(c, rr(dev.insetBy(dx: 18, dy: 18), 20), hex(0xEAF4F5))
    for i in 0..<(small ? 3 : 5) {
        let y = dev.maxY - 62 - CGFloat(i) * 54
        line(c, CGPoint(x: dev.minX + 44, y: y), CGPoint(x: dev.maxX - 44 - (i == 4 ? 40 : 0), y: y), small ? 14 : 8, hex(0x7FAAB8))
    }
    // Sende-Chevron und Punkte
    c.setLineJoin(.round)
    c.move(to: CGPoint(x: 735, y: 650)); c.addLine(to: CGPoint(x: 800, y: 715)); c.addLine(to: CGPoint(x: 735, y: 780))
    c.setStrokeColor(hex(0x9CC4CF)); c.setLineWidth(small ? 36 : 26); c.setLineCap(.round); c.strokePath()
    for (x, y, d) in [(820.0, 640.0, 34.0), (850.0, 770.0, 24.0), (775.0, 830.0, 16.0)] {
        fill(c, CGPath(ellipseIn: CGRect(x: x, y: y, width: d, height: d), transform: nil), hex(0x7FAAB8))
    }
    c.restoreGState()
}

let dir = CommandLine.arguments[1]
for (s, n) in [(16,"16x16"),(32,"16x16@2x"),(32,"32x32"),(64,"32x32@2x"),(128,"128x128"),(256,"128x128@2x"),(256,"256x256"),(512,"256x256@2x"),(512,"512x512"),(1024,"512x512@2x")] {
    let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: s, pixelsHigh: s, bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
    let ctx = NSGraphicsContext(bitmapImageRep: rep)!
    ctx.cgContext.interpolationQuality = .high
    ctx.cgContext.scaleBy(x: CGFloat(s) / 1024, y: CGFloat(s) / 1024)
    draw(ctx.cgContext, small: s <= 64)
    try! rep.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: "\(dir)/icon_\(n).png"))
}
