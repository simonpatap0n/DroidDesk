package lol.patapon.droidlink

import android.content.Context
import android.content.Intent
import android.app.role.RoleManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.telecom.Call
import android.telecom.CallAudioState
import android.telecom.InCallService
import android.telecom.TelecomManager
import android.view.WindowManager
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.delay
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.Executors

data class DroidLinkCallState(
    val number: String,
    val displayName: String,
    val state: String,
    val audioRoute: Int = CallAudioState.ROUTE_EARPIECE,
    val supportedAudioRoutes: Int = CallAudioState.ROUTE_EARPIECE,
    val muted: Boolean = false,
)

object DroidLinkTelecom {
    private const val PREFS = "droidlink"
    private const val TOKEN_KEY = "session_token"
    private const val PORT = 17891
    private val executor = Executors.newSingleThreadExecutor()
    private val mutableCallState = MutableStateFlow<DroidLinkCallState?>(null)
    val callState = mutableCallState.asStateFlow()

    @Volatile private var currentCall: Call? = null
    @Volatile private var currentService: InCallService? = null
    @Volatile private var currentAudioState: CallAudioState? = null
    @Volatile private var preferBluetoothForCurrentCall = false
    private var callback: Call.Callback? = null

    @Synchronized
    fun attach(service: InCallService, call: Call) {
        currentService = service
        currentCall?.let { old -> callback?.let(old::unregisterCallback) }
        currentCall = call
        currentAudioState = null
        preferBluetoothForCurrentCall = true
        val listener = object : Call.Callback() {
            override fun onStateChanged(changedCall: Call, state: Int) = publishState(service, changedCall)
            override fun onDetailsChanged(changedCall: Call, details: Call.Details) = publishState(service, changedCall)
        }
        callback = listener
        call.registerCallback(listener)
        publishState(service, call)
    }

    @Synchronized
    fun detach(call: Call) {
        if (currentCall !== call) return
        callback?.let(call::unregisterCallback)
        callback = null
        currentCall = null
        currentAudioState = null
        preferBluetoothForCurrentCall = false
        mutableCallState.value = null
        val service = currentService
        if (service != null) publishDesktop(service, null)
    }

    fun placeCall(context: Context, number: String): Boolean {
        if (number.isBlank()) return false
        if (context.checkSelfPermission(android.Manifest.permission.CALL_PHONE) != android.content.pm.PackageManager.PERMISSION_GRANTED) return false
        return runCatching {
            val telecom = context.getSystemService(TelecomManager::class.java)
            preferBluetoothForCurrentCall = true
            telecom.placeCall(Uri.fromParts("tel", number, null), Bundle())
            true
        }.getOrDefault(false)
    }

    fun answer() {
        runCatching { currentCall?.answer(0) }
    }

    fun reject() {
        runCatching { currentCall?.reject(false, null) }
    }

    fun hangUp() {
        runCatching { currentCall?.disconnect() }
    }

    fun setAudioRoute(route: Int) {
        preferBluetoothForCurrentCall = false
        runCatching { currentService?.setAudioRoute(route) }
    }

    fun setMuted(muted: Boolean) {
        runCatching { currentService?.setMuted(muted) }
    }

    @Synchronized
    fun updateAudioState(service: InCallService, audioState: CallAudioState) {
        currentService = service
        currentAudioState = audioState
        if (currentCall != null && preferBluetoothForCurrentCall) {
            val bluetoothAvailable = (audioState.supportedRouteMask and CallAudioState.ROUTE_BLUETOOTH) != 0
            if (bluetoothAvailable && audioState.route != CallAudioState.ROUTE_BLUETOOTH) {
                preferBluetoothForCurrentCall = false
                runCatching { service.setAudioRoute(CallAudioState.ROUTE_BLUETOOTH) }
            } else if (bluetoothAvailable && audioState.route == CallAudioState.ROUTE_BLUETOOTH) {
                preferBluetoothForCurrentCall = false
            }
        }
        currentCall?.let { publishState(service, it) }
    }

    @Synchronized
    private fun publishState(service: InCallService, call: Call) {
        val details = call.details
        val state = when (call.state) {
            Call.STATE_RINGING -> "RINGING"
            Call.STATE_DIALING, Call.STATE_CONNECTING, Call.STATE_SELECT_PHONE_ACCOUNT -> "DIALING"
            Call.STATE_ACTIVE -> "ACTIVE"
            Call.STATE_HOLDING -> "HOLDING"
            Call.STATE_DISCONNECTED -> "DISCONNECTED"
            else -> "CONNECTING"
        }
        val snapshot = DroidLinkCallState(
            number = details.handle?.schemeSpecificPart.orEmpty(),
            displayName = details.callerDisplayName?.toString().orEmpty(),
            state = state,
            audioRoute = currentAudioState?.route ?: CallAudioState.ROUTE_EARPIECE,
            supportedAudioRoutes = currentAudioState?.supportedRouteMask ?: CallAudioState.ROUTE_EARPIECE,
            muted = currentAudioState?.isMuted ?: false,
        )
        mutableCallState.value = snapshot
        publishDesktop(service, snapshot)
        if (state == "RINGING" || state == "ACTIVE") {
            runCatching {
                service.startActivity(
                    Intent(service, DroidLinkCallActivity::class.java)
                        .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP),
                )
            }
        }
    }

    private fun publishDesktop(context: Context, snapshot: DroidLinkCallState?) {
        val token = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .getString(TOKEN_KEY, null) ?: return
        val event = JSONObject()
            .put("state", snapshot?.state ?: "IDLE")
            .put("number", snapshot?.number.orEmpty())
            .put("displayName", snapshot?.displayName.orEmpty())
            .put("audioRoute", snapshot?.audioRoute?.let(::audioRouteName) ?: "none")
            .put("supportedAudioRoutes", snapshot?.supportedAudioRoutes ?: 0)
            .put("muted", snapshot?.muted ?: false)
            .put("dialerRole", if (Build.VERSION.SDK_INT >= 29) {
                context.getSystemService(RoleManager::class.java)?.isRoleHeld(RoleManager.ROLE_DIALER) == true
            } else {
                context.getSystemService(TelecomManager::class.java)?.defaultDialerPackage == context.packageName
            })
            .toString()
        executor.execute {
            val connection = URL("http://localhost:$PORT/call").openConnection() as HttpURLConnection
            try {
                connection.requestMethod = "POST"
                connection.connectTimeout = 2_500
                connection.readTimeout = 2_500
                connection.doOutput = true
                connection.setRequestProperty("Authorization", "Bearer $token")
                connection.setRequestProperty("Content-Type", "application/json; charset=utf-8")
                connection.outputStream.use { it.write(event.toByteArray(Charsets.UTF_8)) }
                connection.responseCode
            } catch (_: Exception) {
                // ADB may be disconnected while the call continues on the phone.
            } finally {
                connection.disconnect()
            }
        }
    }

    private fun audioRouteName(route: Int): String = when (route) {
        CallAudioState.ROUTE_BLUETOOTH -> "bluetooth"
        CallAudioState.ROUTE_SPEAKER -> "speaker"
        CallAudioState.ROUTE_WIRED_HEADSET -> "wired"
        CallAudioState.ROUTE_EARPIECE -> "earpiece"
        else -> "unknown"
    }
}

class DroidLinkInCallService : InCallService() {
    override fun onCallAdded(call: Call) {
        super.onCallAdded(call)
        DroidLinkTelecom.attach(this, call)
    }

    override fun onCallRemoved(call: Call) {
        DroidLinkTelecom.detach(call)
        super.onCallRemoved(call)
    }

    @Suppress("DEPRECATION")
    override fun onCallAudioStateChanged(audioState: CallAudioState) {
        super.onCallAudioStateChanged(audioState)
        DroidLinkTelecom.updateAudioState(this, audioState)
    }
}

class DroidLinkCallActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (Build.VERSION.SDK_INT >= 27) {
            setShowWhenLocked(true)
            setTurnScreenOn(true)
        }
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        setContent {
            DroidLinkTheme {
                val call by DroidLinkTelecom.callState.collectAsState()
                LaunchedEffect(call) {
                    if (call == null) {
                        delay(1_200)
                        finish()
                    }
                }
                Column(
                    Modifier.fillMaxSize().padding(28.dp),
                    horizontalAlignment = Alignment.CenterHorizontally,
                    verticalArrangement = Arrangement.Center,
                ) {
                    Text(call?.let { activeCall -> activeCall.displayName.ifBlank { activeCall.number } } ?: "Appel", style = MaterialTheme.typography.headlineMedium, fontWeight = FontWeight.SemiBold)
                    Text(call?.number.orEmpty(), style = MaterialTheme.typography.titleMedium)
                    Text(call?.state?.let(::callStateLabel) ?: "Aucun appel", modifier = Modifier.padding(top = 12.dp), color = MaterialTheme.colorScheme.onSurfaceVariant)
                    Row(Modifier.fillMaxWidth().padding(top = 28.dp), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                        if (call?.state == "RINGING") {
                            OutlinedButton(onClick = { DroidLinkTelecom.reject() }, modifier = Modifier.weight(1f)) { Text("Refuser") }
                            Button(onClick = { DroidLinkTelecom.answer() }, modifier = Modifier.weight(1f)) { Text("Répondre") }
                        } else if (call != null) {
                            Button(onClick = { DroidLinkTelecom.hangUp() }, modifier = Modifier.weight(1f)) { Text("Terminer l’appel") }
                        }
                    }
                    call?.let { activeCall ->
                        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            OutlinedButton(onClick = { DroidLinkTelecom.setAudioRoute(CallAudioState.ROUTE_BLUETOOTH) }, modifier = Modifier.weight(1f)) { Text("Bluetooth") }
                            OutlinedButton(onClick = { DroidLinkTelecom.setAudioRoute(CallAudioState.ROUTE_SPEAKER) }, modifier = Modifier.weight(1f)) { Text("Haut-parleur") }
                            OutlinedButton(onClick = { DroidLinkTelecom.setMuted(!activeCall.muted) }, modifier = Modifier.weight(1f)) { Text(if (activeCall.muted) "Micro ouvert" else "Muet") }
                        }
                    }
                }
            }
        }
    }
}

internal fun callStateLabel(state: String): String = when (state) {
    "RINGING" -> "Appel entrant"
    "DIALING" -> "Appel en cours…"
    "ACTIVE" -> "En communication"
    "HOLDING" -> "En attente"
    "DISCONNECTED" -> "Appel terminé"
    else -> "Connexion…"
}
