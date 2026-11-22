package lol.patapon.droidlink

import android.app.Notification
import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification
import android.app.RemoteInput
import android.content.Intent
import android.os.Bundle
import android.provider.Telephony
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.Executors
import org.json.JSONObject
import org.json.JSONArray

class NotificationRelayService : NotificationListenerService() {
    private val executor = Executors.newSingleThreadScheduledExecutor()

    override fun onCreate() {
        super.onCreate()
        executor.scheduleWithFixedDelay(::pollCommands, 2, 3, java.util.concurrent.TimeUnit.SECONDS)
    }

    override fun onNotificationPosted(sbn: StatusBarNotification) {
        relayNotification(sbn)
    }

    override fun onListenerConnected() {
        super.onListenerConnected()
        executor.execute {
            runCatching { getActiveNotifications().orEmpty().forEach(::relayNotification) }
        }
    }

    private fun relayNotification(sbn: StatusBarNotification) {
        if (sbn.packageName == packageName) return

        val preferences = getSharedPreferences(PREFERENCES, MODE_PRIVATE)
        val knownApps = preferences.getStringSet(KNOWN_APPS, emptySet()).orEmpty().toMutableSet()
        knownApps.add(sbn.packageName)
        preferences.edit().putStringSet(KNOWN_APPS, knownApps).apply()
        if (sbn.packageName in preferences.getStringSet(BLOCKED_APPS, emptySet()).orEmpty()) return

        val token = preferences
            .getString(SESSION_TOKEN, null) ?: return
        val extras = sbn.notification.extras
        val title = extras.getCharSequence("android.title")?.toString().orEmpty()
        val text = extras.getCharSequence("android.text")?.toString().orEmpty()
        val defaultSmsPackage = Telephony.Sms.getDefaultSmsPackage(this)
        @Suppress("DEPRECATION")
        val appLabel = runCatching {
            packageManager.getApplicationLabel(packageManager.getApplicationInfo(sbn.packageName, 0)).toString()
        }.getOrDefault(sbn.packageName)
        val messages = Notification.MessagingStyle.Message.getMessagesFromBundleArray(
            extras.getParcelableArray(Notification.EXTRA_MESSAGES),
        )
        val hasMessagingStyle = extras.containsKey(Notification.EXTRA_MESSAGES)
        val recentMessages = JSONArray()
        messages.takeLast(20).forEach { message ->
            val sender = if (android.os.Build.VERSION.SDK_INT >= 28) {
                message.senderPerson?.name?.toString().orEmpty()
            } else {
                @Suppress("DEPRECATION")
                message.sender?.toString().orEmpty()
            }
            recentMessages.put(
                JSONObject()
                    .put("sender", sender.take(120))
                    .put("text", message.text?.toString().orEmpty().take(600))
                    .put("timestamp", message.timestamp)
                    .put("mediaType", message.dataMimeType.orEmpty().take(80)),
            )
        }
        val actions = JSONArray()
        sbn.notification.actions?.forEachIndexed { index, action ->
            val remoteInput = action.remoteInputs?.firstOrNull()
            actions.put(
                JSONObject()
                    .put("index", index)
                    .put("title", action.title?.toString().orEmpty().take(80))
                    .put("remoteInputKey", remoteInput?.resultKey.orEmpty().take(120)),
            )
        }

        val event = JSONObject()
            .put("app", sbn.packageName)
            .put("title", title.ifBlank { appLabel }.take(180))
            .put("text", text.take(800))
            .put("isMessaging", hasMessagingStyle)
            .put("isSmsApp", defaultSmsPackage != null && sbn.packageName == defaultSmsPackage)
            .put("conversationTitle", extras.getCharSequence(Notification.EXTRA_CONVERSATION_TITLE)?.toString().orEmpty().take(180))
            .put("isGroupConversation", extras.getBoolean(Notification.EXTRA_IS_GROUP_CONVERSATION))
            .put("messages", recentMessages)
            .put("postedAt", sbn.postTime)
            .put("key", sbn.key)
            .put("actions", actions)
            .toString()

        executor.execute { sendEvent(token, event) }
    }

    override fun onNotificationRemoved(sbn: StatusBarNotification) {
        val token = getSharedPreferences(PREFERENCES, MODE_PRIVATE)
            .getString(SESSION_TOKEN, null) ?: return
        val event = JSONObject().put("key", sbn.key).put("removed", true).toString()
        executor.execute { sendEvent(token, event) }
    }

    private fun sendEvent(token: String, body: String) {
        val connection = (URL("http://localhost:$DROIDLINK_PORT/events")
            .openConnection() as HttpURLConnection)
        try {
            connection.requestMethod = "POST"
            connection.connectTimeout = 2_500
            connection.readTimeout = 2_500
            connection.doOutput = true
            connection.setRequestProperty("Authorization", "Bearer $token")
            connection.setRequestProperty("Content-Type", "application/json; charset=utf-8")
            connection.outputStream.use { it.write(body.toByteArray(Charsets.UTF_8)) }
            if (connection.responseCode >= 400) connection.errorStream?.close()
        } catch (_: Exception) {
            // The desktop bridge may be closed; Android keeps the notification locally.
        } finally {
            connection.disconnect()
        }
    }

    private fun pollCommands() {
        val token = getSharedPreferences(PREFERENCES, MODE_PRIVATE)
            .getString(SESSION_TOKEN, null) ?: return
        val connection = (URL("http://localhost:$DROIDLINK_PORT/commands")
            .openConnection() as HttpURLConnection)
        try {
            connection.requestMethod = "GET"
            connection.connectTimeout = 2_500
            connection.readTimeout = 2_500
            connection.setRequestProperty("Authorization", "Bearer $token")
            if (connection.responseCode !in 200..299) return
            val response = connection.inputStream.bufferedReader().use { it.readText() }
            val commands = JSONObject(response).optJSONArray("commands") ?: JSONArray()
            for (index in 0 until commands.length()) {
                val command = commands.optJSONObject(index) ?: continue
                val key = command.optString("key")
                when (command.optString("action")) {
                    "dismiss" -> cancelNotification(key)
                    "open" -> openNotification(key)
                    "snooze" -> if (android.os.Build.VERSION.SDK_INT >= 26) snoozeNotification(key, 60 * 60 * 1000L)
                    "notification_action" -> performNotificationAction(
                        key,
                        command.optInt("actionIndex", -1),
                        command.optString("remoteInputKey"),
                        command.optString("text"),
                        command.optString("actionTitle"),
                    )
                    "place_call" -> {
                        val rawNumber = command.optString("number")
                        val validNumber = rawNumber.matches(Regex("[+0-9*#() .-]{3,40}"))
                        val placed = validNumber && DroidLinkTelecom.placeCall(
                            this,
                            rawNumber.filter { char -> char.isDigit() || char in "+*#" },
                        )
                        if (!placed) {
                            val message = if (android.os.Build.VERSION.SDK_INT >= 23 &&
                                checkSelfPermission(android.Manifest.permission.CALL_PHONE) != android.content.pm.PackageManager.PERMISSION_GRANTED
                            ) {
                                "Autorise les appels sortants dans DroidLink → Accès."
                            } else {
                                "Android n’a pas pu lancer l’appel. Vérifie que DroidLink est l’application Téléphone par défaut."
                            }
                            sendEvent(token, JSONObject().put("type", "call_command_error").put("action", "place_call").put("message", message).toString())
                        }
                    }
                    "answer_call" -> DroidLinkTelecom.answer()
                    "reject_call" -> DroidLinkTelecom.reject()
                    "hangup_call" -> DroidLinkTelecom.hangUp()
                    "call_audio_bluetooth" -> DroidLinkTelecom.setAudioRoute(android.telecom.CallAudioState.ROUTE_BLUETOOTH)
                    "call_audio_speaker" -> DroidLinkTelecom.setAudioRoute(android.telecom.CallAudioState.ROUTE_SPEAKER)
                    "call_audio_earpiece" -> DroidLinkTelecom.setAudioRoute(android.telecom.CallAudioState.ROUTE_EARPIECE)
                    "call_mute_on" -> DroidLinkTelecom.setMuted(true)
                    "call_mute_off" -> DroidLinkTelecom.setMuted(false)
                    "photo_list" -> DroidLinkMedia.sendRecentImages(this, token)
                    "photo_thumbnail" -> command.optLong("photoId", -1L)
                        .takeIf { it > 0L }
                        ?.let { DroidLinkMedia.sendThumbnail(this, token, it) }
                    "app_icons" -> DroidLinkAppIcons.sendIcons(
                        this,
                        token,
                        command.optJSONArray("packages") ?: JSONArray(),
                    )
                    "call_log_list" -> DroidLinkCallLog.sendRecentCalls(this, token)
                }
            }
        } catch (_: Exception) {
            // The ADB reverse tunnel may be temporarily unavailable.
        } finally {
            connection.disconnect()
        }
    }

    private fun openNotification(key: String) {
        if (key.isBlank()) return
        try {
            getActiveNotifications().firstOrNull { it.key == key }
                ?.notification?.contentIntent?.send()
        } catch (_: Exception) {
            // The source app may have canceled its notification action.
        }
    }

    private fun performNotificationAction(key: String, actionIndex: Int, remoteInputKey: String, text: String, expectedTitle: String) {
        if (key.isBlank() || actionIndex < 0) return
        try {
            val notification = getActiveNotifications().firstOrNull { it.key == key }?.notification ?: return
            val action = notification.actions?.getOrNull(actionIndex) ?: return
            if (expectedTitle.isNotBlank() && action.title?.toString() != expectedTitle) return
            val fillIn = Intent()
            if (remoteInputKey.isNotBlank() && text.isNotBlank()) {
                val remoteInputs = action.remoteInputs ?: return
                if (remoteInputs.none { it.resultKey == remoteInputKey }) return
                val results = Bundle().apply { putCharSequence(remoteInputKey, text.take(2_000)) }
                RemoteInput.addResultsToIntent(remoteInputs, fillIn, results)
            }
            action.actionIntent?.send(this, 0, fillIn)
        } catch (_: Exception) {
            // Android or the source app may have canceled the action.
        }
    }

    override fun onDestroy() {
        executor.shutdownNow()
        super.onDestroy()
    }

    companion object {
        private const val PREFERENCES = "droidlink"
        private const val SESSION_TOKEN = "session_token"
        private const val KNOWN_APPS = "known_notification_apps"
        private const val BLOCKED_APPS = "blocked_notification_apps"
        private const val DROIDLINK_PORT = 17891
    }
}
