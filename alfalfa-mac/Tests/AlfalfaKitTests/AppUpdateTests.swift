import XCTest
@testable import AlfalfaKit

final class AppUpdateTests: XCTestCase {

    private let dmg = URL(string: "https://github.com/0sw2ld0/AlfalfaCode/releases/download/v0.99.2/AlfalfaCode-0.99.2.dmg")!

    func testDecodesVersionJSON() throws {
        let json = #"{"version":"0.99.2","url":"https://github.com/0sw2ld0/AlfalfaCode/releases/download/v0.99.2/AlfalfaCode-0.99.2.dmg","sha256":"ab","tamano":58000000,"fecha":"2026-11-09","notas":["Uno"]}"#
        let info = try JSONDecoder().decode(ReleaseInfo.self, from: Data(json.utf8))
        XCTAssertEqual(info, ReleaseInfo(version: "0.99.2", url: dmg, sha256: "ab", tamano: 58_000_000,
                                         fecha: "2026-11-09", notas: ["Uno"]))
    }

    func testVersionsCompareAsNumbers() {
        XCTAssertTrue(AppUpdatePolicy.isNewer("0.10.0", than: "0.9.9"))
        XCTAssertTrue(AppUpdatePolicy.isNewer("1.0", than: "0.99.1"))
        XCTAssertFalse(AppUpdatePolicy.isNewer("0.99.1", than: "0.99.1"))
        XCTAssertFalse(AppUpdatePolicy.isNewer("0.99.1", than: "0.99.1.0"))
        XCTAssertFalse(AppUpdatePolicy.isNewer("0.98.9", than: "0.99.0"))
    }

    /// Sólo un DMG de las releases de 0sw2ld0/AlfalfaCode por https.
    func testOnlyTrustsAlfalfaCodeReleases() {
        XCTAssertTrue(AppUpdatePolicy.isTrustedDownload(dmg))
        let others = ["http://github.com/0sw2ld0/AlfalfaCode/releases/download/v1/A.dmg",
                      "https://github.com/otro/AlfalfaCode/releases/download/v1/A.dmg",
                      "https://github.com.evil.example/0sw2ld0/AlfalfaCode/releases/download/v1/A.dmg",
                      "https://github.com/0sw2ld0/AlfalfaCode/releases/download/v1/A.zip",
                      "file:///tmp/A.dmg"]
        for text in others { XCTAssertFalse(AppUpdatePolicy.isTrustedDownload(URL(string: text)!), text) }
    }

    /// Con un feed local (prueba del circuito), el DMG también es local, y nunca uno de internet.
    func testLocalFeedOnlyForLocalFiles() {
        let feed = AppUpdatePolicy.feed(environment: [AppUpdatePolicy.feedOverrideKey: "file:///tmp/version.json"])
        XCTAssertTrue(feed.isFileURL)
        XCTAssertTrue(AppUpdatePolicy.isTrustedDownload(URL(fileURLWithPath: "/tmp/A.dmg"), feed: feed))
        XCTAssertFalse(AppUpdatePolicy.isTrustedDownload(dmg, feed: feed))
        // Un feed de internet distinto del de GitHub no se acepta como sustituto.
        XCTAssertEqual(AppUpdatePolicy.feed(environment: [AppUpdatePolicy.feedOverrideKey: "https://evil.example/v.json"]),
                       AppUpdatePolicy.feed)
    }

    func testOffersOnlyNewerAndRespectsSkippedVersionWhenAutomatic() {
        let release = ReleaseInfo(version: "0.99.2", url: dmg, sha256: "ab")
        XCTAssertTrue(AppUpdatePolicy.shouldOffer(release, installed: "0.99.1", skipped: nil, manual: false))
        XCTAssertFalse(AppUpdatePolicy.shouldOffer(release, installed: "0.99.2", skipped: nil, manual: true))
        XCTAssertFalse(AppUpdatePolicy.shouldOffer(release, installed: "0.99.1", skipped: "0.99.2", manual: false))
        XCTAssertTrue(AppUpdatePolicy.shouldOffer(release, installed: "0.99.1", skipped: "0.99.2", manual: true))
        let elsewhere = ReleaseInfo(version: "9.0.0", url: URL(string: "https://evil.example/A.dmg")!, sha256: "ab")
        XCTAssertFalse(AppUpdatePolicy.shouldOffer(elsewhere, installed: "0.99.1", skipped: nil, manual: true))
    }

    func testChecksAtMostOncePer20Hours() {
        let now = Date()
        XCTAssertTrue(AppUpdatePolicy.isDue(lastCheck: nil, now: now))
        XCTAssertFalse(AppUpdatePolicy.isDue(lastCheck: now.addingTimeInterval(-3600), now: now))
        XCTAssertTrue(AppUpdatePolicy.isDue(lastCheck: now.addingTimeInterval(-21 * 3600), now: now))
    }

    func testSHA256OfFile() throws {
        let file = FileManager.default.temporaryDirectory.appendingPathComponent("sha-\(UUID().uuidString)")
        try Data("abc".utf8).write(to: file)
        addTeardownBlock { try? FileManager.default.removeItem(at: file) }
        XCTAssertEqual(try AppUpdatePolicy.sha256(of: file),
                       "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")
    }

    func testTeamComesFromItsOwnLine() {
        let ours = "Executable=/A.app/Contents/MacOS/A\nAuthority=Developer ID Application: X (FZ8TW9AWLE)\nTeamIdentifier=FZ8TW9AWLE\n"
        XCTAssertTrue(AppUpdatePolicy.signedByTeam(codesignDetails: ours))
        XCTAssertFalse(AppUpdatePolicy.signedByTeam(codesignDetails: "Signature=adhoc\nTeamIdentifier=not set\n"))
        // Que el identificador aparezca en otra línea (un nombre, una ruta) no basta.
        XCTAssertFalse(AppUpdatePolicy.signedByTeam(codesignDetails: "Executable=/TeamIdentifier=FZ8TW9AWLE/A\n"))
        XCTAssertFalse(AppUpdatePolicy.signedByTeam(codesignDetails: "TeamIdentifier=FZ8TW9AWLEX\n"))
    }

    func testDetectsAppTranslocation() {
        XCTAssertTrue(AppUpdatePolicy.isTranslocated(URL(fileURLWithPath:
            "/private/var/folders/x/T/AppTranslocation/6E4E/d/AlfalfaCode.app")))
        XCTAssertFalse(AppUpdatePolicy.isTranslocated(URL(fileURLWithPath: "/Applications/AlfalfaCode.app")))
    }

    func testKnowsWhenHomebrewInstalledIt() {
        let caskrooms: Set<String> = ["/opt/homebrew/Caskroom/alfalfacode"]
        XCTAssertTrue(AppUpdatePolicy.installedByHomebrew(environment: [:], exists: caskrooms.contains))
        XCTAssertFalse(AppUpdatePolicy.installedByHomebrew(environment: [:], exists: { _ in false }))
        let custom: Set<String> = ["/Users/ana/brew/Caskroom/alfalfacode"]
        XCTAssertTrue(AppUpdatePolicy.installedByHomebrew(environment: ["HOMEBREW_PREFIX": "/Users/ana/brew"],
                                                         exists: custom.contains))
        XCTAssertFalse(AppUpdatePolicy.installedByHomebrew(environment: [:], exists: custom.contains))
    }
}
