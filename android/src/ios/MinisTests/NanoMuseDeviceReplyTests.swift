import XCTest
@testable import Minis

@MainActor
final class NanoMuseDeviceReplyTests: XCTestCase {
    func testTimeoutFailsUnfinishedSteps() {
        assertInterruptedStepsFail(code: "timeout")
    }

    func testDisconnectFailsUnfinishedSteps() {
        assertInterruptedStepsFail(code: "disconnected")
        assertInterruptedStepsFail(code: "closed")
    }

    func testStopCancelsOnlyUnfinishedSteps() {
        let unfinished = step(status: .running)
        let completed = step(status: .success)
        let failed = step(status: .failed(message: "tool failed"))
        NanoMuseDeviceReply.finishSteps([unfinished, completed, failed], completion: .stopped)
        XCTAssertEqual(unfinished.toolStatus, .cancelled)
        XCTAssertNotNil(unfinished.toolDuration)
        XCTAssertEqual(completed.toolStatus, .success)
        XCTAssertEqual(failed.toolStatus, .failed(message: "tool failed"))
    }

    func testAnswerDoesNotInventSuccessForUnconfirmedSteps() {
        let unfinished = step(status: .running)
        let completed = step(status: .success)
        NanoMuseDeviceReply.finishSteps([unfinished, completed], completion: .answered)
        XCTAssertEqual(unfinished.toolStatus, .cancelled)
        XCTAssertEqual(completed.toolStatus, .success)
    }

    private func assertInterruptedStepsFail(code: String) {
        let reason = NanoMuseHubErrors.describe(HubError(code: code, message: "transport failure"))
        let unfinished = step(status: .running)
        let completed = step(status: .success)
        let failed = step(status: .failed(message: "tool failed"))
        let cancelled = step(status: .cancelled)
        NanoMuseDeviceReply.finishSteps(
            [unfinished, completed, failed, cancelled], completion: .failed(reason)
        )
        XCTAssertEqual(unfinished.toolStatus, .failed(message: reason))
        XCTAssertNotNil(unfinished.toolDuration)
        XCTAssertEqual(completed.toolStatus, .success)
        XCTAssertEqual(failed.toolStatus, .failed(message: "tool failed"))
        XCTAssertEqual(cancelled.toolStatus, .cancelled)
    }

    private func step(status: ToolBlockStatus) -> AssistantBlock {
        let block = AssistantBlock(kind: .shellTool(command: "pwd"), content: "", toolStatus: status)
        block.toolStartTime = Date().addingTimeInterval(-1)
        return block
    }
}
