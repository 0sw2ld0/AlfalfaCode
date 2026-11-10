@testable import AlfalfaKit
import Foundation
import WebKit
import XCTest

@MainActor
final class BrowserProfileTests: XCTestCase {

    func testDefaultIsolatedBrowsersDoNotShareCookies() async throws {
        try await withFixture { fixture in
            XCTAssertEqual(fixture.profiles.mode(forProject: fixture.project), .isolated)
            XCTAssertFalse(fixture.profiles.hasProfile(forProject: fixture.project))
            let first = fixture.profiles.dataStore(forProject: fixture.project)
            let second = fixture.profiles.dataStore(forProject: fixture.project)
            XCTAssertFalse(first.isPersistent)
            XCTAssertFalse(second.isPersistent)
            XCTAssertFalse(first === second)
            await self.setCookie("aislada", in: first)
            let own = await self.cookies(in: first)
            let other = await self.cookies(in: second)
            XCTAssertTrue(own.contains { $0.name == "sesion" && $0.value == "aislada" })
            XCTAssertFalse(other.contains { $0.name == "sesion" })
        }
    }

    func testSavedBrowsersShareOnlyTheirProjectCookies() async throws {
        try await withFixture { fixture in
            try fixture.profiles.setMode(.project, forProject: fixture.project)
            try fixture.profiles.setMode(.project, forProject: fixture.otherProject)
            let first = BrowserEngine(dataStore: fixture.profiles.dataStore(forProject: fixture.project))
            let second = BrowserEngine(dataStore: fixture.profiles.dataStore(forProject: fixture.project))
            let other = BrowserEngine(dataStore: fixture.profiles.dataStore(forProject: fixture.otherProject))
            defer { first.close(); second.close(); other.close() }
            let store = first.webView.configuration.websiteDataStore
            XCTAssertEqual(first.profileMode, .project)
            XCTAssertNotNil(store.identifier)
            XCTAssertEqual(store.identifier, second.webView.configuration.websiteDataStore.identifier)
            XCTAssertNotEqual(store.identifier, other.webView.configuration.websiteDataStore.identifier)
            await self.setCookie("proyecto-a", in: store)
            let shared = await self.cookies(in: second.webView.configuration.websiteDataStore)
            let separate = await self.cookies(in: other.webView.configuration.websiteDataStore)
            XCTAssertTrue(shared.contains { $0.value == "proyecto-a" })
            XCTAssertFalse(separate.contains { $0.name == "sesion" })
            let state = await second.perform(BrowserRequest(id: "perfil", action: "estado"))
            XCTAssertTrue(state.ok, state.json)
            XCTAssertEqual(state.result["perfil"] as? String, "proyecto")
        }
    }

    func testPreferenceSurvivesRecreationAndSymlinksUseTheSameProject() async throws {
        try await withFixture { fixture in
            try fixture.profiles.setMode(.project, forProject: fixture.project)
            let store = fixture.profiles.dataStore(forProject: fixture.project)
            let alias = fixture.root.appendingPathComponent("alias")
            try FileManager.default.createSymbolicLink(at: alias, withDestinationURL: fixture.project)
            let restored = BrowserProfiles(defaults: fixture.defaults)
            XCTAssertEqual(restored.mode(forProject: alias), .project)
            XCTAssertEqual(restored.dataStore(forProject: alias).identifier, store.identifier)
            XCTAssertEqual(restored.mode(forProject: fixture.otherProject), .isolated)
            try restored.setMode(.isolated, forProject: fixture.project)
            XCTAssertFalse(restored.dataStore(forProject: fixture.project).isPersistent)
            try restored.setMode(.project, forProject: fixture.project)
            XCTAssertEqual(restored.dataStore(forProject: fixture.project).identifier, store.identifier)
        }
    }

    func testClearRemovesOnlyTheChosenProfileAndOldViewsCannotRefillIt() async throws {
        try await withFixture { fixture in
            try fixture.profiles.setMode(.project, forProject: fixture.project)
            try fixture.profiles.setMode(.project, forProject: fixture.otherProject)
            let old = fixture.profiles.dataStore(forProject: fixture.project)
            let other = fixture.profiles.dataStore(forProject: fixture.otherProject)
            let firstView = BrowserEngine(dataStore: old)
            let secondView = BrowserEngine(dataStore: old)
            defer { firstView.close(); secondView.close() }
            await self.setCookie("borrar-a", in: old)
            await self.setCookie("conservar-b", in: other)
            // Incluso si otra vista antigua sigue viva, sus escrituras no repueblan el perfil nuevo.
            try await fixture.profiles.clearData(forProject: fixture.project)
            let fresh = fixture.profiles.dataStore(forProject: fixture.project)
            XCTAssertNotEqual(old.identifier, fresh.identifier)
            XCTAssertEqual(fixture.profiles.mode(forProject: fixture.project), .project)
            let erased = await self.cookies(in: old)
            let kept = await self.cookies(in: other)
            XCTAssertFalse(erased.contains { $0.name == "sesion" })
            XCTAssertTrue(kept.contains { $0.value == "conservar-b" })
            await self.setCookie("vista-antigua", in: old)
            let clean = await self.cookies(in: fresh)
            XCTAssertFalse(clean.contains { $0.name == "sesion" })
            await self.erase(old)
        }
    }

    func testMalformedPreferencesFailClosedWithoutCreatingTheZeroStore() async throws {
        try await withFixture { fixture in
            let key = try XCTUnwrap(BrowserProfiles.projectKey(for: fixture.project))
            let data = try JSONSerialization.data(withJSONObject: [key: [
                "identifier": "00000000-0000-0000-0000-000000000000", "saved": true]])
            fixture.defaults.set(data, forKey: BrowserProfiles.Key.profiles)
            XCTAssertEqual(fixture.profiles.mode(forProject: fixture.project), .isolated)
            XCTAssertFalse(fixture.profiles.dataStore(forProject: fixture.project).isPersistent)
            try fixture.profiles.setMode(.project, forProject: fixture.project)
            XCTAssertNotEqual(fixture.profiles.dataStore(forProject: fixture.project).identifier?.uuidString,
                              "00000000-0000-0000-0000-000000000000")
            let url = try XCTUnwrap(URL(string: "https://pagina.test/"))
            XCTAssertThrowsError(try fixture.profiles.setMode(.project, forProject: url))
            XCTAssertFalse(fixture.profiles.dataStore(forProject: url).isPersistent)
        }
    }

    // MARK: - Métodos privados

    private func withFixture(_ operation: @MainActor (ProfileFixture) async throws -> Void) async throws {
        let fixture = try ProfileFixture()
        do {
            try await operation(fixture)
            await fixture.cleanUp()
        } catch {
            await fixture.cleanUp()
            throw error
        }
    }

    private func setCookie(_ value: String, in store: WKWebsiteDataStore) async {
        guard let cookie = HTTPCookie(properties: [.name: "sesion", .value: value, .domain: "perfil.alfalfa.test",
                                                   .path: "/", .expires: Date().addingTimeInterval(3600)]) else {
            return XCTFail("No se pudo crear la cookie ficticia.")
        }
        await withCheckedContinuation { continuation in
            store.httpCookieStore.setCookie(cookie) { continuation.resume() }
        }
    }

    private func cookies(in store: WKWebsiteDataStore) async -> [HTTPCookie] {
        await withCheckedContinuation { continuation in
            store.httpCookieStore.getAllCookies { continuation.resume(returning: $0) }
        }
    }

    private func erase(_ store: WKWebsiteDataStore) async {
        await withCheckedContinuation { continuation in
            store.removeData(ofTypes: WKWebsiteDataStore.allWebsiteDataTypes(), modifiedSince: .distantPast) {
                continuation.resume()
            }
        }
    }
}

@MainActor
private final class ProfileFixture {
    let root: URL
    let project: URL
    let otherProject: URL
    let suite: String
    let defaults: UserDefaults
    let profiles: BrowserProfiles

    init() throws {
        root = FileManager.default.temporaryDirectory.appendingPathComponent("perfiles-a6-\(UUID().uuidString)")
        project = root.appendingPathComponent("proyecto-a")
        otherProject = root.appendingPathComponent("proyecto-b")
        try FileManager.default.createDirectory(at: project, withIntermediateDirectories: true)
        try FileManager.default.createDirectory(at: otherProject, withIntermediateDirectories: true)
        suite = "alfalfa.perfiles.pruebas.\(UUID().uuidString)"
        defaults = try XCTUnwrap(UserDefaults(suiteName: suite))
        profiles = BrowserProfiles(defaults: defaults)
    }

    func cleanUp() async {
        try? await profiles.clearData(forProject: project)
        try? await profiles.clearData(forProject: otherProject)
        defaults.removePersistentDomain(forName: suite)
        try? FileManager.default.removeItem(at: root)
    }
}
