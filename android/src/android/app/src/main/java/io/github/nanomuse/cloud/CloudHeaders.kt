package io.github.nanomuse.cloud

import android.content.Context
import com.openminis.app.data.model.ProviderInstance
import io.github.nanomuse.hub.Hub

/**
 * The two headers a chat request to nanoMuse Cloud carries for the account's record (relay
 * 0.29, docs/cloud.md): which device the turn came from and which conversation it belongs to.
 * Plain ids, never a key or a name.
 *
 * The device is fixed per installation (the hub's id), so it is set once on the Cloud's
 * provider row by `ProviderFactory`; the conversation is the same stable id the OpenCode
 * session header uses, a hash of the conversation's first user message, which every device of
 * the account computes alike for a synced conversation and which names no content.
 * `OpenAIProvider.buildRequest` adds it to a request whose row carries [DEVICE]. Any other
 * provider gets neither. The iOS twin is NanoMuseCloudHeaders.swift.
 */
object CloudHeaders {
    const val DEVICE = "X-NanoMuse-Device"
    const val CONVERSATION = "X-NanoMuse-Conversation"

    /** The headers for a provider row: the device for the Cloud's instance, nothing for any other. */
    fun forInstance(context: Context?, instance: ProviderInstance): Map<String, String> {
        if (context == null || NanoMuseCloud.instance(context)?.id != instance.id) return emptyMap()
        return mapOf(DEVICE to Hub.deviceId(context).take(80))
    }

    /** Whether a provider's fixed headers mark it as the Cloud's row. */
    fun isCloud(extraHeaders: Map<String, String>): Boolean = extraHeaders.containsKey(DEVICE)
}
