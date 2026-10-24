package io.github.nanomuse.backup

import com.openminis.app.backup.BackupBlobStore
import com.openminis.app.backup.BackupCategory
import com.openminis.app.backup.BackupFileIndexEntry
import com.openminis.app.backup.BackupFileIndexWriter
import com.openminis.app.backup.BackupFileTreeExporter
import com.openminis.app.backup.BackupFormat
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.io.File

/**
 * Chats as messages only (#561): one session's tree is listed as tombstones while another
 * category's tree, through the same blob store and the same package-wide cap, keeps its files.
 * Both importers treat a `skipped` entry as a tombstone, so such a package restores everywhere.
 */
class BackupChatsWithoutFilesTest {
    private lateinit var tmp: File
    private lateinit var staging: File

    @Before fun setUp() {
        tmp = File.createTempFile("minisbak-chatfiles", "").apply { delete(); mkdirs() }
        staging = File(tmp, "staging").apply { mkdirs() }
    }

    @After fun tearDown() { tmp.deleteRecursively() }

    private fun tree(name: String, vararg files: Pair<String, Int>): File {
        val root = File(tmp, name).apply { mkdirs() }
        for ((path, size) in files) File(root, path).apply { parentFile?.mkdirs() }.writeBytes(ByteArray(size) { 7 })
        return root
    }

    private fun index(file: File): List<BackupFileIndexEntry> =
        file.readLines().filter { it.isNotBlank() }.map { BackupFormat.json.decodeFromString(BackupFileIndexEntry.serializer(), it) }

    @Test fun `a chat tree without files is tombstones only, another category keeps its files`() {
        val session = tree("sid1", "attachments/a.png" to 3_000, "workspace/notes.md" to 40)
        val shared = tree("shared", "report.pdf" to 500)
        val blobs = BackupBlobStore(staging, maxFileBytes = null)
        val indexFile = File(staging, "files.index.jsonl")
        val trees = BackupFileTreeExporter(blobs, BackupFileIndexWriter(indexFile))

        val chats = trees.export(session, "chats/sid1", BackupCategory.CHATS, "sid1", tombstonesOnly = true)
        val files = trees.export(shared, "shared", BackupCategory.SHARED_FILES)

        assertEquals(0, chats.filesIncluded)
        assertEquals(2, chats.filesSkipped)
        assertEquals(1, files.filesIncluded)
        assertEquals("only the shared file has content in the package", 1, blobs.blobIndex.size)
        assertEquals(2, blobs.skippedFiles)
        assertEquals(3_040L, blobs.skippedBytes)
        assertNull("the package-wide cap stays what the person chose", blobs.maxFileBytesForManifest)

        val entries = index(indexFile)
        val chatEntries = entries.filter { it.category == BackupCategory.CHATS.key }
        assertEquals(2, chatEntries.size)
        assertTrue("every chat file is a tombstone with its true size", chatEntries.all { it.skipped == "size" && it.sha256 == null && it.size > 0 })
        assertEquals(listOf("chats/sid1/attachments/a.png", "chats/sid1/workspace/notes.md"), chatEntries.map { it.path }.sorted())
        val sharedEntry = entries.single { it.category == BackupCategory.SHARED_FILES.key }
        assertNull(sharedEntry.skipped)
        assertEquals(500L, sharedEntry.size)
    }

    @Test fun `with files on, the chat tree is carried as before`() {
        val session = tree("sid2", "attachments/b.png" to 10)
        val blobs = BackupBlobStore(staging, maxFileBytes = null)
        val trees = BackupFileTreeExporter(blobs, BackupFileIndexWriter(File(staging, "files.index.jsonl")))

        val r = trees.export(session, "chats/sid2", BackupCategory.CHATS, "sid2")

        assertEquals(1, r.filesIncluded)
        assertEquals(0, r.filesSkipped)
        assertEquals(1, blobs.blobIndex.size)
    }
}
