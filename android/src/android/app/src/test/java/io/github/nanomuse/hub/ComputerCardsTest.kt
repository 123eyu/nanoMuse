package io.github.nanomuse.hub

import io.github.nanomuse.guard.GuardKind
import io.github.nanomuse.guard.RiskDecision
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** A computer's pushed approval card on this phone: the card it shows and the answer it sends back. */
class ComputerCardsTest {
    private fun body(scopes: List<String> = listOf("once", "schedule")) = JSONObject()
        .put("stage", "approval").put("approval_id", "ap_1").put("any_device", true)
        .put("preview", "Press Enter in WeChat").put("preview_zh", "在微信里按回车").put("reason", "send the report")
        .put("app", "WeChat").put("task", JSONObject().put("id", "sch-1").put("title", "Daily report"))
        .put("chat", "Work").put("scopes", JSONArray(scopes)).put("timeout", 600)

    @Test fun `the card names the computer and the task and offers always in this task only when the computer does`() {
        val r = ComputerCards.request(body(), "Desk", chinese = false)
        assertEquals(GuardKind.SCHEDULED, r.kind)
        assertEquals("Desk", r.pageUrl)
        assertEquals("Daily report", r.elementText)
        assertEquals("WeChat", r.app)
        assertEquals("Press Enter in WeChat", r.preview)
        assertEquals(600_000L, r.timeoutMs)
        assertTrue(r.canAlways)
        assertFalse(r.canSession)
        assertFalse(ComputerCards.request(body(listOf("once")), "Desk", chinese = false).canAlways)
        assertEquals("在微信里按回车", ComputerCards.request(body(), "Desk", chinese = true).preview)
    }

    @Test fun `the answer says allow and its scope, deny, or nothing when nobody answered`() {
        val both = listOf("once", "schedule")
        assertEquals("""{"allow":true,"scope":"schedule"}""", ComputerCards.answer(RiskDecision.ALLOW_ALWAYS, both).toString())
        assertEquals("""{"allow":true,"scope":"once"}""", ComputerCards.answer(RiskDecision.ALLOW_ALWAYS, listOf("once")).toString())
        assertEquals("""{"allow":true,"scope":"once"}""", ComputerCards.answer(RiskDecision.ALLOW_ONCE, both).toString())
        assertEquals("""{"allow":false}""", ComputerCards.answer(RiskDecision.DENY, both).toString())
        assertNull(ComputerCards.answer(RiskDecision.TIMEOUT, both))
    }
}
