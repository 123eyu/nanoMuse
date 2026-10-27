//
//  NanoMuseGateEvents.swift
//  nanoMuse
//
//  The phone's gate on what the agent may do is upstream's permission check for the `apple-*`
//  commands (HealthKit, Calendar, Photos, Location and the rest: Bypass, Ask Once, Not Allowed
//  under Settings, Permissions). What it decides, and how the person answered its sheet, goes
//  to the account's record as codes (relay 0.31, docs/cloud.md "Client events"): one
//  `sentinel.decision` per check (the command, its class, the risk, the decision and what it
//  rested on) and one `approval.result` when a sheet was shown (the answer, whether it holds
//  for the chat, who gave it, how long it took). Never the command's arguments.
//
//  `checkPermission` keeps upstream's name and signature so the tool loop is unchanged; the
//  check itself is upstream's `checkPermissionBare`. Android: guard/RiskGate.kt (the risk gate
//  there sends the same two kinds).
//

import Combine
import Foundation

extension OffloadPermissionManager {
    /// Upstream's check, with the record told what came of it.
    func checkPermission(for command: String, sessionId: String?, fullCommand: String = "") async -> PermissionResult {
        let level = permissionLevel(for: command)
        let started = Date()
        let seen = NanoMuseGateSeen()
        let watch = $pendingRequest.sink { request in
            if let request, request.commandName == command { seen.asked = true }
        }
        let result = await checkPermissionBare(for: command, sessionId: sessionId, fullCommand: fullCommand)
        watch.cancel()
        NanoMuseGateEvents.report(session: sessionId, command: command, level: level, asked: seen.asked, result: result, waited: Date().timeIntervalSince(started))
        return result
    }
}

/// Whether the sheet came up during one check (set from the publisher, read after the await).
private final class NanoMuseGateSeen {
    var asked = false
}

@MainActor
enum NanoMuseGateEvents {
    /// Upstream's sheet waits this long for an answer before it counts as a no.
    static let sheetTimeout: TimeInterval = 30

    /// The command's class for the record: upstream's category, as a lower-case word.
    static func classOf(_ command: String) -> String {
        (OffloadPermissionManager.allCommands.first { $0.name == command }?.category.rawValue ?? "system").lowercased()
    }

    /// The risk word for a class: personal data is `sensitive`, media `moderate`, the rest `safe`.
    static func riskOf(category word: String) -> String {
        switch word {
        case "privacy": return "sensitive"
        case "media": return "moderate"
        default: return "safe"
        }
    }

    /// The two events of one check, as docs/cloud.md lists them.
    static func events(command: String, level: OffloadPermissionLevel, asked: Bool, allowed: Bool, waited: TimeInterval) -> [(kind: String, data: [String: Any?])] {
        let word = classOf(command)
        let risk = riskOf(category: word)
        var decision: [String: Any?] = ["tool": command, "class": word, "risk": risk]
        switch level {
        case .bypass:
            decision["decision"] = "allow"
            decision["by"] = "policy"
            decision["policy"] = "bypass"
            return [("sentinel.decision", decision)]
        case .notAllowed:
            decision["decision"] = "deny"
            decision["by"] = "policy"
            decision["policy"] = "not_allowed"
            return [("sentinel.decision", decision)]
        case .askOnce:
            guard asked else {
                // the sheet was answered earlier in this chat: the grant covered it
                decision["decision"] = "allow"
                decision["by"] = "grant"
                decision["grant"] = "conversation"
                return [("sentinel.decision", decision)]
            }
            decision["decision"] = "ask"
            let by: String
            if allowed {
                by = "person"
            } else if waited >= sheetTimeout - 0.5 {
                by = "timeout"
            } else if waited < 0.3 {
                by = "auto" // declined by the app itself (a hub task while the app was not on screen)
            } else {
                by = "person"
            }
            var answer: [String: Any?] = ["tool": command, "risk": risk, "allow": allowed, "by": by, "waited_s": Int(waited.rounded())]
            if allowed { answer["scope"] = "conversation" }
            return [("sentinel.decision", decision), ("approval.result", answer)]
        }
    }

    static func report(session: String?, command: String, level: OffloadPermissionLevel, asked: Bool, result: PermissionResult, waited: TimeInterval) {
        let allowed: Bool
        if case .allowed = result { allowed = true } else { allowed = false }
        for e in events(command: command, level: level, asked: asked, allowed: allowed, waited: waited) {
            NanoMuseClientEvents.add(session: session, kind: e.kind, data: e.data)
        }
    }
}
