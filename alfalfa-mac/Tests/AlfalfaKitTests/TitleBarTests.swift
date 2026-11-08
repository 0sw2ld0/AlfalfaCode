import XCTest
@testable import AlfalfaKit

final class TitleBarTests: XCTestCase {
    func testFollowsTheSystemSetting() {
        XCTAssertEqual(TitleBarDoubleClick.action(globalDefaults: [:]), .zoom)
        XCTAssertEqual(TitleBarDoubleClick.action(globalDefaults: ["AppleActionOnDoubleClick": "Maximize"]), .zoom)
        XCTAssertEqual(TitleBarDoubleClick.action(globalDefaults: ["AppleActionOnDoubleClick": "Fill"]), .zoom)
        XCTAssertEqual(TitleBarDoubleClick.action(globalDefaults: ["AppleActionOnDoubleClick": "Minimize"]), .minimize)
        XCTAssertEqual(TitleBarDoubleClick.action(globalDefaults: ["AppleActionOnDoubleClick": "None"]), .nothing)
        XCTAssertEqual(TitleBarDoubleClick.action(globalDefaults: ["AppleMiniaturizeOnDoubleClick": true]), .minimize)
    }
}
