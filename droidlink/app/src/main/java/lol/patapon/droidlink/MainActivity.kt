package lol.patapon.droidlink

import android.Manifest
import android.content.Context
import android.content.Intent
import android.app.role.RoleManager
import android.os.BatteryManager
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import android.provider.Settings.Secure
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.BatteryFull
import androidx.compose.material.icons.outlined.Call
import androidx.compose.material.icons.outlined.Home
import androidx.compose.material.icons.outlined.Link
import androidx.compose.material.icons.outlined.Notifications
import androidx.compose.material.icons.outlined.PhoneAndroid
import androidx.compose.material.icons.outlined.Security
import androidx.compose.material.icons.outlined.Settings
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CenterAlignedTopAppBar
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ElevatedCard
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

private const val PREFS = "droidlink"
private const val TOKEN_KEY = "session_token"
private const val DROIDLINK_PORT = 17891

class MainActivity : ComponentActivity() {
    private var incomingDialNumber by mutableStateOf("")
    private var phoneNavigationRequest by mutableIntStateOf(0)

    companion object {
        const val EXTRA_OPEN_PHONE = "lol.patapon.droidlink.OPEN_PHONE"
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        incomingDialNumber = intent?.data?.schemeSpecificPart.orEmpty()
        if (intent?.getBooleanExtra(EXTRA_OPEN_PHONE, false) == true) phoneNavigationRequest += 1
        setContent { DroidLinkTheme { DroidLinkApp(incomingDialNumber, phoneNavigationRequest) } }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        incomingDialNumber = intent.data?.schemeSpecificPart.orEmpty()
        if (intent.getBooleanExtra(EXTRA_OPEN_PHONE, false)) phoneNavigationRequest += 1
    }
}

private enum class DroidLinkTab(val label: String, val icon: ImageVector) {
    Home("Accueil", Icons.Outlined.Home),
    Phone("Appels", Icons.Outlined.Call),
    Notifications("Alertes", Icons.Outlined.Notifications),
    Settings("Accès", Icons.Outlined.Settings),
}

private data class PairingResult(val token: String? = null, val error: String? = null)

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun DroidLinkApp(initialDialNumber: String, phoneNavigationRequest: Int) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    var selectedTab by rememberSaveable { mutableStateOf(DroidLinkTab.Home) }
    var sessionToken by remember { mutableStateOf(readToken(context)) }
    var notificationAccess by remember { mutableStateOf(hasNotificationAccess(context)) }
    var pairingCode by rememberSaveable { mutableStateOf("") }
    var pairingStatus by rememberSaveable { mutableStateOf("") }
    var pairingBusy by remember { mutableStateOf(false) }
    var dialerRole by remember { mutableStateOf(hasDialerRole(context)) }
    var mediaAccess by remember { mutableStateOf(DroidLinkMedia.hasAccess(context)) }
    var callLogAccess by remember { mutableStateOf(DroidLinkCallLog.hasAccess(context)) }
    var callPhoneAccess by remember { mutableStateOf(hasCallPhoneAccess(context)) }
    var phoneNumber by rememberSaveable { mutableStateOf(initialDialNumber) }
    val callState by DroidLinkTelecom.callState.collectAsState()

    LaunchedEffect(initialDialNumber) {
        if (initialDialNumber.isNotBlank()) {
            phoneNumber = initialDialNumber
            selectedTab = DroidLinkTab.Phone
        }
    }

    LaunchedEffect(phoneNavigationRequest) {
        if (phoneNavigationRequest > 0) selectedTab = DroidLinkTab.Phone
    }

    val settingsLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.StartActivityForResult(),
    ) {
        notificationAccess = hasNotificationAccess(context)
        dialerRole = hasDialerRole(context)
        callLogAccess = DroidLinkCallLog.hasAccess(context)
        callPhoneAccess = hasCallPhoneAccess(context)
        scope.launch { withContext(Dispatchers.IO) { publishPhoneCapabilities(context) } }
    }

    val requestNotificationAccess = {
        settingsLauncher.launch(Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS))
    }

    val mediaPermissionLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) {
        mediaAccess = DroidLinkMedia.hasAccess(context)
    }
    val requestMediaAccess = {
        val permission = if (Build.VERSION.SDK_INT >= 33) Manifest.permission.READ_MEDIA_IMAGES
        else Manifest.permission.READ_EXTERNAL_STORAGE
        if (!DroidLinkMedia.hasAccess(context)) mediaPermissionLauncher.launch(permission)
    }

    val callLogPermissionLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) {
        callLogAccess = DroidLinkCallLog.hasAccess(context)
        scope.launch { withContext(Dispatchers.IO) { publishPhoneCapabilities(context) } }
    }
    val requestCallLogAccess = {
        if (!DroidLinkCallLog.hasAccess(context)) callLogPermissionLauncher.launch(Manifest.permission.READ_CALL_LOG)
    }

    val callPhonePermissionLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) {
        callPhoneAccess = hasCallPhoneAccess(context)
        if (dialerRole && !DroidLinkCallLog.hasAccess(context)) {
            callLogPermissionLauncher.launch(Manifest.permission.READ_CALL_LOG)
        }
        scope.launch { withContext(Dispatchers.IO) { publishPhoneCapabilities(context) } }
    }
    val requestCallPhoneAccess = {
        if (!hasCallPhoneAccess(context)) callPhonePermissionLauncher.launch(Manifest.permission.CALL_PHONE)
    }

    val dialerRoleLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.StartActivityForResult(),
    ) {
        dialerRole = hasDialerRole(context)
        if (dialerRole && !hasCallPhoneAccess(context)) {
            callPhonePermissionLauncher.launch(Manifest.permission.CALL_PHONE)
        } else if (dialerRole && !DroidLinkCallLog.hasAccess(context)) {
            callLogPermissionLauncher.launch(Manifest.permission.READ_CALL_LOG)
        }
        scope.launch { withContext(Dispatchers.IO) { publishPhoneCapabilities(context) } }
    }
    val requestDialerRole = {
        val manager = if (Build.VERSION.SDK_INT >= 29) context.getSystemService(RoleManager::class.java) else null
        if (manager != null && manager.isRoleAvailable(RoleManager.ROLE_DIALER)) {
            dialerRoleLauncher.launch(manager.createRequestRoleIntent(RoleManager.ROLE_DIALER))
        } else {
            settingsLauncher.launch(Intent(Settings.ACTION_MANAGE_DEFAULT_APPS_SETTINGS))
        }
    }

    val pairWithDesktop: (String) -> Unit = { code ->
        pairingBusy = true
        pairingStatus = "Connexion à DroidDesk…"
        scope.launch {
            val result = withContext(Dispatchers.IO) { requestPairing(context, code) }
            pairingBusy = false
            if (result.token != null) {
                sessionToken = result.token
                pairingCode = ""
                pairingStatus = "DroidDesk est associé à ce téléphone."
            } else {
                pairingStatus = result.error ?: "Connexion impossible. Vérifie le code et ADB."
            }
        }
    }

    val disconnect = {
        val token = sessionToken
        scope.launch(Dispatchers.IO) { if (token != null) requestUnpair(token) }
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().remove(TOKEN_KEY).apply()
        sessionToken = null
        pairingStatus = "DroidDesk a été dissocié."
    }

    Scaffold(
        topBar = {
            CenterAlignedTopAppBar(
                title = {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Icon(Icons.Outlined.Link, contentDescription = null, tint = MaterialTheme.colorScheme.primary)
                        Spacer(Modifier.width(8.dp))
                        Text("DroidLink", fontWeight = FontWeight.SemiBold)
                    }
                },
            )
        },
        bottomBar = {
            NavigationBar {
                DroidLinkTab.entries.forEach { tab ->
                    NavigationBarItem(
                        selected = selectedTab == tab,
                        onClick = { selectedTab = tab },
                        icon = { Icon(tab.icon, contentDescription = null) },
                        label = { Text(tab.label) },
                    )
                }
            }
        },
    ) { padding ->
        when (selectedTab) {
            DroidLinkTab.Home -> HomeScreen(
                modifier = Modifier.padding(padding),
                paired = sessionToken != null,
                notificationAccess = notificationAccess,
                code = pairingCode,
                pairingStatus = pairingStatus,
                pairingBusy = pairingBusy,
                onCodeChange = { pairingCode = it.filter(Char::isDigit).take(6) },
                onPair = pairWithDesktop,
                onUnpair = disconnect,
                onEnableNotifications = requestNotificationAccess,
                onOpenPermissions = { selectedTab = DroidLinkTab.Settings },
            )
            DroidLinkTab.Phone -> PhoneScreen(
                modifier = Modifier.padding(padding),
                number = phoneNumber,
                call = callState,
                dialerRole = dialerRole,
                onNumberChange = { phoneNumber = it.filter { char -> char.isDigit() || char in "+*#" }.take(32) },
                onRequestDialer = requestDialerRole,
                onCall = {
                    val number = phoneNumber.trim()
                    if (dialerRole && DroidLinkTelecom.placeCall(context, number)) {
                        phoneNumber = ""
                    } else if (number.isNotBlank()) {
                        context.startActivity(Intent(Intent.ACTION_DIAL, android.net.Uri.fromParts("tel", number, null)))
                    }
                },
                onAnswer = { DroidLinkTelecom.answer() },
                onHangUp = { DroidLinkTelecom.hangUp() },
            )
            DroidLinkTab.Notifications -> NotificationsScreen(
                modifier = Modifier.padding(padding),
                paired = sessionToken != null,
                accessGranted = notificationAccess,
                onEnable = requestNotificationAccess,
            )
            DroidLinkTab.Settings -> SettingsScreen(
                modifier = Modifier.padding(padding),
                paired = sessionToken != null,
                accessGranted = notificationAccess,
                mediaAccess = mediaAccess,
                callLogAccess = callLogAccess,
                callPhoneAccess = callPhoneAccess,
                dialerRole = dialerRole,
                onEnable = requestNotificationAccess,
                onDisconnect = disconnect,
                onRequestDialer = requestDialerRole,
                onRequestMediaAccess = requestMediaAccess,
                onRequestCallLogAccess = requestCallLogAccess,
                onRequestCallPhoneAccess = requestCallPhoneAccess,
            )
        }
    }
}

@Composable
private fun PhoneScreen(
    modifier: Modifier,
    number: String,
    call: DroidLinkCallState?,
    dialerRole: Boolean,
    onNumberChange: (String) -> Unit,
    onRequestDialer: () -> Unit,
    onCall: () -> Unit,
    onAnswer: () -> Unit,
    onHangUp: () -> Unit,
) {
    Column(modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(20.dp), verticalArrangement = Arrangement.spacedBy(16.dp)) {
        Text("Appels", style = MaterialTheme.typography.headlineMedium, fontWeight = FontWeight.SemiBold)
        if (!dialerRole) {
            FeatureCard(
                Icons.Outlined.Call,
                "Définir DroidLink comme application Téléphone",
                "Autorise l’interface d’appel intégrée. Tu pourras remettre ton application actuelle dans les réglages Android.",
                "Choisir",
                onRequestDialer,
            )
        }
        if (call != null) {
            ElevatedCard(shape = RoundedCornerShape(24.dp)) {
                Column(Modifier.fillMaxWidth().padding(20.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text(call.displayName.ifBlank { call.number.ifBlank { "Appel" } }, style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.SemiBold)
                    Text(call.number, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    Text(callStateLabel(call.state), style = MaterialTheme.typography.labelLarge, color = MaterialTheme.colorScheme.primary)
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        if (call.state == "RINGING") OutlinedButton(onClick = onAnswer) { Text("Répondre") }
                        Button(onClick = onHangUp) { Text(if (call.state == "RINGING") "Refuser" else "Terminer") }
                    }
                }
            }
        }
        OutlinedTextField(
            value = number,
            onValueChange = onNumberChange,
            modifier = Modifier.fillMaxWidth(),
            label = { Text("Numéro à composer") },
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Phone),
            singleLine = true,
            shape = RoundedCornerShape(18.dp),
        )
        val keys = listOf("1", "2", "3", "4", "5", "6", "7", "8", "9", "*", "0", "#")
        keys.chunked(3).forEach { row ->
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                row.forEach { key ->
                    OutlinedButton(onClick = { onNumberChange(number + key) }, modifier = Modifier.weight(1f)) {
                        Text(key, style = MaterialTheme.typography.titleLarge)
                    }
                }
            }
        }
        Button(onClick = onCall, enabled = number.isNotBlank(), modifier = Modifier.fillMaxWidth().height(54.dp), shape = RoundedCornerShape(18.dp)) {
            Icon(Icons.Outlined.Call, contentDescription = null)
            Spacer(Modifier.width(8.dp))
            Text("Appeler avec la SIM du téléphone")
        }
        Text("L’audio passe par le profil mains libres Bluetooth si le téléphone est connecté à Debian comme casque.", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

@Composable
private fun HomeScreen(
    modifier: Modifier,
    paired: Boolean,
    notificationAccess: Boolean,
    code: String,
    pairingStatus: String,
    pairingBusy: Boolean,
    onCodeChange: (String) -> Unit,
    onPair: (String) -> Unit,
    onUnpair: () -> Unit,
    onEnableNotifications: () -> Unit,
    onOpenPermissions: () -> Unit,
) {
    val context = LocalContext.current
    Column(
        modifier = modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 20.dp, vertical = 14.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        Text("Votre téléphone, relié à GNOME.", style = MaterialTheme.typography.headlineMedium, fontWeight = FontWeight.SemiBold)
        Text("Un lien local par ADB pour les alertes, les appels et les photos dans DroidDesk.", color = MaterialTheme.colorScheme.onSurfaceVariant)

        DeviceCard(context)

        ElevatedCard(shape = RoundedCornerShape(24.dp)) {
            Column(Modifier.fillMaxWidth().padding(20.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text("Tuile DroidLink", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
                Text("Ajoute DroidLink aux Réglages rapides : balaie deux fois depuis le haut de l’écran, touche Modifier, puis fais glisser DroidLink dans les tuiles actives. Un toucher ouvre directement Appels.", color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }

        ElevatedCard(shape = RoundedCornerShape(28.dp), colors = CardDefaults.elevatedCardColors(containerColor = MaterialTheme.colorScheme.surfaceContainerHigh)) {
            Column(Modifier.fillMaxWidth().padding(20.dp), verticalArrangement = Arrangement.spacedBy(14.dp)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Icon(Icons.Outlined.Link, contentDescription = null, tint = MaterialTheme.colorScheme.primary, modifier = Modifier.size(26.dp))
                    Spacer(Modifier.width(12.dp))
                    Column(Modifier.weight(1f)) {
                        Text(if (paired) "DroidDesk est associé" else "Associer à DroidDesk", style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.SemiBold)
                        Text(if (paired) "Connexion par le tunnel ADB" else "Démarre l’appairage sur le PC, puis saisis son code.", style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                }
                if (paired) {
                    StatusPill("Connecté", MaterialTheme.colorScheme.primaryContainer)
                    if (pairingStatus.isNotBlank()) Text(pairingStatus, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    OutlinedButton(onClick = onUnpair, modifier = Modifier.fillMaxWidth()) { Text("Dissocier ce téléphone") }
                } else {
                    OutlinedTextField(
                        value = code,
                        onValueChange = onCodeChange,
                        modifier = Modifier.fillMaxWidth(),
                        label = { Text("Code à 6 chiffres") },
                        supportingText = { Text("DroidDesk → DroidLink → Démarrer l’appairage") },
                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.NumberPassword),
                        singleLine = true,
                        shape = RoundedCornerShape(18.dp),
                    )
                    Button(
                        onClick = { onPair(code) },
                        enabled = code.length == 6 && !pairingBusy,
                        modifier = Modifier.fillMaxWidth().height(52.dp),
                        shape = RoundedCornerShape(18.dp),
                    ) {
                        if (pairingBusy) CircularProgressIndicator(Modifier.size(20.dp), strokeWidth = 2.dp)
                        else Text("Associer à DroidDesk")
                    }
                    if (pairingStatus.isNotBlank()) Text(pairingStatus, color = MaterialTheme.colorScheme.error)
                }
            }
        }

        Text("Fonctions du lien", style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.SemiBold)
        FeatureCard(
            icon = Icons.Outlined.Notifications,
            title = "Notifications",
            subtitle = if (notificationAccess) "Accès autorisé · prêt à relayer" else "Autorise DroidLink pour les recevoir sur le PC",
            action = if (notificationAccess) "Réglages" else "Autoriser",
            onClick = if (notificationAccess) onOpenPermissions else onEnableNotifications,
        )
        Text("DroidLink ne transmet que les notifications autorisées par Android. Le téléphone et le PC doivent être appairés par ADB.", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

@Composable
private fun DeviceCard(context: Context) {
    val manufacturer = Build.MANUFACTURER.replaceFirstChar { if (it.isLowerCase()) it.titlecase() else it.toString() }
    val model = Build.MODEL
    val battery = remember {
        runCatching {
            context.getSystemService(BatteryManager::class.java)?.getIntProperty(BatteryManager.BATTERY_PROPERTY_CAPACITY)
        }.getOrNull()?.takeIf { it in 0..100 }
    }
    Card(
        shape = RoundedCornerShape(28.dp),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.primaryContainer),
    ) {
        Row(Modifier.fillMaxWidth().padding(20.dp), verticalAlignment = Alignment.CenterVertically) {
            Box(
                Modifier.size(width = 58.dp, height = 76.dp)
                    .background(Brush.linearGradient(listOf(MaterialTheme.colorScheme.primary, MaterialTheme.colorScheme.tertiary)), RoundedCornerShape(18.dp)),
                contentAlignment = Alignment.Center,
            ) {
                Icon(Icons.Outlined.PhoneAndroid, contentDescription = null, tint = MaterialTheme.colorScheme.onPrimary, modifier = Modifier.size(34.dp))
            }
            Spacer(Modifier.width(16.dp))
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                Text("$manufacturer $model", style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.SemiBold)
                Text("Android ${Build.VERSION.RELEASE}", color = MaterialTheme.colorScheme.onSurfaceVariant)
                if (battery != null) Row(verticalAlignment = Alignment.CenterVertically) {
                    Icon(Icons.Outlined.BatteryFull, contentDescription = null, modifier = Modifier.size(18.dp))
                    Spacer(Modifier.width(4.dp))
                    Text("$battery%", style = MaterialTheme.typography.labelLarge)
                }
            }
        }
    }
}

@Composable
private fun NotificationsScreen(modifier: Modifier, paired: Boolean, accessGranted: Boolean, onEnable: () -> Unit) {
    Column(modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(20.dp), verticalArrangement = Arrangement.spacedBy(16.dp)) {
        Text("Alertes", style = MaterialTheme.typography.headlineMedium, fontWeight = FontWeight.SemiBold)
        if (!paired) {
            FeatureCard(Icons.Outlined.Link, "Associer d’abord DroidDesk", "Le tunnel ADB doit être actif pour relayer les notifications.", "Accueil") { }
        } else if (!accessGranted) {
            FeatureCard(Icons.Outlined.Security, "Autoriser l’accès aux notifications", "Android demande votre accord dans les réglages système.", "Autoriser", onEnable)
        } else {
            ElevatedCard(shape = RoundedCornerShape(24.dp)) {
                Column(Modifier.fillMaxWidth().padding(24.dp), horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(12.dp)) {
                    Icon(Icons.Outlined.Notifications, contentDescription = null, tint = MaterialTheme.colorScheme.primary, modifier = Modifier.size(36.dp))
                    Text("Les alertes arrivent dans GNOME", style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.SemiBold)
                    Text("DroidLink relaie les notifications et garde le canal ADB local pour les commandes d’appel, les photos et les icônes d’applications.", color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
        }
        Text("Le contenu reste sur le téléphone si le tunnel ADB est fermé.", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

@Composable
private fun SettingsScreen(
    modifier: Modifier,
    paired: Boolean,
    accessGranted: Boolean,
    mediaAccess: Boolean,
    callLogAccess: Boolean,
    callPhoneAccess: Boolean,
    dialerRole: Boolean,
    onEnable: () -> Unit,
    onDisconnect: () -> Unit,
    onRequestDialer: () -> Unit,
    onRequestMediaAccess: () -> Unit,
    onRequestCallLogAccess: () -> Unit,
    onRequestCallPhoneAccess: () -> Unit,
) {
    val context = LocalContext.current
    var blockedApps by remember { mutableStateOf(readBlockedApps(context)) }
    var knownApps by remember { mutableStateOf(readKnownApps(context)) }
    Column(modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(20.dp), verticalArrangement = Arrangement.spacedBy(16.dp)) {
        Text("Accès et confidentialité", style = MaterialTheme.typography.headlineMedium, fontWeight = FontWeight.SemiBold)
        FeatureCard(
            icon = Icons.Outlined.Notifications,
            title = "Accès aux notifications",
            subtitle = if (accessGranted) "Autorisé dans Android" else "Non autorisé",
            action = if (accessGranted) "Gérer" else "Autoriser",
            onClick = onEnable,
        )
        FeatureCard(
            icon = Icons.Outlined.Security,
            title = "Connexion",
            subtitle = if (paired) "Appairé par ADB · aucun serveur public" else "Aucun ordinateur associé",
            action = if (paired) "Dissocier" else "—",
            onClick = if (paired) onDisconnect else ({}),
        )
        FeatureCard(
            icon = Icons.Outlined.Call,
            title = "Application Téléphone",
            subtitle = if (dialerRole) "DroidLink gère les appels" else "Requis pour répondre et terminer un appel depuis Debian",
            action = if (dialerRole) "Activée" else "Choisir",
            onClick = onRequestDialer,
        )
        FeatureCard(
            icon = Icons.Outlined.PhoneAndroid,
            title = "Galerie du téléphone",
            subtitle = if (mediaAccess) "Accès aux photos autorisé" else "Autorise les images pour afficher les 2 000 plus récentes",
            action = if (mediaAccess) "Autorisé" else "Autoriser",
            onClick = onRequestMediaAccess,
        )
        FeatureCard(
            icon = Icons.Outlined.Call,
            title = "Historique des appels",
            subtitle = if (callLogAccess) "Accès autorisé" else "DroidLink doit être l’application Téléphone pour demander cet accès",
            action = if (callLogAccess) "Autorisé" else if (dialerRole) "Autoriser" else "Choisir Téléphone",
            onClick = if (dialerRole) onRequestCallLogAccess else onRequestDialer,
        )
        FeatureCard(
            icon = Icons.Outlined.Call,
            title = "Appels sortants depuis le PC",
            subtitle = if (callPhoneAccess) "Autorisation Android active" else "Requiert DroidLink comme Téléphone et l’autorisation d’appel",
            action = if (callPhoneAccess) "Autorisé" else if (dialerRole) "Autoriser" else "Choisir Téléphone",
            onClick = if (dialerRole) onRequestCallPhoneAccess else onRequestDialer,
        )
        ElevatedCard(shape = RoundedCornerShape(24.dp)) {
            Column(Modifier.fillMaxWidth().padding(20.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f)) {
                        Text("Applications autorisées", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
                        Text("Choisis quelles notifications peuvent quitter le téléphone.", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                    TextButton(onClick = { knownApps = readKnownApps(context) }) { Text("Actualiser") }
                }
                if (knownApps.isEmpty()) {
                    Text("Les applications apparaîtront ici après leurs premières notifications.", color = MaterialTheme.colorScheme.onSurfaceVariant)
                } else {
                    knownApps.sorted().forEach { packageName ->
                        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                            Text(packageName, modifier = Modifier.weight(1f), style = MaterialTheme.typography.bodyMedium)
                            Switch(
                                checked = packageName !in blockedApps,
                                onCheckedChange = { allowed ->
                                    val updated = blockedApps.toMutableSet()
                                    if (allowed) updated.remove(packageName) else updated.add(packageName)
                                    blockedApps = updated
                                    context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
                                        .edit().putStringSet(BLOCKED_APPS_KEY, updated).apply()
                                },
                            )
                        }
                    }
                }
            }
        }
        ElevatedCard(shape = RoundedCornerShape(24.dp)) {
            Column(Modifier.fillMaxWidth().padding(20.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text("Vos données restent sous votre contrôle", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
                Text("Le relais de notifications maintient le canal ADB local de commandes. Les photos et les appels demandent en plus leurs autorisations dédiées. Aucune connexion cloud n’est configurée.", color = MaterialTheme.colorScheme.onSurfaceVariant)
                Spacer(Modifier.height(2.dp))
                Text("DroidLink · GPL-3.0-only", style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.primary)
            }
        }
    }
}

@Composable
private fun FeatureCard(icon: ImageVector, title: String, subtitle: String, action: String, onClick: () -> Unit) {
    ElevatedCard(onClick = onClick, shape = RoundedCornerShape(22.dp), colors = CardDefaults.elevatedCardColors(containerColor = MaterialTheme.colorScheme.surfaceContainerHigh)) {
        Row(Modifier.fillMaxWidth().padding(18.dp), verticalAlignment = Alignment.CenterVertically) {
            Box(Modifier.size(44.dp).background(MaterialTheme.colorScheme.secondaryContainer, CircleShape), contentAlignment = Alignment.Center) {
                Icon(icon, contentDescription = null, tint = MaterialTheme.colorScheme.onSecondaryContainer)
            }
            Spacer(Modifier.width(14.dp))
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(3.dp)) {
                Text(title, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
                Text(subtitle, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            Spacer(Modifier.width(8.dp))
            Text(action, style = MaterialTheme.typography.labelLarge, color = MaterialTheme.colorScheme.primary)
        }
    }
}

@Composable
private fun StatusPill(label: String, color: Color) {
    Surface(color = color, shape = RoundedCornerShape(50)) {
        Text(label, modifier = Modifier.padding(horizontal = 14.dp, vertical = 7.dp), style = MaterialTheme.typography.labelLarge)
    }
}

private fun readToken(context: Context): String? =
    context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString(TOKEN_KEY, null)

private fun hasDialerRole(context: Context): Boolean = if (Build.VERSION.SDK_INT >= 29) {
    context.getSystemService(RoleManager::class.java)?.isRoleHeld(RoleManager.ROLE_DIALER) == true
} else {
    context.getSystemService(android.telecom.TelecomManager::class.java)?.defaultDialerPackage == context.packageName
}

private fun hasCallPhoneAccess(context: Context): Boolean =
    context.checkSelfPermission(Manifest.permission.CALL_PHONE) == android.content.pm.PackageManager.PERMISSION_GRANTED

private const val BLOCKED_APPS_KEY = "blocked_notification_apps"
private const val KNOWN_APPS_KEY = "known_notification_apps"

private fun readBlockedApps(context: Context): Set<String> =
    context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getStringSet(BLOCKED_APPS_KEY, emptySet()).orEmpty().toSet()

private fun readKnownApps(context: Context): Set<String> =
    context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getStringSet(KNOWN_APPS_KEY, emptySet()).orEmpty().toSet()

private fun hasNotificationAccess(context: Context): Boolean {
    val listeners = Secure.getString(context.contentResolver, "enabled_notification_listeners") ?: return false
    return listeners.contains(context.packageName)
}

private suspend fun requestPairing(context: Context, code: String): PairingResult = withContext(Dispatchers.IO) {
    val connection = (URL("http://localhost:$DROIDLINK_PORT/pair").openConnection() as HttpURLConnection)
    try {
        connection.requestMethod = "POST"
        connection.connectTimeout = 3_000
        connection.readTimeout = 4_000
        connection.doOutput = true
        connection.setRequestProperty("Content-Type", "application/json; charset=utf-8")
        val body = JSONObject()
            .put("code", code)
            .put("manufacturer", Build.MANUFACTURER)
            .put("model", Build.MODEL)
            .put("android", Build.VERSION.RELEASE)
            .put("dialerRole", hasDialerRole(context))
            .put("notificationAccess", hasNotificationAccess(context))
            .put("callLogAccess", DroidLinkCallLog.hasAccess(context))
            .put("callPhoneAccess", hasCallPhoneAccess(context))
            .toString()
        connection.outputStream.use { it.write(body.toByteArray(Charsets.UTF_8)) }
        val status = connection.responseCode
        val stream = if (status in 200..299) connection.inputStream else connection.errorStream
        val response = stream?.bufferedReader()?.use { it.readText() }?.let(::JSONObject)
        val token = response?.optString("token")?.takeIf { it.isNotBlank() && it != "null" }
        if (status in 200..299 && token != null) {
            context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putString(TOKEN_KEY, token).apply()
            PairingResult(token = token)
        } else {
            PairingResult(error = response?.optString("error")?.takeIf { it.isNotBlank() } ?: "Code refusé. Vérifie qu’il est encore valide.")
        }
    } catch (_: Exception) {
        PairingResult(error = "DroidDesk est inaccessible. Vérifie ADB et le code d’appairage.")
    } finally {
        connection.disconnect()
    }
}

private suspend fun requestUnpair(token: String) = withContext(Dispatchers.IO) {
    val connection = (URL("http://localhost:$DROIDLINK_PORT/unpair").openConnection() as HttpURLConnection)
    try {
        connection.requestMethod = "POST"
        connection.connectTimeout = 1_500
        connection.readTimeout = 1_500
        connection.doOutput = true
        connection.setRequestProperty("Authorization", "Bearer $token")
        connection.setRequestProperty("Content-Type", "application/json")
        connection.outputStream.use { it.write("{}".toByteArray()) }
        connection.responseCode
    } catch (_: Exception) {
        // Clear local pairing even if the desktop is currently offline.
    } finally {
        connection.disconnect()
    }
}

private suspend fun publishPhoneCapabilities(context: Context) = withContext(Dispatchers.IO) {
    val token = readToken(context) ?: return@withContext
    val connection = (URL("http://localhost:$DROIDLINK_PORT/phone").openConnection() as HttpURLConnection)
    try {
        connection.requestMethod = "POST"
        connection.connectTimeout = 2_000
        connection.readTimeout = 2_000
        connection.doOutput = true
        connection.setRequestProperty("Authorization", "Bearer $token")
        connection.setRequestProperty("Content-Type", "application/json; charset=utf-8")
        val body = JSONObject()
            .put("manufacturer", Build.MANUFACTURER)
            .put("model", Build.MODEL)
            .put("android", Build.VERSION.RELEASE)
            .put("dialerRole", hasDialerRole(context))
            .put("notificationAccess", hasNotificationAccess(context))
            .put("callLogAccess", DroidLinkCallLog.hasAccess(context))
            .put("callPhoneAccess", hasCallPhoneAccess(context))
            .toString()
        connection.outputStream.use { it.write(body.toByteArray(Charsets.UTF_8)) }
        connection.responseCode
    } catch (_: Exception) {
        // The desktop can refresh this state at the next pairing.
    } finally {
        connection.disconnect()
    }
}

@Composable
internal fun DroidLinkTheme(content: @Composable () -> Unit) {
    val dark = androidx.compose.foundation.isSystemInDarkTheme()
    val colors = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
        val context = LocalContext.current
        if (dark) androidx.compose.material3.dynamicDarkColorScheme(context) else androidx.compose.material3.dynamicLightColorScheme(context)
    } else if (dark) {
        androidx.compose.material3.darkColorScheme(
            primary = Color(0xFFB9C4FF),
            secondary = Color(0xFFBFC6EA),
            tertiary = Color(0xFFE3B9D4),
            background = Color(0xFF10131A),
            surface = Color(0xFF191C24),
        )
    } else {
        androidx.compose.material3.lightColorScheme(
            primary = Color(0xFF4054B5),
            secondary = Color(0xFF53628F),
            tertiary = Color(0xFF76546F),
            background = Color(0xFFF8F7FF),
            surface = Color(0xFFF0EFF7),
        )
    }
    MaterialTheme(colorScheme = colors, content = content)
}
