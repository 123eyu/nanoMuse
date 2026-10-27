package io.github.nanomuse.guard

import io.github.nanomuse.cloud.ClientEvents
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.async
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.test.setMain
import org.json.JSONArray
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/**
 * What the record hears of the gate (relay 0.31, docs/cloud.md "Client events"): the
 * decision on every call it sees and the answer to every card, as codes — the tool's kind, the
 * risk's tier, who settled it — never the command.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class RiskGateEventsTest {
    private val dispatcher = StandardTestDispatcher()
    private val sent = mutableListOf<JSONObject>()

    @Before fun main() {
        Dispatchers.setMain(dispatcher)
        ClientEvents.reset()
        sent.clear()
        ClientEvents.enabled = { true }
        ClientEvents.sender = { batch: JSONArray -> for (i in 0 until batch.length()) sent += batch.getJSONObject(i) }
    }

    @After fun reset() {
        Dispatchers.resetMain()
        ClientEvents.reset()
        ClientEvents.enabled = { false }
        ClientEvents.sender = null
    }

    private fun events(): List<JSONObject> {
        ClientEvents.drainNow()
        return sent.toList()
    }

    @Test fun `the relay's words for the tiers and the permissions`() {
        assertEquals("safe", RiskGate.riskWord(RiskClass.SAFE))
        assertEquals("moderate", RiskGate.riskWord(RiskClass.INSTALL))
        assertEquals("sensitive", RiskGate.riskWord(RiskClass.DESTRUCTIVE))
        assertEquals("sensitive", RiskGate.riskWord(RiskClass.OUTBOUND))
        assertEquals("critical", RiskGate.riskWord(RiskClass.MONEY))
        val plain = RiskRequest(sessionId = "s", kind = GuardKind.SHELL, assessment = RiskAssessment(RiskClass.DESTRUCTIVE, "deletes", "/tmp/x"), preview = "rm x")
        assertEquals("once", RiskGate.scopeWord(RiskDecision.ALLOW_ONCE, plain))
        assertEquals("conversation", RiskGate.scopeWord(RiskDecision.ALLOW_SESSION, plain))
        assertEquals("always", RiskGate.scopeWord(RiskDecision.ALLOW_ALWAYS, plain))
        assertNull(RiskGate.scopeWord(RiskDecision.DENY, plain))
        assertNull(RiskGate.scopeWord(RiskDecision.TIMEOUT, plain))
        val money = plain.copy(assessment = RiskAssessment(RiskClass.MONEY, "pays", "shop.example"))
        assertEquals("once", RiskGate.scopeWord(RiskDecision.ALLOW_SESSION, money)) // a chat-wide grant is never offered for money
    }

    @Test fun `a call the policy lets through is one decision, allowed by the policy`() = runTest(dispatcher) {
        val out = RiskGate.check("s1", GuardKind.SHELL, RiskAssessment(RiskClass.SAFE, ""), preview = "ls -la /very/private/path")
        assertTrue(out is GateOutcome.Allowed)
        val e = events().single()
        assertEquals("sentinel.decision", e.getString("kind"))
        val d = e.getJSONObject("data")
        assertEquals("shell", d.getString("tool"))
        assertEquals("safe", d.getString("risk"))
        assertEquals("allow", d.getString("decision"))
        assertEquals("policy", d.getString("by"))
        assertFalse(d.toString().contains("private")) // the command never travels
    }

    @Test fun `a card asked and answered is a decision to ask, then the answer`() = runTest(dispatcher) {
        val answer = async { RiskGate.check("s2", GuardKind.BROWSER, RiskAssessment(RiskClass.OUTBOUND, "sends a message", "chat.example"), preview = "Tap “Send”") }
        runCurrent()
        val req = RiskGate.pending.value!!
        RiskGate.decide(req.id, RiskDecision.ALLOW_SESSION, by = RiskGate.BY_OTHER_DEVICE)
        runCurrent()
        assertTrue(answer.await() is GateOutcome.Allowed)
        val e = events()
        assertEquals(listOf("sentinel.decision", "approval.result"), e.map { it.getString("kind") })
        val asked = e[0].getJSONObject("data")
        assertEquals("ask", asked.getString("decision"))
        assertEquals("browser", asked.getString("tool"))
        assertEquals("sensitive", asked.getString("risk"))
        assertEquals("outbound", asked.getString("class"))
        val answered = e[1].getJSONObject("data")
        assertTrue(answered.getBoolean("allow"))
        assertEquals("conversation", answered.getString("scope"))
        assertEquals("other_device", answered.getString("by"))
        assertTrue(answered.has("waited_s"))
    }

    @Test fun `a denial is an answer that did not allow`() = runTest(dispatcher) {
        val answer = async { RiskGate.check("s3", GuardKind.SHELL, RiskAssessment(RiskClass.DESTRUCTIVE, "deletes", "/tmp/y", warnings = listOf("rm -rf")), preview = "rm -rf /tmp/y") }
        runCurrent()
        RiskGate.decide(RiskGate.pending.value!!.id, RiskDecision.DENY)
        runCurrent()
        assertTrue(answer.await() is GateOutcome.Denied)
        val answered = events().last().getJSONObject("data")
        assertFalse(answered.getBoolean("allow"))
        assertFalse(answered.has("scope"))
        assertEquals("person", answered.getString("by"))
        assertEquals(1, answered.getInt("warnings"))
    }
}
