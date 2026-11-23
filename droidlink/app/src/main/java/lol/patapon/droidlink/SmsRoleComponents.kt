package lol.patapon.droidlink

import android.app.Activity
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.BroadcastReceiver
import android.content.ContentValues
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.IBinder
import android.provider.Telephony
import android.telephony.SmsManager
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp

private const val SMS_CHANNEL = "droidlink_sms"

class SmsComposeActivity : ComponentActivity() {
    @OptIn(ExperimentalMaterial3Api::class)
    override fun onCreate(savedInstanceState: android.os.Bundle?) {
        super.onCreate(savedInstanceState)
        val address = intent.data?.schemeSpecificPart?.substringBefore("?").orEmpty()
        val initialBody = intent.getStringExtra("sms_body").orEmpty()
        setContent {
            DroidLinkTheme {
                var recipient by remember { mutableStateOf(address) }
                var message by remember { mutableStateOf(initialBody) }
                Scaffold(topBar = { TopAppBar(title = { Text("Nouveau SMS") }) }) { padding ->
                    Column(
                        Modifier.fillMaxSize().padding(padding).padding(20.dp),
                        verticalArrangement = Arrangement.spacedBy(14.dp),
                    ) {
                        OutlinedTextField(
                            value = recipient,
                            onValueChange = { recipient = it },
                            modifier = Modifier.fillMaxWidth(),
                            label = { Text("Destinataire") },
                            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Phone),
                            singleLine = true,
                            shape = RoundedCornerShape(16.dp),
                        )
                        OutlinedTextField(
                            value = message,
                            onValueChange = { message = it },
                            modifier = Modifier.fillMaxWidth().weight(1f),
                            label = { Text("Message") },
                            shape = RoundedCornerShape(16.dp),
                        )
                        Text("Cette fenêtre envoie des SMS. Les MMS et RCS ne sont pas pris en charge ici.", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        Button(
                            onClick = { sendSms(this@SmsComposeActivity, recipient, message) },
                            enabled = recipient.isNotBlank() && message.isNotBlank(),
                            modifier = Modifier.fillMaxWidth(),
                        ) { Text("Envoyer") }
                    }
                }
            }
        }
    }
}

class SmsDeliverReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val parts = Telephony.Sms.Intents.getMessagesFromIntent(intent)
        if (parts.isNullOrEmpty()) return
        val address = parts.first().originatingAddress.orEmpty()
        val body = parts.joinToString(separator = "") { it.messageBody.orEmpty() }
        val timestamp = parts.first().timestampMillis
        runCatching {
            val threadId = Telephony.Threads.getOrCreateThreadId(context, address)
            val values = ContentValues().apply {
                put("address", address)
                put("body", body)
                put("date", timestamp)
                put("read", 0)
                put("type", Telephony.Sms.MESSAGE_TYPE_INBOX)
                put("thread_id", threadId)
            }
            context.contentResolver.insert(Telephony.Sms.Inbox.CONTENT_URI, values)
        }.onSuccess {
            notifyMessage(context, "Nouveau SMS", address.ifBlank { "Message reçu" })
        }
    }
}

class MmsDeliverReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        // Android routes MMS delivery exclusively to the SMS role holder. Keep the
        // limitation visible until DroidLink has a complete carrier MMS decoder.
        notifyMessage(context, "MMS reçu", "Les pièces jointes MMS ne sont pas encore prises en charge par DroidLink.")
    }
}

class RespondViaMessageService : Service() {
    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val address = intent?.data?.schemeSpecificPart?.substringBefore("?").orEmpty()
        val body = intent?.getStringExtra(Intent.EXTRA_TEXT).orEmpty()
        if (address.isNotBlank() && body.isNotBlank()) sendSms(this, address, body)
        stopSelf(startId)
        return START_NOT_STICKY
    }
}

private fun sendSms(context: Context, address: String, body: String) {
    if (address.isBlank() || body.isBlank()) return
    runCatching {
        @Suppress("DEPRECATION")
        val manager = SmsManager.getDefault()
        val parts = manager.divideMessage(body)
        if (parts.size > 1) manager.sendMultipartTextMessage(address, null, parts, null, null)
        else manager.sendTextMessage(address, null, body, null, null)
        val values = ContentValues().apply {
            put("address", address)
            put("body", body)
            put("date", System.currentTimeMillis())
            put("read", 1)
            put("type", Telephony.Sms.MESSAGE_TYPE_SENT)
            put("thread_id", Telephony.Threads.getOrCreateThreadId(context, address))
        }
        context.contentResolver.insert(Telephony.Sms.Sent.CONTENT_URI, values)
        Toast.makeText(context, "SMS envoyé", Toast.LENGTH_SHORT).show()
        if (context is Activity) context.finish()
    }.onFailure {
        Toast.makeText(context, "Échec de l’envoi SMS : ${it.localizedMessage ?: "erreur Android"}", Toast.LENGTH_LONG).show()
    }
}

private fun notifyMessage(context: Context, title: String, text: String) {
    val manager = context.getSystemService(NotificationManager::class.java)
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
        manager.createNotificationChannel(NotificationChannel(SMS_CHANNEL, "Messages DroidLink", NotificationManager.IMPORTANCE_DEFAULT))
    }
    val openApp = PendingIntent.getActivity(
        context,
        0,
        Intent(context, MainActivity::class.java),
        PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
    )
    val notification = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
        Notification.Builder(context, SMS_CHANNEL)
    } else {
        @Suppress("DEPRECATION")
        Notification.Builder(context)
    }.setSmallIcon(R.drawable.ic_droidlink_tile)
        .setContentTitle(title)
        .setContentText(text)
        .setContentIntent(openApp)
        .setAutoCancel(true)
        .setVisibility(Notification.VISIBILITY_PRIVATE)
        .build()
    manager.notify((System.currentTimeMillis() % Int.MAX_VALUE).toInt(), notification)
}
