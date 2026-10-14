//
//  NanoMuseDeviceMention.swift
//  nanoMuse
//
//  Contract C7, rule 8: a message that starts with "@<device name>" is work
//  for that device. The iOS agent has no `delegate` tool of its own, so the
//  task goes straight to the device's Muse over the hub (`task`), with the
//  mention removed, and its answer is shown here as the reply. A step that
//  Muse must ask about comes back as an `approval` event and is answered
//  here, on the card (NanoMuseRemoteApproval.swift). Device names come from
//  the hub's list; the match is a case-insensitive prefix, the longest name
//  first.
//

import Foundation

enum NanoMuseDeviceMention {
    struct Hit {
        let device: HubDevice
        /// The message without the mention; empty when the mention was all there was.
        let task: String
    }

    /// "@Pixel 8 open the calendar" → Pixel 8, "open the calendar".
    @MainActor
    static func parse(_ text: String) -> Hit? {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard trimmed.hasPrefix("@") else { return nil }
        let rest = String(trimmed.dropFirst())
        guard !rest.isEmpty else { return nil }
        let devices = NanoMuseHub.shared.others
            .filter { !$0.name.trimmingCharacters(in: .whitespaces).isEmpty }
            .sorted { $0.name.count > $1.name.count }
        for device in devices {
            guard let range = rest.range(of: device.name, options: [.caseInsensitive, .anchored]) else { continue }
            // "@Mac" must not take "@Macbook …": the name ends at a word boundary.
            if range.upperBound < rest.endIndex, rest[range.upperBound].isLetter || rest[range.upperBound].isNumber { continue }
            var task = String(rest[range.upperBound...])
            while let first = task.first, first == ":" || first == "," || first == "，" || first == "：" || first.isWhitespace {
                task.removeFirst()
            }
            return Hit(device: device, task: task.trimmingCharacters(in: .whitespacesAndNewlines))
        }
        return nil
    }

    /// Runs the mention when there is one: the person's line goes into the chat, the device's
    /// answer follows when it comes. Returns false when the text does not address a device.
    @MainActor
    static func handle(_ text: String, in vm: AIChatViewModel) -> Bool {
        guard let hit = parse(text) else { return false }
        let device = hit.device
        let task = hit.task.isEmpty ? text : hit.task
        vm.nmLocalTurn(user: text)
        guard device.online else {
            vm.nmLocalTurn(assistant: String(format: AppLocalized("%@ is not online right now."), device.name))
            return true
        }
        let notes = Notes()
        let sessionId = vm.sessionId ?? vm.draftId ?? "draft"
        Task { @MainActor in
            do {
                let reply = try await NanoMuseHub.shared.call(
                    to: device.id,
                    action: "task",
                    // `language` (runtime 1.0.0, docs/hub.md): the Muse over there answers in
                    // this phone's language instead of guessing from the text; older runtimes ignore it.
                    args: ["text": task, "from": NanoMuseHub.shared.name, "language": NanoMuseLocale.tag],
                    timeout: 600
                ) { body in
                    guard let stage = body["stage"] as? String, let id = body["approval_id"] as? String else { return }
                    switch stage {
                    case "approval":
                        // That Muse asks whether it may do something risky. The question is answered
                        // here, where the task was typed (docs/hub.md): the card, or a remembered answer.
                        guard let question = NanoMuseRemoteQuestion(body: body) else { return }
                        Task { @MainActor in
                            let outcome = await NanoMuseRemoteApprovals.shared.decide(question, from: device, sessionId: sessionId)
                            switch outcome {
                            case .allowed(let standing): if standing { notes.standing += 1 }
                            case .denied: notes.declined += 1
                            case .settledElsewhere: break
                            }
                            guard let allow = outcome.allow else { return }
                            _ = try? await NanoMuseHub.shared.call(to: device.id, action: "approve", args: ["approval_id": id, "allow": allow], timeout: 30)
                        }
                    case "approval_result":
                        // Decided at the other device, or expired there: the card here goes.
                        Task { @MainActor in NanoMuseRemoteApprovals.shared.settled(id) }
                    default:
                        break
                    }
                }
                var answer = (reply["text"] as? String) ?? (reply["answer"] as? String) ?? ""
                if answer.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { answer = AppLocalized("Done") }
                if notes.declined > 0 {
                    answer += "\n\n" + String(format: AppLocalized("%@ asked for permission for a step and did not get it from here."), device.name)
                }
                if notes.standing > 0 {
                    answer += "\n\n" + AppLocalized("A step ran on a standing approval given on this phone; Approvals on the agent's page lists it.")
                }
                vm.nmLocalTurn(assistant: String(format: AppLocalized("%@ answered:"), device.name) + "\n\n" + answer)
            } catch {
                let reason = NanoMuseHubErrors.describe(error)
                vm.nmLocalTurn(assistant: String(format: AppLocalized("%@ did not answer: %@"), device.name, reason))
            }
        }
        return true
    }

    /// What the approvals of one task came to, for the lines under the answer.
    @MainActor
    private final class Notes {
        var declined = 0
        var standing = 0
    }
}
