@testable import AlfalfaKit
import XCTest

/// Los enlaces de la app van a páginas de la web de AlfalfaCode, siempre por HTTPS.
final class LinksTests: XCTestCase {
    func testLinksArePagesOfTheWebsite() {
        for url in [Links.privacy, Links.cloud] {
            XCTAssertEqual(url.scheme, "https")
            XCTAssertEqual(url.host(), "cuycoders.dev")
            XCTAssertTrue(url.path().hasPrefix("/alfalfa/"))
        }
    }
}
