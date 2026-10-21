//
//  NanoMuseDeviceReply.swift
//  nanoMuse
//
//  The reply of a task running on another device (C7, NanoMuseDeviceMention), drawn while
//  that device works: "Mac is working…" with the typing indicator under it, each step as a
//  capsule (running, then done or failed), what that Muse says as it says it, the answer in
//  place at the end. The steps are the hub's `event` frames of the `task` call (docs/hub.md).
//  A `text` replaces the text written since the last step, so a device that sends the text of
//  a message as it grows is shown as it grows. While it runs the chat is busy (`isProcessing`):
//  the composer shows stop, and stop ends the task on that device with `stop {call}`.
//

import Foundation

@MainActor
final class NanoMuseDeviceReply {
    enum Completion {
        case answered
        case failed(String)
        case stopped
    }

    let device: HubDevice
    /// The `task` call's frame id: what `stop {call}` names.
    let callId = NanoMuseHub.newCallId()
    /// The person pressed stop here.
    private(set) var stopped = false
    private var finished = false
    private let vm: AIChatViewModel
    private let message: ChatMessage
    /// "Mac is working…", then "Mac answered:" (removed when there is no answer).
    private let header: AssistantBlock

    /// The chats with a device task running, by view model: what stop reaches.
    private static var running: [ObjectIdentifier: NanoMuseDeviceReply] = [:]

    init(device: HubDevice, vm: AIChatViewModel) {
        self.device = device
        self.vm = vm
        header = AssistantBlock(kind: .text, content: "")
        message = ChatMessage(role: .assistant, content: "", blocks: [header])
        message.isAwaitingModelResponse = true
        vm.messages.append(message)
        vm.isProcessing = true
        Self.running[ObjectIdentifier(vm)] = self
        setText(header, String(format: AppLocalized("%@ is working…"), device.name), final: false)
        vm.publishUnlessTransitioning()
        vm.scrollToBottomSignal.send()
    }

    /// Stop was pressed in this chat (`AIChatViewModel.cancel`): the task ends on that device
    /// too, and the reply here ends now instead of when that device answers.
    static func stop(in vm: AIChatViewModel) {
        guard let reply = running[ObjectIdentifier(vm)], !reply.finished else { return }
        reply.stopped = true
        let to = reply.device.id, call = reply.callId
        Task { @MainActor in _ = try? await NanoMuseHub.shared.call(to: to, action: "stop", args: ["call": call], timeout: 30) }
        NanoMuseHub.shared.abandon(call)
    }

    /// One `event` of the task: a step starting (`tool`), a step done (`tool_result`), what that
    /// Muse says (`text`). Approvals are answered by NanoMuseDeviceMention; other stages are skipped.
    func apply(_ body: [String: Any]) {
        guard !finished, let stage = body["stage"] as? String else { return }
        switch stage {
        case "tool":
            let name = (body["name"] as? String) ?? ""
            let summary = ((body["summary"] as? String) ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
            let block = AssistantBlock(kind: Self.kind(of: name, summary), content: "", toolStatus: .running, toolUseId: stepId(body["id"]))
            block.toolSummary = summary.isEmpty ? name : (name.isEmpty ? summary : "\(name): \(summary)")
            block.toolStartTime = Date()
            append(block)
        case "tool_result":
            guard let id = stepId(body["id"]), let block = message.blocks.last(where: { $0.toolUseId == id }) else { return }
            let summary = (body["summary"] as? String) ?? ""
            block.content = summary
            block.toolStatus = ((body["ok"] as? Bool) ?? true) ? .success : .failed(message: summary)
            if let start = block.toolStartTime { block.toolDuration = Date().timeIntervalSince(start) }
            vm.publishUnlessTransitioning()
        case "text":
            guard let text = body["text"] as? String, !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return }
            setText(textSinceLastStep() ?? append(AssistantBlock(kind: .text, content: "")), text, final: false)
        default:
            break
        }
    }

    /// The task is over. `header` nil drops the header line (a failure, a stop); `replacing` puts
    /// `answer` over the text since the last step (that text is the answer as it was coming in).
    func finish(header title: String?, answer: String, replacing: Bool, completion: Completion) async {
        guard !finished else { return }
        finished = true
        if Self.running[ObjectIdentifier(vm)] === self { Self.running[ObjectIdentifier(vm)] = nil }
        Self.finishSteps(message.blocks, completion: stopped ? .stopped : completion)
        message.isAwaitingModelResponse = false
        if let title {
            setText(header, title, final: true)
        } else {
            message.blocks.removeAll { $0 === header }
        }
        let target = replacing ? textSinceLastStep() : nil
        setText(target ?? append(AssistantBlock(kind: .text, content: "")), answer, final: true)
        for block in message.blocks where block.kind == .text && block.cachedAttributedString == nil {
            setText(block, block.content, final: true)
        }
        vm.publishUnlessTransitioning()
        vm.scrollToBottomSignal.send()
        let record = message.blocks.filter { $0.kind == .text && !$0.content.isEmpty }.map(\.content).joined(separator: "\n\n")
        await vm.nmRecord([AgentMessage(role: .assistant, parts: [.text(record)])])
        // A stop has ended the turn already (and `cancel` sent what was queued on), and a new
        // turn may be running by now.
        guard !stopped else { return }
        vm.isProcessing = false
        sendQueued()
    }

    static func finishSteps(_ blocks: [AssistantBlock], completion: Completion) {
        for block in blocks where block.toolStatus == .running {
            switch completion {
            case .failed(let reason):
                block.toolStatus = .failed(message: reason)
            case .answered, .stopped:
                block.toolStatus = .cancelled
            }
            if let start = block.toolStartTime { block.toolDuration = Date().timeIntervalSince(start) }
        }
    }

    /// What was queued while that device worked goes now, as if sent: an `@device` line is a task
    /// again, anything else a model turn, which drains the rest of the queue when it ends. What
    /// the person is typing meanwhile stays in the composer.
    private func sendQueued() {
        guard !vm.isProcessing, !vm.promptQueue.isEmpty else { return }
        let next = vm.promptQueue.removeFirst()
        vm.messages.removeAll { $0.queuedPromptId == next.id }
        let draft = vm.inputText, draftAttachments = vm.attachments
        vm.inputText = next.text
        vm.attachments = next.attachments
        vm.send()
        if vm.inputText.isEmpty, vm.attachments.isEmpty {
            vm.inputText = draft
            vm.attachments = draftAttachments
        }
    }

    // MARK: Blocks

    /// The text block after the last step, when the reply ends in one (never the header).
    private func textSinceLastStep() -> AssistantBlock? {
        guard let last = message.blocks.last, last !== header, last.kind == .text else { return nil }
        return last
    }

    @discardableResult
    private func append(_ block: AssistantBlock) -> AssistantBlock {
        message.blocks.append(block)
        vm.publishUnlessTransitioning()
        vm.scrollToBottomSignal.send()
        return block
    }

    /// Through the stream's own path, so the cell resizes and the markdown is parsed as for a model turn.
    private func setText(_ block: AssistantBlock, _ text: String, final: Bool) {
        guard let m = vm.messages.firstIndex(where: { $0 === message }),
              let b = message.blocks.firstIndex(where: { $0 === block }) else {
            block.content = text
            return
        }
        vm.setStreamingTextBlockContent(
            msgIdx: m, blockIdx: b, text: text,
            parsedMarkdown: MarkdownContent(prepareMarkdownForRender(text)),
            cacheAttributedString: final, requestScroll: true
        )
    }

    /// The step's id on this reply's blocks; the device's ids are only unique on that device.
    private func stepId(_ raw: Any?) -> String? {
        guard let raw else { return nil }
        return "\(device.id):\(raw)"
    }

    /// The capsule a step is drawn as, from the tool's name on that device.
    nonisolated static func kind(of name: String, _ summary: String) -> AssistantBlockKind {
        let n = name.lowercased()
        if n.contains("read") { return .fileReadTool(path: summary) }
        if n.contains("write") { return .fileWriteTool(path: summary) }
        if n.contains("edit") || n.contains("patch") { return .fileEditTool(path: summary) }
        if n.contains("browser") || n.contains("web") || n.contains("fetch") || n.contains("search") { return .browserTool(action: summary) }
        return .shellTool(command: summary)
    }
}
