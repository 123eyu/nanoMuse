package io.github.nanomuse.net

import java.io.IOException
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException
import kotlinx.coroutines.suspendCancellableCoroutine
import okhttp3.Call
import okhttp3.Callback
import okhttp3.Response

/**
 * Runs the call and suspends until it answers, the way a coroutine expects: cancelling the
 * coroutine cancels the call, which closes the socket at once. `execute()` inside
 * `withContext(Dispatchers.IO)` does not notice cancellation; it keeps the IO thread parked in
 * the read until OkHttp's own timeout, so leaving the sign-in page or the network page used to
 * leave the request running for up to half a minute (#336).
 *
 * Throws [IOException] as `execute()` would, and `CancellationException` when the coroutine
 * was cancelled; a response that arrives after the cancellation is closed, not leaked.
 */
suspend fun Call.await(): Response = suspendCancellableCoroutine { cont ->
    enqueue(object : Callback {
        override fun onResponse(call: Call, response: Response) {
            cont.resume(response) { response.close() }
        }

        override fun onFailure(call: Call, e: IOException) {
            if (!cont.isCancelled) cont.resumeWithException(e)
        }
    })
    cont.invokeOnCancellation { cancel() }
}
