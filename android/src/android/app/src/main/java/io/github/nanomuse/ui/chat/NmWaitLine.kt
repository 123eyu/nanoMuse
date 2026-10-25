package io.github.nanomuse.ui.chat

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.produceState
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.openminis.app.R
import io.github.nanomuse.cloud.NmStreamStatus
import kotlinx.coroutines.delay

/** The typing dots have bounced this long before the line under them says anything. */
private const val QUIET_S = 10

/**
 * A small line under the typing dots while a step of the turn has no first token yet. Nothing
 * for the first ten seconds; then "The model is busy, 15 s so far" with a Cancel; and, when
 * nanoMuse Cloud has said the request is queued ([NmStreamStatus]), "3 ahead of you" or "You
 * are next" instead, as soon as that is known. The clock starts when the dots appear, so each
 * step of a tool-using turn counts its own wait; a status that arrived before this step's
 * dots is an earlier step's and is ignored. [onCancel] is the chat's stop.
 */
@Composable
fun NmWaitLine(onCancel: (() -> Unit)?, modifier: Modifier = Modifier) {
    val shownAt = remember { System.currentTimeMillis() }
    val now by produceState(shownAt) {
        while (true) {
            delay(1_000)
            value = System.currentTimeMillis()
        }
    }
    val status by NmStreamStatus.latest.collectAsState()
    val waitedS = ((now - shownAt) / 1_000).toInt()
    val queued = status?.takeIf { it.queuedFor(now, shownAt) }
    val text = when {
        queued != null && queued.ahead > 0 -> stringResource(R.string.nm_wait_queued, queued.ahead, waitedS)
        queued != null -> stringResource(R.string.nm_wait_next, waitedS)
        waitedS >= QUIET_S -> stringResource(R.string.nm_wait_busy, waitedS)
        else -> return
    }
    val muted = MaterialTheme.colorScheme.onSurfaceVariant
    Row(
        modifier = modifier.padding(bottom = 8.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(text = text, fontSize = 12.sp, color = muted)
        if (onCancel != null) {
            Spacer(Modifier.width(10.dp))
            Text(
                text = stringResource(R.string.cancel),
                fontSize = 12.sp,
                color = MaterialTheme.colorScheme.primary,
                modifier = Modifier
                    .clickable(onClick = onCancel)
                    .padding(horizontal = 4.dp, vertical = 2.dp),
            )
        }
    }
}
