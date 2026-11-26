package lol.patapon.droidlink

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification
import android.app.RemoteInput
import android.os.BatteryManager
import android.os.Build
import android.os.Bundle
import android.provider.Telephony
import java.util.concurrent.Executors
import org.json.JSONObject
import org.json.JSONArray

class NotificationRelayService : NotificationListenerService() {
    private val executor = Executors.newScheduledThreadPool(3)
    private val batteryReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) {
            publishBatteryLevel()
        }
    }

    override fun onCreate() {
        super.onCreate()
        executor.scheduleWithFixedDelay(::pollCommands, 0, 1, java.util.concurrent.TimeUnit.SECONDS)
        val filter = IntentFilter(Intent.ACTION_BATTERY_CHANGED)
        if (Build.VERSION.SDK_INT >= 33) {
            registerReceiver(batteryReceiver, filter, Context.RECEIVER_NOT_EXPORTED)
        } else {
            @Suppress("DEPRECATION")
            registerReceiver(batteryReceiver, filter)
        }
    }

    override fun onDestroy() {
        runCatching { unregisterReceiver(batteryReceiver) }
        DroidLinkRinger.stop()
        executor.shutdownNow()
        super.onDestroy()
    }

    private fun publishBatteryLevel() {
        val level = getSystemService(BatteryManager::class.java)
            ?.getIntProperty(BatteryManager.BATTERY_PROPERTY_CAPACITY)
            ?.takeIf { it in 0..100 } ?: return
        val token = getSharedPreferences(PREFERENCES, MODE_PRIVATE)
            .getString(SESSION_TOKEN, null) ?: return
        executor.execute {
            DroidLinkTransport.post(this, token, "phone", JSONObject().put("batteryPercent", level))
        }
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
        val actionTitles = mutableListOf<String>()
        sbn.notification.actions?.forEachIndexed { index, action ->
            val remoteInput = action.remoteInputs?.firstOrNull()
            val actionTitle = action.title?.toString().orEmpty().take(80)
            actionTitles += actionTitle
            actions.put(
                JSONObject()
                    .put("index", index)
                    .put("title", actionTitle)
                    .put("remoteInputKey", remoteInput?.resultKey.orEmpty().take(120)),
            )
        }
        val category = sbn.notification.category.orEmpty()
        val hasAnswerAction = actionTitles.any { action ->
            val normalized = action.lowercase()
            listOf("answer", "répond", "repond", "décroch", "decroch", "accepter").any { normalized.contains(it) }
        }

        val event = JSONObject()
            .put("app", sbn.packageName)
            .put("title", title.ifBlank { appLabel }.take(180))
            .put("text", text.take(800))
            .put("isMessaging", hasMessagingStyle)
            .put("isSmsApp", defaultSmsPackage != null && sbn.packageName == defaultSmsPackage)
            .put("conversationTitle", extras.getCharSequence(Notification.EXTRA_CONVERSATION_TITLE)?.toString().orEmpty().take(180))
            .put("isGroupConversation", extras.getBoolean(Notification.EXTRA_IS_GROUP_CONVERSATION))
            .put("category", category)
            .put("ongoing", (sbn.notification.flags and Notification.FLAG_ONGOING_EVENT) != 0)
            .put("isIncomingCall", category == Notification.CATEGORY_CALL && hasAnswerAction)
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
        runCatching { JSONObject(body) }.getOrNull()?.let {
            DroidLinkTransport.post(this, token, "events", it)
        }
    }

    private fun pollCommands() {
        val token = getSharedPreferences(PREFERENCES, MODE_PRIVATE)
            .getString(SESSION_TOKEN, null) ?: return
        try {
            val commands = DroidLinkTransport.getCommands(this, token) ?: return
            getSharedPreferences(PREFERENCES, MODE_PRIVATE)
                .edit()
                .putLong("desktop_last_seen_ms", System.currentTimeMillis())
                .apply()
            for (index in 0 until commands.length()) {
                val command = commands.optJSONObject(index) ?: continue
                val key = command.optString("key")
                when (command.optString("action")) {
                    "dismiss" -> dismissNotification(token, key)
                    "open" -> openNotification(key)
                    "snooze" -> if (android.os.Build.VERSION.SDK_INT >= 26) snoozeNotification(key, 60 * 60 * 1000L)
                    "notification_action" -> performNotificationAction(
                        key,
                        command.optInt("actionIndex", -1),
                        command.optString("remoteInputKey"),
                        command.optString("text"),
                        command.optString("actionTitle"),
                    )
                    "ring_phone" -> DroidLinkRinger.ring(this)
                    "stop_phone_ring" -> DroidLinkRinger.stop()
                    "contacts_list" -> DroidLinkContacts.send(this, token)
                    "photo_list" -> DroidLinkMedia.sendRecentImages(this, token)
                    "photo_thumbnail" -> command.optLong("photoId", -1L)
                        .takeIf { it > 0L }
                        ?.let { DroidLinkMedia.sendThumbnail(this, token, it) }
                    "photo_preview" -> command.optLong("photoId", -1L)
                        .takeIf { it > 0L }
                        ?.let { DroidLinkMedia.sendPreview(this, token, it) }
                    "photo_delete" -> command.optLong("photoId", -1L)
                        .takeIf { it > 0L }
                        ?.let { requestPhotoDelete(token, it) }
                    "app_icons" -> DroidLinkAppIcons.sendIcons(
                        this,
                        token,
                        command.optJSONArray("packages") ?: JSONArray(),
                    )
                }
            }
        } catch (_: Exception) {
            // The relay may be temporarily unavailable; the next long poll retries.
        }
    }

    private fun requestPhotoDelete(token: String, photoId: Long) {
        if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission(android.Manifest.permission.POST_NOTIFICATIONS) != android.content.pm.PackageManager.PERMISSION_GRANTED) {
            DroidLinkTransport.post(this, token, "events", JSONObject()
                .put("type", "photo_delete_result")
                .put("id", photoId)
                .put("success", false)
                .put("message", "Autorise les notifications DroidLink dans Android pour afficher la confirmation de suppression."))
            return
        }
        val manager = getSystemService(NotificationManager::class.java)
        if (Build.VERSION.SDK_INT >= 26) {
            manager.createNotificationChannel(
                NotificationChannel(PHOTO_DELETE_CHANNEL, "Actions DroidDesk", NotificationManager.IMPORTANCE_HIGH),
            )
        }
        val intent = Intent(this, PhotoDeleteActivity::class.java)
            .putExtra(PhotoDeleteActivity.EXTRA_PHOTO_ID, photoId)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        val pending = PendingIntent.getActivity(
            this,
            photoId.toInt(),
            intent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        val notification = Notification.Builder(this, PHOTO_DELETE_CHANNEL)
            .setSmallIcon(R.drawable.ic_droidlink_tile)
            .setContentTitle("Confirmer la suppression")
            .setContentText("Ouvre la demande pour supprimer la photo sélectionnée.")
            .setContentIntent(pending)
            .setAutoCancel(true)
            .setCategory(Notification.CATEGORY_RECOMMENDATION)
            .build()
        manager.notify(photoId.toInt(), notification)
    }

    private fun dismissNotification(token: String, key: String) {
        if (key.isBlank()) {
            reportCommandResult(token, "dismiss", key, false, "Clé de notification invalide.")
            return
        }

        dismissNotificationAttempt(token, key, 1)
    }

    private fun dismissNotificationAttempt(token: String, key: String, attempt: Int) {
        val active = try {
            getActiveNotifications().orEmpty().firstOrNull { it.key == key }
        } catch (_: Exception) {
            reportCommandResult(token, "dismiss", key, false, "Accès aux notifications Android indisponible.")
            return
        }

        if (active == null) {
            reportCommandResult(token, "dismiss", key, true, "La notification était déjà absente.")
            return
        }
        if (!active.isClearable) {
            reportCommandResult(token, "dismiss", key, false, "Android marque cette notification comme persistante; DroidLink ne peut pas la supprimer.")
            return
        }

        try {
            cancelNotification(key)
        } catch (_: Exception) {
            reportCommandResult(token, "dismiss", key, false, "Android a refusé la suppression de la notification.")
            return
        }

        executor.schedule({
            val stillActive = try {
                getActiveNotifications().orEmpty().firstOrNull { it.key == key }
            } catch (_: Exception) {
                reportCommandResult(token, "dismiss", key, false, "Impossible de vérifier la suppression sur Android.")
                return@schedule
            }
            when {
                stillActive == null -> reportCommandResult(token, "dismiss", key, true, "Notification supprimée sur Android.")
                !stillActive.isClearable -> reportCommandResult(token, "dismiss", key, false, "Android marque cette notification comme persistante; DroidLink ne peut pas la supprimer.")
                attempt < MAX_DISMISS_ATTEMPTS -> dismissNotificationAttempt(token, key, attempt + 1)
                else -> reportCommandResult(token, "dismiss", key, false, "La notification reste active ou est republiée après plusieurs tentatives.")
            }
        }, DISMISS_VERIFY_DELAY_MS, java.util.concurrent.TimeUnit.MILLISECONDS)
    }

    private fun reportCommandResult(token: String, action: String, key: String, success: Boolean, message: String) {
        RelayDiagnostics.recordNote(message, success)
        val event = JSONObject()
            .put("type", "command_result")
            .put("action", action)
            .put("key", key.take(256))
            .put("success", success)
            .put("message", message.take(200))
            .toString()
        sendEvent(token, event)
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

    companion object {
        private const val PREFERENCES = "droidlink"
        private const val SESSION_TOKEN = "session_token"
        private const val PHOTO_DELETE_CHANNEL = "photo_delete"
        private const val MAX_DISMISS_ATTEMPTS = 3
        private const val DISMISS_VERIFY_DELAY_MS = 1_200L
        private const val KNOWN_APPS = "known_notification_apps"
        private const val BLOCKED_APPS = "blocked_notification_apps"
    }
}
