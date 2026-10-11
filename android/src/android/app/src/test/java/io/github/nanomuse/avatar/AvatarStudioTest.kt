package io.github.nanomuse.avatar

import android.content.Context
import android.content.ContextWrapper
import android.graphics.Bitmap
import com.openminis.app.data.model.ProviderCredential
import com.openminis.app.data.model.ProviderInstance
import com.openminis.app.data.model.ProviderType
import io.github.nanomuse.avatar.AvatarStudio.Slot
import io.github.nanomuse.ui.avatar.AgentMood
import java.io.IOException
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicInteger
import java.util.concurrent.atomic.AtomicReference
import kotlin.concurrent.thread
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder

class AvatarStudioTest {
    @get:Rule val tmp = TemporaryFolder()

    private val endpoint = ImageGen.Endpoint(
        instance = ProviderInstance(id = "p", label = "p", providerType = ProviderType.openAI, credentialType = ProviderCredential.apiKey),
        apiKey = "k",
        model = "m",
    )
    private val context: Context = object : ContextWrapper(null) {}
    private val realDrawer = AvatarStudio.drawer

    @After fun restore() {
        AvatarStudio.drawer = realDrawer
    }

    /** The stubbed android.jar cannot make a Bitmap the usual way; its no-argument constructor does. */
    private fun picture(): Bitmap = Bitmap::class.java.getDeclaredConstructor().apply { isAccessible = true }.newInstance()

    private suspend fun waitUntil(what: String, condition: () -> Boolean) {
        withTimeout(5_000) { while (!condition()) delay(10) }
        assertTrue(what, condition())
    }

    @Test fun `a cancelled run does not mark the tiles of its replacement failed`() = runBlocking {
        AvatarStore.useDirForTests(tmp.newFolder())
        val firstGate = CompletableDeferred<Unit>()
        val secondGate = CompletableDeferred<Unit>()
        val pandas = AtomicInteger(0)
        val bitmap = picture()
        // Two pictures are drawn at a time (the lane), so a set has two calls in flight and two waiting.
        AvatarStudio.drawer = { _, _, _, prompt ->
            if ("panda" in prompt) { pandas.incrementAndGet(); firstGate.await() } else secondGate.await()
            bitmap
        }
        AvatarStudio.startCandidates(context, endpoint, "a round red panda", AvatarStudio.Style.MUSE, null)
        val first = AvatarStudio.candidateJob!!
        waitUntil("the first set is being drawn") { pandas.get() == 2 }
        // A second description while the first set is still drawing: the first run is cancelled,
        // its four coroutines see CancellationException and must stay out of the new tiles.
        AvatarStudio.startCandidates(context, endpoint, "a small blue whale", AvatarStudio.Style.MUSE, null)
        first.join()
        assertTrue(AvatarStudio.slots.value.toString(), AvatarStudio.slots.value.all { it is Slot.Loading })
        secondGate.complete(Unit)
        AvatarStudio.candidateJob!!.join()
        assertTrue(AvatarStudio.slots.value.toString(), AvatarStudio.slots.value.all { it is Slot.Ready })
        assertEquals(null, AvatarStudio.error.value)
    }

    @Test fun `a retry that finishes after a new set is asked for does not touch the new tiles`() = runBlocking {
        AvatarStore.useDirForTests(tmp.newFolder())
        val retryGate = CompletableDeferred<Unit>()
        val secondGate = CompletableDeferred<Unit>()
        val bitmap = picture()
        var phase = 0
        AvatarStudio.drawer = { _, _, _, _ ->
            when (phase) {
                0 -> throw IOException("boom")
                1 -> { retryGate.await(); bitmap }
                else -> { secondGate.await(); bitmap }
            }
        }
        AvatarStudio.startCandidates(context, endpoint, "a round red panda", AvatarStudio.Style.MUSE, null)
        AvatarStudio.candidateJob!!.join()
        assertTrue(AvatarStudio.slots.value.all { it is Slot.Failed })
        phase = 1
        AvatarStudio.retrySlot(context, 0)
        waitUntil("the retry is being drawn") { AvatarStudio.slots.value[0] is Slot.Loading }
        phase = 2
        AvatarStudio.startCandidates(context, endpoint, "a small blue whale", AvatarStudio.Style.MUSE, null)
        // The retry was launched outside the run, so nothing cancels it; its picture belongs to
        // the old set and must not land in the new one.
        retryGate.complete(Unit)
        delay(300)
        assertTrue(AvatarStudio.slots.value.toString(), AvatarStudio.slots.value.all { it is Slot.Loading })
        secondGate.complete(Unit)
        AvatarStudio.candidateJob!!.join()
        assertTrue(AvatarStudio.slots.value.toString(), AvatarStudio.slots.value.all { it is Slot.Ready })
    }

    @Test fun `an old failure cannot publish across a replacement during a slot update`() = runBlocking {
        assertPublicationOwnership(ready = false)
    }

    @Test fun `an old picture cannot publish across a replacement during a slot update`() = runBlocking {
        assertPublicationOwnership(ready = true)
    }

    private suspend fun assertPublicationOwnership(ready: Boolean) {
        AvatarStore.useDirForTests(tmp.newFolder())
        val selectedChild = AtomicBoolean(false)
        val oldStarted = CountDownLatch(1)
        val finishOld = CompletableDeferred<Unit>()
        val holdOthers = CompletableDeferred<Unit>()
        val holdReplacement = CompletableDeferred<Unit>()
        val publicationThread = AtomicReference<Thread>()
        val copying = CountDownLatch(1)
        val releaseCopy = CountDownLatch(1)
        val copied = AtomicBoolean(false)
        val replacementFinished = CountDownLatch(1)
        val replacementError = AtomicReference<Throwable>()
        val bitmap = picture()
        AvatarStudio.drawer = { _, _, _, prompt ->
            if ("panda" in prompt) {
                if (selectedChild.compareAndSet(false, true)) {
                    oldStarted.countDown()
                    finishOld.await()
                    publicationThread.set(Thread.currentThread())
                    if (!ready) throw IOException("old-A failure")
                } else holdOthers.await()
            } else holdReplacement.await()
            bitmap
        }
        AvatarStudio.startCandidates(context, endpoint, "a round red panda", AvatarStudio.Style.MUSE, null)
        val oldJob = AvatarStudio.candidateJob!!
        var replacement: Thread? = null
        try {
            assertTrue("old drawer started", oldStarted.await(5, TimeUnit.SECONDS))
            // Pause a real production StateFlow update while toMutableList copies its input.
            // A previously completed tile makes this list differ from the initial Loading list,
            // so StateFlow stores it. No production generation logic is replaced by the fixture.
            val prior = List<Slot>(AvatarStudio.CANDIDATES) { if (it == 3) Slot.Failed("prior-A") else Slot.Loading }
            val barrier = object : java.util.AbstractList<Slot>() {
                override val size: Int get() = prior.size
                override fun get(index: Int): Slot {
                    if (Thread.currentThread() === publicationThread.get() && copied.compareAndSet(false, true)) {
                        copying.countDown()
                        check(releaseCopy.await(5, TimeUnit.SECONDS)) { "publication barrier timed out" }
                    }
                    return prior[index]
                }
            }
            (AvatarStudio.slots as MutableStateFlow<List<Slot>>).value = barrier
            finishOld.complete(Unit)
            assertTrue("old child reached slot publication", copying.await(5, TimeUnit.SECONDS))
            val replacementThread = thread(isDaemon = true, name = "avatar-replacement") {
                try {
                    AvatarStudio.startCandidates(context, endpoint, "a small blue whale", AvatarStudio.Style.MUSE, null)
                } catch (error: Throwable) {
                    replacementError.set(error)
                } finally {
                    replacementFinished.countDown()
                }
            }
            replacement = replacementThread
            // A serialized implementation waits for the old publication; an atomic state
            // implementation may finish replacement immediately. Both must keep B's tiles.
            waitUntil("replacement completed or waits for publication") {
                replacementFinished.count == 0L || (replacementThread.state == Thread.State.BLOCKED &&
                    replacementThread.stackTrace.any { it.className == AvatarStudio::class.java.name && it.methodName.startsWith("startCandidates") })
            }
            releaseCopy.countDown()
            assertTrue("replacement started", replacementFinished.await(5, TimeUnit.SECONDS))
            assertEquals(null, replacementError.get())
            withTimeout(5_000) { oldJob.join() }
            assertTrue("replacement tiles while its drawer is held: ${AvatarStudio.slots.value}",
                AvatarStudio.slots.value.all { it is Slot.Loading })
            assertEquals(null, AvatarStudio.error.value)
            holdReplacement.complete(Unit)
            withTimeout(5_000) { AvatarStudio.candidateJob!!.join() }
            assertTrue(AvatarStudio.slots.value.toString(), AvatarStudio.slots.value.all { it is Slot.Ready })
        } finally {
            releaseCopy.countDown()
            finishOld.complete(Unit)
            holdOthers.complete(Unit)
            holdReplacement.complete(Unit)
            replacement?.join(5_000)
            oldJob.cancel()
            AvatarStudio.candidateJob?.cancel()
            withTimeout(5_000) {
                oldJob.join()
                AvatarStudio.candidateJob?.join()
            }
        }
    }

    @Test fun `four candidates get four different prompts from one description`() {
        val prompts = (0 until 4).map { AvatarStudio.buildPrompt("A round red panda", AvatarStudio.Style.FLAT, it) }
        assertEquals(4, prompts.toSet().size)
        prompts.forEach {
            assertTrue(it.contains("A round red panda. "))
            assertTrue(it.contains(AvatarStudio.Style.FLAT.phrase))
            assertTrue(it.contains("No text"))
            // Muse's house rules: whole figure, facing you, white ground, square.
            assertTrue(it.contains("Full body"))
            assertTrue(it.contains("pure white background"))
            assertTrue(it.contains("Square composition"))
        }
        // The fifth candidate wraps around instead of crashing.
        assertEquals(prompts[0], AvatarStudio.buildPrompt("A round red panda", AvatarStudio.Style.FLAT, 4))
    }

    @Test fun `the default style is the 3D toy look`() {
        assertEquals(AvatarStudio.Style.MUSE, AvatarStudio.Style.byId(null))
        assertEquals(AvatarStudio.Style.MUSE, AvatarStudio.Style.byId("nonsense"))
        assertTrue(AvatarStudio.Style.MUSE.phrase.contains("3D"))
    }

    @Test fun `a trailing full stop in the description is not doubled`() {
        val p = AvatarStudio.buildPrompt("  一只圆滚滚的小熊猫。 ", AvatarStudio.Style.CLAY, 0)
        assertTrue(p.contains("一只圆滚滚的小熊猫. "))
        assertFalse(p.contains(".."))
        assertFalse(p.contains("。."))
    }

    @Test fun `every mood but idle gets a pose that keeps the character`() {
        for (mood in AgentMood.entries.filter { it != AgentMood.IDLE }) {
            val i = AvatarStudio.moodInstruction(mood)
            assertTrue(mood.name, i.startsWith("Keep this exact character"))
            assertTrue(mood.name, i.length > 80)
        }
        assertTrue(AvatarStudio.moodInstruction(AgentMood.WORKING).contains("headphones"))
        assertTrue(AvatarStudio.moodInstruction(AgentMood.WAITING).contains("crystal ball"))
        assertTrue(AvatarStudio.moodInstruction(AgentMood.HAPPY).contains("star"))
    }

    @Test fun `rate limits are recognised however the provider phrases them`() {
        assertTrue(AvatarStudio.looksRateLimited(RuntimeException("HTTP 429 Too Many Requests")))
        assertTrue(AvatarStudio.looksRateLimited(RuntimeException("Rate limited — please try again later")))
        assertTrue(AvatarStudio.looksRateLimited(RuntimeException("Throttling.RateQuota")))
        assertFalse(AvatarStudio.looksRateLimited(RuntimeException("HTTP 401 invalid api key")))
        assertFalse(AvatarStudio.looksRateLimited(RuntimeException(null as String?)))
    }
}
