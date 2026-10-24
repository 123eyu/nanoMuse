package io.github.nanomuse.reach

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Test

class RemoteApprovalKeyTest {
    @Test
    fun aSenderThatNamesTheKeyIsRememberedByIt() {
        val event = JSONObject().put("preview", "shell: git push origin main").put("grant_key", "shell:git")
        assertEquals("task:shell:git", RemoteApprovalKey.target(event))
    }

    @Test
    fun twoStepsWithTheSameFirstWordsDoNotShareAnAnswerWhenTheKeyIsNamed() {
        val prefix = "shell: echo " + "a".repeat(60)
        val one = JSONObject().put("preview", "$prefix…").put("grant_key", "$prefix; ls")
        val two = JSONObject().put("preview", "$prefix…").put("grant_key", "$prefix; rm -rf ~")
        assertNotEquals(RemoteApprovalKey.target(one), RemoteApprovalKey.target(two))
    }

    @Test
    fun anOlderSenderIsRememberedByItsPreviewAsBefore() {
        val event = JSONObject().put("preview", "shell: git push origin main")
        assertEquals("task:shell: git push origin main", RemoteApprovalKey.target(event))
        val long = JSONObject().put("preview", "x".repeat(80))
        assertEquals("task:" + "x".repeat(60), RemoteApprovalKey.target(long))
        assertEquals("task:from summary", RemoteApprovalKey.target(JSONObject().put("summary", "from summary")))
    }
}
