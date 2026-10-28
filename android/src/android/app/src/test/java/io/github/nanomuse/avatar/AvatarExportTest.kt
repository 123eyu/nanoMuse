package io.github.nanomuse.avatar

import java.io.File
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder

/** What *Save to gallery* copies out of the avatar folder (#664). */
class AvatarExportTest {
    @get:Rule val tmp = TemporaryFolder()

    private fun put(root: File, path: String, bytes: Int = 3): File =
        File(root, path).apply { parentFile?.mkdirs(); writeBytes(ByteArray(bytes)) }

    @Test fun `the face, its moods, the candidates and every clip go while posters, metadata and empty files stay`() {
        val root = tmp.newFolder("avatar")
        put(root, "base.png")
        put(root, "working.png"); put(root, "happy.png")
        put(root, "error.png", bytes = 0) // a mood whose file never got written
        put(root, "avatar.json")
        put(root, "candidates/0.png"); put(root, "candidates/1.png")
        put(root, "motion/idle.mp4"); put(root, "motion/happy.mp4")
        put(root, "own-clip.mp4"); put(root, "own-clip.png") // the clip and its poster
        put(root, "own-clip-working.mp4"); put(root, "own-clip-working.png")
        put(root, "own-clip.pending.mp4") // a choice that was never confirmed

        val items = AvatarExport.collect(root, "20261027-1945")
        assertEquals(
            listOf(
                "20261027-1945-base.png", "20261027-1945-working.png", "20261027-1945-happy.png",
                "20261027-1945-candidate-0.png", "20261027-1945-candidate-1.png",
                "20261027-1945-idle.mp4", "20261027-1945-happy.mp4",
                "20261027-1945-own-clip.mp4", "20261027-1945-own-clip-working.mp4",
            ),
            items.map { it.name },
        )
        assertEquals(listOf(false, false, false, false, false, true, true, true, true), items.map { it.video })
        assertTrue(items.all { it.file.isFile && it.file.length() > 0 })
    }

    @Test fun `an empty folder has nothing to save`() {
        assertEquals(emptyList<AvatarExport.Item>(), AvatarExport.collect(tmp.newFolder("empty"), "x"))
    }

    @Test fun `the stamp is the minute, in local time, with nothing a file name minds`() {
        val stamp = AvatarExport.stamp()
        assertTrue(stamp, Regex("""\d{8}-\d{4}""").matches(stamp))
    }
}
