package io.github.nanomuse.ui.feedback

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ThumbDown
import androidx.compose.material.icons.filled.ThumbUp
import androidx.compose.material.icons.outlined.ThumbDown
import androidx.compose.material.icons.outlined.ThumbUp
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.openminis.app.R
import io.github.nanomuse.ui.home.MuseTones
import kotlinx.coroutines.delay

/**
 * Two small thumbs under a reply ([TurnFeedback.vote]): shown under the newest reply, and
 * kept under an older one that was voted on. A thumb down opens one line of reasons, each a
 * code, with Skip; a thumb up says "noted" for a moment. Nothing here reads the reply.
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun ThumbsRow(
    sessionId: String,
    messageId: String,
    newest: Boolean,
    modifier: Modifier = Modifier,
) {
    val votes by TurnFeedback.votes.collectAsState()
    val vote = votes[messageId]
    if (!newest && vote == null) return
    var asking by remember(messageId) { mutableStateOf(false) }
    var noted by remember(messageId) { mutableStateOf(false) }
    val quiet = MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.7f)
    val strong = MaterialTheme.colorScheme.onSurface
    LaunchedEffect(noted) {
        if (noted) {
            delay(2_500)
            noted = false
        }
    }
    Column(modifier.padding(start = 8.dp, end = 12.dp, top = 2.dp, bottom = 4.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            IconButton(
                onClick = {
                    asking = false
                    noted = vote != "up"
                    TurnFeedback.vote(sessionId, messageId, "up")
                },
                modifier = Modifier.size(28.dp),
            ) {
                Icon(
                    if (vote == "up") Icons.Filled.ThumbUp else Icons.Outlined.ThumbUp,
                    contentDescription = stringResource(R.string.nm_fb_up),
                    tint = if (vote == "up") strong else quiet,
                    modifier = Modifier.size(15.dp),
                )
            }
            IconButton(
                onClick = {
                    noted = false
                    val taking = vote == "down"
                    asking = !taking
                    TurnFeedback.vote(sessionId, messageId, "down")
                },
                modifier = Modifier.size(28.dp),
            ) {
                Icon(
                    if (vote == "down") Icons.Filled.ThumbDown else Icons.Outlined.ThumbDown,
                    contentDescription = stringResource(R.string.nm_fb_down),
                    tint = if (vote == "down") strong else quiet,
                    modifier = Modifier.size(15.dp),
                )
            }
            if (noted) {
                Spacer(Modifier.width(4.dp))
                Text(stringResource(R.string.nm_fb_thanks), fontSize = 12.sp, color = quiet)
            }
        }
        if (asking && vote == "down") {
            Text(stringResource(R.string.nm_fb_why), fontSize = 12.sp, color = quiet, modifier = Modifier.padding(start = 4.dp, top = 2.dp))
            FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(4.dp), modifier = Modifier.padding(top = 4.dp)) {
                for (reason in TurnFeedback.DOWN_REASONS) {
                    ReasonPill(downReasonText(reason)) {
                        asking = false
                        noted = true
                        TurnFeedback.vote(sessionId, messageId, "down", reason, keep = true)
                    }
                }
                TextButton(onClick = { asking = false }, modifier = Modifier.height(30.dp)) {
                    Text(stringResource(R.string.nm_fb_skip), fontSize = 12.sp, color = quiet)
                }
            }
        }
    }
}

/**
 * One question after a Stop, in the chat it happened in: why, as one of five codes, or Skip
 * ([TurnFeedback.answerStop]). Drawn where the chat's cards go, while the question is still
 * fresh ([TurnFeedback.STOP_ASK_MS]).
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun StopAskCard(
    ask: TurnFeedback.StopAsk,
    modifier: Modifier = Modifier,
) {
    val quiet = MaterialTheme.colorScheme.onSurfaceVariant
    Surface(
        shape = RoundedCornerShape(20.dp),
        color = MuseTones.surface,
        border = BorderStroke(1.dp, MuseTones.hairline),
        modifier = modifier.fillMaxWidth().widthIn(max = 400.dp),
    ) {
        Column(Modifier.padding(horizontal = 14.dp, vertical = 10.dp)) {
            Text(
                text = stringResource(R.string.nm_stop_why),
                fontSize = 14.sp,
                fontWeight = FontWeight.Medium,
                color = MaterialTheme.colorScheme.onSurface,
            )
            FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(4.dp), modifier = Modifier.padding(top = 8.dp)) {
                for (reason in TurnFeedback.STOP_REASONS) {
                    ReasonPill(stopReasonText(reason)) { TurnFeedback.answerStop(ask, reason) }
                }
                TextButton(onClick = { TurnFeedback.answerStop(ask, null) }, modifier = Modifier.height(30.dp)) {
                    Text(stringResource(R.string.nm_fb_skip), fontSize = 12.sp, color = quiet)
                }
            }
        }
    }
}

@Composable
private fun ReasonPill(label: String, onClick: () -> Unit) {
    Text(
        text = label,
        fontSize = 12.sp,
        color = MaterialTheme.colorScheme.onSurface,
        modifier = Modifier
            .clip(CircleShape)
            .background(MuseTones.fill)
            .clickable(onClick = onClick)
            .padding(horizontal = 12.dp, vertical = 6.dp),
    )
}

@Composable
private fun downReasonText(reason: String): String = stringResource(
    when (reason) {
        "wrong" -> R.string.nm_fb_reason_wrong
        "unhelpful" -> R.string.nm_fb_reason_unhelpful
        "unsafe" -> R.string.nm_fb_reason_unsafe
        "slow" -> R.string.nm_fb_reason_slow
        else -> R.string.nm_fb_reason_other
    },
)

@Composable
private fun stopReasonText(reason: String): String = stringResource(
    when (reason) {
        "wrong" -> R.string.nm_stop_reason_wrong
        "slow" -> R.string.nm_stop_reason_slow
        "changed_mind" -> R.string.nm_stop_reason_changed_mind
        "done" -> R.string.nm_stop_reason_done
        else -> R.string.nm_stop_reason_other
    },
)
