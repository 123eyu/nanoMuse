//
//  NanoMuseFeedback.swift
//  nanoMuse
//
//  The lightest feedback a chat can take, for the record (relay 0.31, docs/cloud.md "Client
//  events"): a thumb up or down under the newest reply, and one question after a Stop (why)
//  that the person may skip. Codes only travel: `turn.feedback {vote, reason?}`,
//  `turn.stopped {reason}`, `turn.ended {ended}`; never the reply or a word typed.
//
//  `AIChatViewModel.cancel()` calls `stopped` when the person presses Stop (the event goes at
//  once; the question is offered for a while, in the chat the Stop happened in, and only for a
//  turn the person started: a routine's or another device's turn gets no card). `nmAfterTurn`
//  calls `turnEnded` at every end, which reports a turn that ended in an error. The thumbs keep
//  what was voted per reply for the screen (`votes`). The two views sit in the cards host above
//  the composer (NanoMuseChatCardsHost). Android: ui/feedback/TurnFeedback.kt, FeedbackCards.kt;
//  desktop: src/client/Feedback.tsx.
//

import SwiftUI

@MainActor
final class NanoMuseFeedback: ObservableObject {
    static let shared = NanoMuseFeedback()

    /// The chat whose Stop is being asked about, and when it was pressed.
    struct StopAsk: Equatable {
        let sessionId: String
        let turnNo: Int
        let at: Date
    }

    /// How long after a Stop the question is still worth asking.
    static let stopAskSeconds: TimeInterval = 90

    static let stopReasons = ["wrong", "slow", "changed_mind", "done", "other"]
    static let downReasons = ["wrong", "unhelpful", "unsafe", "slow", "other"]

    @Published private(set) var stopAsk: StopAsk?
    /// Reply id → `up` or `down`, as voted on this screen since the app started.
    @Published private(set) var votes: [String: String] = [:]

    private var stoppedSessions = Set<String>()

    private init() {}

    /// The person pressed Stop in `session` while a turn ran.
    func stopped(session: String) {
        guard !session.isEmpty else { return }
        stoppedSessions.insert(session)
        NanoMuseClientEvents.turnEnded(session: session, ended: "aborted")
        if NanoMuseProvenance.runOf(session: session) == NanoMuseProvenance.runPerson {
            stopAsk = StopAsk(sessionId: session, turnNo: NanoMuseProvenance.turnNo(session: session), at: Date())
        }
    }

    /// The turn in `session` is over. A turn that was stopped was reported at the Stop; one that
    /// ended in an error is reported here; a turn that finished says nothing. Either way the
    /// next turn is the person's again (NanoMuseProvenance.turnEnded).
    func turnEnded(session: String, errored: Bool) {
        let wasStopped = stoppedSessions.remove(session) != nil
        if !wasStopped, errored { NanoMuseClientEvents.turnEnded(session: session, ended: "error") }
        NanoMuseProvenance.turnEnded(session: session)
    }

    /// Whether the question after a Stop is still worth showing in `session`.
    func asking(session: String, now: Date = Date()) -> StopAsk? {
        guard let ask = stopAsk, ask.sessionId == session, now.timeIntervalSince(ask.at) <= Self.stopAskSeconds else { return nil }
        return ask
    }

    /// The person answered why they stopped (one of `stopReasons`), or skipped the question.
    func answerStop(_ ask: StopAsk, reason: String?) {
        if stopAsk == ask { stopAsk = nil }
        let word = reason.flatMap { Self.stopReasons.contains($0) ? $0 : nil } ?? "skipped"
        NanoMuseClientEvents.turnStopped(session: ask.sessionId, reason: word)
    }

    /// Drops the question without an answer (the moment passed; nothing is reported).
    func dropStopAsk(_ ask: StopAsk) {
        if stopAsk == ask { stopAsk = nil }
    }

    /// A thumb on reply `messageId` of `session`: `up` or `down`, with a reason for a thumb down
    /// when one was picked (one of `downReasons`); the same thumb again takes it back (`none`),
    /// unless `keep` says the vote stands (the reason picked after the thumb).
    func vote(session: String, messageId: String, _ vote: String, reason: String? = nil, keep: Bool = false) {
        let current = votes[messageId]
        let next = (current == vote && !keep) ? "none" : vote
        if next == "none" { votes.removeValue(forKey: messageId) } else { votes[messageId] = next }
        let why = (next == "down") ? reason.flatMap { Self.downReasons.contains($0) ? $0 : nil } : nil
        NanoMuseClientEvents.turnFeedback(session: session, vote: next, reason: why)
    }

    /// For tests.
    func reset() {
        stopAsk = nil
        votes = [:]
        stoppedSessions.removeAll()
    }
}

// MARK: - The two views

/// Two small thumbs under the newest finished reply (NanoMuseFeedback.vote). A thumb down opens
/// one line of reasons, each a code, with Skip; a thumb up says "noted" for a moment. Nothing
/// here reads the reply.
struct NanoMuseThumbsRow: View {
    var session: String
    var messageId: String
    @ObservedObject private var feedback = NanoMuseFeedback.shared
    @State private var asking = false
    @State private var noted = false

    private var vote: String? { feedback.votes[messageId] }

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 2) {
                thumb(up: true)
                thumb(up: false)
                if noted {
                    Text(AppLocalized("Noted, thanks"))
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .padding(.leading, 4)
                        .transition(.opacity)
                }
                Spacer(minLength: 0)
            }
            if asking, vote == "down" {
                Text(AppLocalized("What was wrong?"))
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .padding(.leading, 6)
                NanoMuseFlowLayout(spacing: 6) {
                    ForEach(NanoMuseFeedback.downReasons, id: \.self) { reason in
                        NanoMuseReasonPill(text: Self.downReasonText(reason)) {
                            asking = false
                            noted = true
                            feedback.vote(session: session, messageId: messageId, "down", reason: reason, keep: true)
                            fadeNote()
                        }
                    }
                    Button(AppLocalized("Skip")) { asking = false }
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .buttonStyle(.plain)
                        .padding(.horizontal, 6)
                        .padding(.vertical, 6)
                }
                .padding(.leading, 6)
            }
        }
        .padding(.leading, 4)
        .nmOnChange(of: messageId) { _ in
            asking = false
            noted = false
        }
    }

    private func thumb(up: Bool) -> some View {
        let word = up ? "up" : "down"
        let on = vote == word
        return Button {
            if up {
                asking = false
                noted = vote != "up"
                feedback.vote(session: session, messageId: messageId, "up")
                if noted { fadeNote() }
            } else {
                noted = false
                asking = vote != "down"
                feedback.vote(session: session, messageId: messageId, "down")
            }
        } label: {
            Image(systemName: up ? (on ? "hand.thumbsup.fill" : "hand.thumbsup") : (on ? "hand.thumbsdown.fill" : "hand.thumbsdown"))
                .font(.system(size: 13, weight: .medium))
                .foregroundStyle(on ? Color.primary : Color.secondary.opacity(0.75))
                .frame(width: 28, height: 28)
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel(Text(up ? AppLocalized("Good reply") : AppLocalized("Not a good reply")))
    }

    private func fadeNote() {
        Task { @MainActor in
            try? await Task.sleep(nanoseconds: 2_500_000_000)
            withAnimation { noted = false }
        }
    }

    static func downReasonText(_ reason: String) -> String {
        switch reason {
        case "wrong": return AppLocalized("Wrong")
        case "unhelpful": return AppLocalized("Not helpful")
        case "unsafe": return AppLocalized("Should not have done that")
        case "slow": return AppLocalized("Too slow")
        default: return AppLocalized("Something else")
        }
    }
}

/// One question after a Stop, in the chat it happened in: why, as one of five codes, or Skip
/// (NanoMuseFeedback.answerStop). Shown while the question is still fresh
/// (NanoMuseFeedback.stopAskSeconds), then gone on its own.
struct NanoMuseStopAskCard: View {
    var ask: NanoMuseFeedback.StopAsk
    @ObservedObject private var feedback = NanoMuseFeedback.shared

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(AppLocalized("Why did you stop it?"))
                .font(.subheadline.weight(.medium))
            NanoMuseFlowLayout(spacing: 6) {
                ForEach(NanoMuseFeedback.stopReasons, id: \.self) { reason in
                    NanoMuseReasonPill(text: Self.stopReasonText(reason)) { feedback.answerStop(ask, reason: reason) }
                }
                Button(AppLocalized("Skip")) { feedback.answerStop(ask, reason: nil) }
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .buttonStyle(.plain)
                    .padding(.horizontal, 6)
                    .padding(.vertical, 6)
            }
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(NanoMuseTones.surface, in: RoundedRectangle(cornerRadius: 20, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 20, style: .continuous).stroke(NanoMuseTones.hairline))
        .task(id: ask) {
            let left = Self.remaining(for: ask)
            if left > 0 { try? await Task.sleep(nanoseconds: UInt64(left * 1_000_000_000)) }
            guard !Task.isCancelled else { return }
            feedback.dropStopAsk(ask) // unanswered for long enough: the moment has passed
        }
    }

    static func remaining(for ask: NanoMuseFeedback.StopAsk, now: Date = Date()) -> TimeInterval {
        max(0, NanoMuseFeedback.stopAskSeconds - now.timeIntervalSince(ask.at))
    }

    static func stopReasonText(_ reason: String) -> String {
        switch reason {
        case "wrong": return AppLocalized("It was going wrong")
        case "slow": return AppLocalized("Too slow")
        case "changed_mind": return AppLocalized("Changed my mind")
        case "done": return AppLocalized("It was already done")
        default: return AppLocalized("Something else")
        }
    }
}

/// One reason, as a small pill.
struct NanoMuseReasonPill: View {
    var text: String
    var action: () -> Void

    var body: some View {
        Button(action: action) {
            Text(text)
                .font(.caption)
                .foregroundStyle(Color.primary)
                .padding(.horizontal, 12)
                .padding(.vertical, 6)
                .background(NanoMuseTones.fill, in: Capsule())
        }
        .buttonStyle(.plain)
    }
}
