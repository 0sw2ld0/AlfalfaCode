import XCTest
@testable import AlfalfaKit

final class ReleasePolicyTests: XCTestCase {
    func testLeavingFreeSessionClosesNow() {
        XCTAssertEqual(ReleasePolicy.onLeave(status: .idle), .closeNow)
        XCTAssertEqual(ReleasePolicy.onLeave(status: .blocked), .closeNow)
    }

    func testLeavingWithPendingPermissionCancelsAndCloses() {
        XCTAssertEqual(ReleasePolicy.onLeave(status: .waitingPermission), .cancelAndClose)
    }

    func testPermissionArrivingAfterLeavingCancelsAndCloses() {
        XCTAssertEqual(ReleasePolicy.onUpdate(status: .waitingPermission), .cancelAndClose)
        XCTAssertEqual(ReleasePolicy.onUpdate(status: .idle), .closeNow)
        XCTAssertEqual(ReleasePolicy.onUpdate(status: .blocked), .closeNow)
        XCTAssertNil(ReleasePolicy.onUpdate(status: .working))
    }

    func testLeavingWhileWorkingClosesWhenTurnEnds() {
        XCTAssertEqual(ReleasePolicy.onLeave(status: .working), .closeWhenParked)
        XCTAssertFalse(ReleasePolicy.isParked(.working))
        XCTAssertTrue(ReleasePolicy.isParked(.blocked))
    }
}
