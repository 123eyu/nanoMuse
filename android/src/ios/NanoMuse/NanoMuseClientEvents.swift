//
//  NanoMuseClientEvents.swift
//  nanoMuse
//
//  What happened around a turn that the request itself does not show, told to the account's
//  record as codes and numbers (relay 0.31, docs/cloud.md "Client events"): what the permission
//  check decided on an `apple-*` command and how the person answered its sheet, that a reply
//  was stopped and why, a thumb up or down. `POST /v1/events`, a hundred at most per request,
//  gathered for a few seconds first; nothing goes out without an account, and a relay out of
//  reach loses the batch rather than hoarding it.
//
//  The shape is kept here before anything travels: `data` is a flat object of short codes
//  (`[A-Za-z0-9_.:/ -]`, 120 characters at most), numbers and booleans; a string that looks
//  like prose is left out, never cut, which is how the promise that no message travels here is
//  kept on the phone and not only at the relay. Each event may name the turn it is about by the
//  id the record knows it by (NanoMuseProvenance.turnRef) and the conversation.
//  Android: cloud/ClientEvents.kt; desktop: src/events.ts.
//

import Foundation
import UIKit

enum NanoMuseClientEvents {
    /// Events per request, the relay's ceiling.
    static let batch = 100
    /// How long events gather before a batch goes.
    static let flushSeconds: TimeInterval = 4
    /// Events waiting; past this the oldest is dropped.
    static let queueMax = 1_000
    static let dataMaxKeys = 16
    static let codeMax = 120
    static let kindMax = 48

    /// Where a batch goes; the app's is the relay, a test's is a list.
    nonisolated(unsafe) static var sender: (@Sendable ([[String: Any]]) async throws -> Void)?
    /// Whether anything goes at all: signed in, on the app.
    nonisolated(unsafe) static var enabled: @Sendable () async -> Bool = { await MainActor.run { NanoMuseCloud.isSignedIn } }

    private static let lock = NSLock()
    private nonisolated(unsafe) static var queue: [[String: Any]] = []
    private nonisolated(unsafe) static var timer: Task<Void, Never>?
    private nonisolated(unsafe) static var draining = false
    private nonisolated(unsafe) static var observers: [NSObjectProtocol] = []
    /// Batches sent and events dropped since the process started; the log reads them.
    private(set) nonisolated(unsafe) static var sent = 0
    private(set) nonisolated(unsafe) static var dropped = 0

    /// App start: the relay as the sender, and what is waiting goes when the app leaves the screen.
    @MainActor
    static func start() {
        guard observers.isEmpty else { return }
        sender = { events in try await NanoMuseRelayMedia.postEvents(events) }
        observers.append(NotificationCenter.default.addObserver(forName: UIApplication.didEnterBackgroundNotification, object: nil, queue: .main) { _ in
            flush()
        })
    }

    /// How many events are waiting.
    static var pending: Int {
        lock.lock(); defer { lock.unlock() }
        return queue.count
    }

    /// One event: `kind` in dotted lower case, `data` cleaned to the shape above, the turn it is
    /// about (`turn`, when the record has named it) and its `conversation`. Queued now, judged
    /// (signed in or not) when the batch goes.
    static func add(kind: String, data: [String: Any?], turn: NanoMuseProvenance.TurnRef? = nil, conversation: String? = nil) {
        guard let event = event(kind: kind, data: data, turn: turn, conversation: conversation) else { return }
        lock.lock()
        if queue.count >= queueMax {
            queue.removeFirst()
            dropped += 1
        }
        queue.append(event)
        let arm = timer == nil
        if arm {
            timer = Task.detached(priority: .utility) {
                try? await Task.sleep(nanoseconds: UInt64(flushSeconds * 1_000_000_000))
                flush()
            }
        }
        lock.unlock()
    }

    /// An event about the session's latest turn: the record's id and the conversation looked up for it.
    static func add(session: String?, kind: String, data: [String: Any?]) {
        let sid = (session ?? "").isEmpty ? nil : session
        add(kind: kind, data: data, turn: sid.flatMap { NanoMuseProvenance.turnRef(session: $0) }, conversation: sid.flatMap { NanoMuseProvenance.conversationOf(session: $0) })
    }

    /// The event as it travels, or nil for one the relay would drop (so it is not sent at all).
    static func event(kind: String, data: [String: Any?], turn: NanoMuseProvenance.TurnRef? = nil, conversation: String? = nil, now: Date = Date()) -> [String: Any]? {
        let k = kind.trimmingCharacters(in: .whitespaces)
        guard isKind(k) else { return nil }
        var clean: [String: Any] = [:]
        for (key, value) in data.sorted(by: { $0.key < $1.key }) {
            if clean.count >= dataMaxKeys { break }
            guard isKey(key), let value else { continue }
            switch value {
            case let b as Bool:
                clean[key] = b
            case let n as Int:
                if abs(n) < 1_000_000_000_000_000 { clean[key] = n }
            case let n as Int64:
                if abs(n) < 1_000_000_000_000_000 { clean[key] = n }
            case let n as Double:
                if n.isFinite, abs(n) < 1e15 { clean[key] = n }
            case let s as String:
                let t = s.trimmingCharacters(in: .whitespaces)
                if isCode(t) { clean[key] = t } // prose never travels: left out, not cut
            default:
                continue
            }
        }
        var e: [String: Any] = ["kind": k, "ts": Int(now.timeIntervalSince1970), "data": clean]
        if let turn {
            e["turn"] = turn.id
            if turn.own { e["source"] = "own" }
        }
        if let conversation, !conversation.isEmpty { e["conversation"] = String(conversation.prefix(96)) }
        return e
    }

    /// Sends what is waiting, a batch at a time, off the main thread.
    static func flush() {
        lock.lock()
        timer?.cancel()
        timer = nil
        let start = !draining
        draining = true
        lock.unlock()
        guard start else { return }
        Task.detached(priority: .utility) { await drain() }
    }

    /// For tests: what is waiting goes now, awaited.
    static func drainNow() async {
        lock.lock()
        timer?.cancel()
        timer = nil
        let start = !draining
        draining = true
        lock.unlock()
        guard start else { return }
        await drain()
    }

    private static func drain() async {
        defer {
            lock.lock()
            draining = false
            lock.unlock()
        }
        while true {
            lock.lock()
            let events = Array(queue.prefix(batch))
            queue.removeFirst(events.count)
            lock.unlock()
            if events.isEmpty { return }
            guard let send = sender else { return }
            guard await enabled() else {
                lock.lock(); dropped += events.count; lock.unlock()
                return
            }
            do {
                try await send(events)
                lock.lock(); sent += 1; lock.unlock()
            } catch {
                lock.lock(); dropped += events.count; lock.unlock()
                AppLogger(category: "NanoMuseClientEvents").info("a batch of \(events.count) events was not sent: \(NanoMuseCloud.describe(error))")
                return
            }
        }
    }

    /// For tests: nothing waiting, nothing scheduled.
    static func reset() {
        lock.lock()
        queue.removeAll()
        timer?.cancel()
        timer = nil
        sent = 0
        dropped = 0
        lock.unlock()
    }

    // MARK: The kinds, as docs/cloud.md lists them

    /// The permission check's word on an `apple-*` command: what the setting said, what was decided.
    static func sentinelDecision(session: String?, data: [String: Any?]) { add(session: session, kind: "sentinel.decision", data: data) }

    /// How the person answered a permission sheet, or that nobody did.
    static func approvalResult(session: String?, data: [String: Any?]) { add(session: session, kind: "approval.result", data: data) }

    /// A turn that ended short: `aborted` (Stop), `error`, `abandoned`.
    static func turnEnded(session: String?, ended: String) {
        add(session: session, kind: "turn.ended", data: ["ended": ended, "turn_no": turnNo(session)])
    }

    /// Why a reply was stopped, in the person's one word, or `skipped`.
    static func turnStopped(session: String?, reason: String) {
        add(session: session, kind: "turn.stopped", data: ["reason": reason, "turn_no": turnNo(session)])
    }

    /// A thumb up or down on a reply (`none` takes it back), with a reason when one was given.
    static func turnFeedback(session: String?, vote: String, reason: String?, turnNo no: Int? = nil) {
        add(session: session, kind: "turn.feedback", data: ["vote": vote, "reason": reason, "turn_no": no ?? turnNo(session)])
    }

    private static func turnNo(_ session: String?) -> Int? {
        guard let session, !session.isEmpty else { return nil }
        let n = NanoMuseProvenance.turnNo(session: session)
        return n > 0 ? n : nil
    }

    // MARK: Shapes

    private static let codeChars = CharacterSet.alphanumerics.union(CharacterSet(charactersIn: "_.:/ -"))
    private static let wordChars = CharacterSet.lowercaseLetters.union(.decimalDigits).union(CharacterSet(charactersIn: "_"))

    /// `[A-Za-z0-9_.:/ -]{0,120}`: a code, a number as text, a path-like id; not prose.
    static func isCode(_ s: String) -> Bool {
        s.count <= codeMax && s.unicodeScalars.allSatisfy { $0.isASCII && codeChars.contains($0) }
    }

    /// `[a-z][a-z0-9_]*`.
    private static func isWord(_ s: Substring) -> Bool {
        guard let first = s.unicodeScalars.first, first.isASCII, CharacterSet.lowercaseLetters.contains(first) else { return false }
        return s.unicodeScalars.allSatisfy { $0.isASCII && wordChars.contains($0) }
    }

    /// A `data` key: a word of 32 characters at most.
    static func isKey(_ s: String) -> Bool { s.count <= 32 && isWord(Substring(s)) }

    /// `[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*){0,3}`, 48 characters at most.
    static func isKind(_ s: String) -> Bool {
        guard s.count <= kindMax else { return false }
        let parts = s.split(separator: ".", omittingEmptySubsequences: false)
        return parts.count <= 4 && parts.allSatisfy(isWord)
    }
}
