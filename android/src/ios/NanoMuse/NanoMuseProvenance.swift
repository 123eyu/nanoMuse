//
//  NanoMuseProvenance.swift
//  nanoMuse
//
//  Where a model call sits, said to the relay (relay 0.31, docs/cloud.md "Provenance"): who
//  started the turn (`person`, `schedule`, `feed`, `goal`, `hub`), the turn's number in the
//  conversation and the call's number within the turn. A Cloud request carries them as headers
//  next to `X-NanoMuse-Conversation` (NanoMuseCloudHeaders); a turn run on a key of the person's
//  own carries the same as fields of the record it is sent up in (NanoMuseOwnTurns). Codes and
//  numbers, never a word of the conversation. The phone has no screen operator, so no trajectory
//  is named from here.
//
//  The provider that builds the request does not know which chat it works for, so the chat
//  says it around each call: the agent loop's provider is wrapped (`wrap`) in one that counts
//  the call as a step of the chat's turn and binds what it found as a task-local (`current`),
//  which the request builder and the own-key recorder read. Who started the turn is told by
//  whoever starts it (`arm`) and lasts until the turn ends (`turnEnded`); a turn nobody
//  announced is the person's. The same rows remember the id the relay gave the last Cloud call
//  (`x-nanomuse-request`) and the id of the last own-key turn sent up, so an event about the
//  turn (NanoMuseClientEvents) can name it. Android: cloud/Provenance.kt; desktop: src/provenance.ts.
//

import Foundation

enum NanoMuseProvenance {
    static let platform = "ios"

    static let runHeader = "X-NanoMuse-Run"
    static let turnHeader = "X-NanoMuse-Turn"
    static let stepHeader = "X-NanoMuse-Step"
    static let platformHeader = "X-NanoMuse-Platform"
    /// The relay names the turn it kept in this response header; events about the turn carry the same id.
    static let responseHeader = "x-nanomuse-request"

    static let runPerson = "person"
    static let runSchedule = "schedule"
    static let runFeed = "feed"
    static let runGoal = "goal"
    static let runHub = "hub"

    /// Sessions remembered; past this the one longest untouched goes.
    static let sessionsKept = 64

    /// What one call said, as the headers carry it and as an own-key turn's fields repeat it.
    struct Fields: Equatable, Sendable {
        var run: String
        var turn: Int
        var step: Int

        var headers: [String: String] {
            var h = [runHeader: run, platformHeader: platform]
            if turn > 0 { h[turnHeader] = String(turn) }
            if step > 0 { h[stepHeader] = String(step) }
            return h
        }

        /// The same on a turn sent up: `run`, `turn`, `step`, `platform`.
        func into(_ turnJSON: inout [String: Any]) {
            turnJSON["run"] = run
            if turn > 0 { turnJSON["turn"] = turn }
            if step > 0 { turnJSON["step"] = step }
            turnJSON["platform"] = platform
        }
    }

    /// The call under way, as the wrapper found it: the chat, the conversation and the fields.
    struct Current: Sendable {
        let sessionId: String?
        let conversation: String
        let fields: Fields
    }

    /// Bound around the inner provider's call by `wrap`; read where the request is built.
    @TaskLocal static var current: Current?

    /// The id the record knows a turn by: the relay's request id of a Cloud turn, or an own-key turn's own id.
    struct TurnRef: Equatable, Sendable {
        let id: String
        let own: Bool
    }

    private final class Row {
        var run = runPerson
        var turn = 0
        var step = 0
        var conversation: String?
        var ref: TurnRef?
        var touched = 0
    }

    private static let lock = NSLock()
    private nonisolated(unsafe) static var rows: [String: Row] = [:]
    private nonisolated(unsafe) static var tick = 0

    // MARK: The chat's side

    /// The next turn in `session` is `run`'s (a routine's, the feed's, a goal's, another device's), until it ends.
    static func arm(session: String, run: String) {
        let word = run.lowercased()
        guard isRunWord(word) else { return }
        lock.lock(); defer { lock.unlock() }
        self.row(session).run = word
    }

    /// The announced run did not start after all.
    static func disarm(session: String) { turnEnded(session: session) }

    /// Who the turn under way in `session` belongs to.
    static func runOf(session: String) -> String {
        lock.lock(); defer { lock.unlock() }
        return rows[session]?.run ?? runPerson
    }

    /// The turn in `session` is over: the next one is the person's unless someone says otherwise, and its calls count from one.
    static func turnEnded(session: String) {
        lock.lock(); defer { lock.unlock() }
        guard let row = rows[session] else { return }
        row.run = runPerson
        row.step = 0
    }

    /// The number of the turn under way (or last run) in `session`, 0 when none was counted yet.
    static func turnNo(session: String) -> Int {
        lock.lock(); defer { lock.unlock() }
        return rows[session]?.turn ?? 0
    }

    /// The conversation id the session's calls carried, once one went out.
    static func conversationOf(session: String) -> String? {
        lock.lock(); defer { lock.unlock() }
        return rows[session]?.conversation
    }

    /// The record's id for the session's latest turn, once the relay or the uploader named it.
    static func turnRef(session: String) -> TurnRef? {
        lock.lock(); defer { lock.unlock() }
        return rows[session]?.ref
    }

    /// The session a conversation id belongs to, when a call of it went out from here.
    static func sessionOf(conversation: String) -> String? {
        lock.lock(); defer { lock.unlock() }
        return rows.filter { $0.value.conversation == conversation }.max { $0.value.touched < $1.value.touched }?.key
    }

    // MARK: The call's side

    /// The headers for a Cloud request built under `wrap`: the current call's, or the platform alone.
    static func headers() -> [String: String] {
        current?.fields.headers ?? [platformHeader: platform]
    }

    /// Whether the conversation the request names is the one the current call is about.
    static func isCurrent(conversation: String?) -> Bool {
        guard let conversation, let now = current else { return false }
        return now.conversation == conversation
    }

    /// `provider` as the agent loop uses it: every call counted as a step of the chat's turn
    /// and announced to what runs under it. `chat` is read as each call goes out: the chat's
    /// session id (once it has one) and its turn count (its user rows), which is the turn's
    /// number even after older rows were compacted away from the request.
    static func wrap(_ provider: AgentProvider, chat: @escaping @MainActor @Sendable () -> (session: String?, turns: Int)) -> AgentProvider {
        if provider is Counting { return provider }
        return Counting(inner: provider, chat: chat)
    }

    /**
     What the call about to go out says of itself: the session's run and the turn (the chat's
     count when it has one, else counted from the request), one more step of that turn, and the
     conversation remembered as the session's. Without a session (a model call from somewhere
     else) the turn and step are counted from the request alone and the run is the person's.
     */
    static func fields(session: String?, turns counted: Int, conversation: String, messages: [AgentMessage]) -> Fields {
        guard let session, !session.isEmpty else {
            return Fields(run: runPerson, turn: turnOf(messages), step: stepOf(messages))
        }
        lock.lock(); defer { lock.unlock() }
        let row = self.row(session)
        let turn = counted > 0 ? counted : turnOf(messages)
        if turn != row.turn {
            row.turn = turn
            row.step = 0
        }
        row.step += 1
        if !conversation.isEmpty { row.conversation = conversation }
        return Fields(run: row.run, turn: turn, step: row.step)
    }

    /// The relay answered a Cloud call of `conversation` and named the turn `requestId`.
    static func noteResponse(conversation: String?, requestId: String?) {
        guard let conversation, !conversation.isEmpty,
              let id = requestId?.trimmingCharacters(in: .whitespaces), isId(id) else { return }
        lock.lock(); defer { lock.unlock() }
        guard let row = rows.filter({ $0.value.conversation == conversation }).max(by: { $0.value.touched < $1.value.touched })?.value else { return }
        row.ref = TurnRef(id: id, own: false)
    }

    /// An own-key turn of `session` went up under `id`.
    static func noteOwnTurn(session: String?, id: String) {
        guard let session, !session.isEmpty else { return }
        lock.lock(); defer { lock.unlock() }
        self.row(session).ref = TurnRef(id: id, own: true)
    }

    // MARK: Counting from the request alone

    /// A message the person (or a routine in their place) sent: a user row that is not a tool's result.
    static func isPersonMessage(_ m: AgentMessage) -> Bool {
        guard m.role == .user else { return false }
        return !m.parts.contains { if case .toolResult = $0 { return true } else { return false } }
    }

    /// The turn's number as the request shows it: its user messages, tool results aside.
    static func turnOf(_ messages: [AgentMessage]) -> Int { messages.filter(isPersonMessage).count }

    /// The call's number within the turn as the request shows it: the model's answers since the last user message, plus one.
    static func stepOf(_ messages: [AgentMessage]) -> Int {
        var n = 0
        for m in messages.reversed() {
            if isPersonMessage(m) { break }
            if m.role == .assistant { n += 1 }
        }
        return n + 1
    }

    /// For tests: nothing remembered.
    static func reset() {
        lock.lock(); defer { lock.unlock() }
        rows.removeAll()
    }

    // MARK: Shapes

    private static let wordChars = CharacterSet(charactersIn: "abcdefghijklmnopqrstuvwxyz0123456789_")
    private static let idChars = CharacterSet(charactersIn: "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.:-")

    /// `[a-z][a-z0-9_]{0,23}`: a run word the relay takes.
    static func isRunWord(_ s: String) -> Bool {
        guard let first = s.unicodeScalars.first, s.count <= 24, first.isASCII, first.properties.isLowercase else { return false }
        return s.unicodeScalars.allSatisfy { wordChars.contains($0) }
    }

    /// `[A-Za-z0-9_.:-]{1,80}`: an id of the record's.
    static func isId(_ s: String) -> Bool {
        !s.isEmpty && s.count <= 80 && s.unicodeScalars.allSatisfy { idChars.contains($0) }
    }

    private static func row(_ session: String) -> Row {
        tick += 1
        if let row = rows[session] {
            row.touched = tick
            return row
        }
        let row = Row()
        row.touched = tick
        rows[session] = row
        if rows.count > sessionsKept, let oldest = rows.min(by: { $0.value.touched < $1.value.touched })?.key {
            rows.removeValue(forKey: oldest)
        }
        return row
    }

    // MARK: The wrapper

    private final class Counting: AgentProvider {
        let inner: AgentProvider
        let chat: @MainActor @Sendable () -> (session: String?, turns: Int)

        init(inner: AgentProvider, chat: @escaping @MainActor @Sendable () -> (session: String?, turns: Int)) {
            self.inner = inner
            self.chat = chat
        }

        var name: String { inner.name }
        var model: LLMModel { inner.model }
        var defaultMaxTokens: Int { inner.defaultMaxTokens }

        func streamAgentMessageClamped(
            messages: [AgentMessage],
            systemPrompt: String?,
            tools: [AgentToolDefinition],
            maxTokens: Int,
            thinkingLevel: ThinkingLevel
        ) async throws -> AsyncThrowingStream<AgentStreamEvent, Error> {
            let (session, turns) = await chat()
            let conversation = NanoMuseOwnTurns.conversationKey(messages)
            let fields = NanoMuseProvenance.fields(session: session, turns: turns, conversation: conversation, messages: messages)
            let now = Current(sessionId: session, conversation: conversation, fields: fields)
            return try await NanoMuseProvenance.$current.withValue(now) {
                try await inner.streamAgentMessageClamped(
                    messages: messages, systemPrompt: systemPrompt, tools: tools, maxTokens: maxTokens, thinkingLevel: thinkingLevel
                )
            }
        }
    }
}
