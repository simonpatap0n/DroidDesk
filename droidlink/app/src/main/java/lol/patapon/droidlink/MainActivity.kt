package lol.patapon.droidlink

import android.Manifest
import android.content.Context
import android.content.Intent
import android.app.role.RoleManager
import android.os.BatteryManager
import android.os.Build
import android.os.Bundle
import android.provider.Telephony
import android.provider.Settings
import android.provider.Settings.Secure
import android.text.format.DateFormat
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
import androidx.compose.material.icons.outlined.Home
import androidx.compose.material.icons.outlined.Link
import androidx.compose.material.icons.outlined.Notifications
import androidx.compose.material.icons.outlined.PhoneAndroid
import androidx.compose.material.icons.outlined.Security
import androidx.compose.material.icons.outlined.Settings
import androidx.compose.material3.Button
import androidx.compose.material3.AlertDialog
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
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.util.Date

private const val PREFS = "droidlink"
private const val TOKEN_KEY = "session_token"

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent { DroidLinkTheme { DroidLinkApp() } }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
    }
}

private enum class DroidLinkTab(val label: String, val icon: ImageVector) {
    Home("Accueil", Icons.Outlined.Home),
    Notifications("Alertes", Icons.Outlined.Notifications),
    Settings("Accès", Icons.Outlined.Settings),
}

private data class PairingResult(val token: String? = null, val sessionId: String? = null, val error: String? = null)

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun DroidLinkApp() {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    var selectedTab by rememberSaveable { mutableStateOf(DroidLinkTab.Home) }
    var sessionToken by remember { mutableStateOf(readToken(context)) }
    var notificationAccess by remember { mutableStateOf(hasNotificationAccess(context)) }
    var postNotifications by remember {
        mutableStateOf(Build.VERSION.SDK_INT < 33 || context.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) == android.content.pm.PackageManager.PERMISSION_GRANTED)
    }
    var contactsAccess by remember { mutableStateOf(DroidLinkContacts.hasAccess(context)) }
    var pairingCode by rememberSaveable { mutableStateOf("") }
    var pairingStatus by rememberSaveable { mutableStateOf("") }
    var pairingBusy by remember { mutableStateOf(false) }
    var mediaAccess by remember { mutableStateOf(DroidLinkMedia.hasAccess(context)) }
    var defaultSmsApp by remember { mutableStateOf(isDefaultSmsApp(context)) }
    val smsRoleAvailable = remember { isSmsRoleAvailable(context) }

    val settingsLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.StartActivityForResult(),
    ) {
        notificationAccess = hasNotificationAccess(context)
        scope.launch { withContext(Dispatchers.IO) { publishPhoneCapabilities(context) } }
    }
    val postNotificationsLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { granted -> postNotifications = granted }
    val requestPostNotifications = {
        if (Build.VERSION.SDK_INT >= 33 && !postNotifications) {
            postNotificationsLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
        }
    }

    val smsRoleLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.StartActivityForResult(),
    ) {
        defaultSmsApp = isDefaultSmsApp(context)
    }
    val requestSmsRole = {
        val intent = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            context.getSystemService(RoleManager::class.java)
                ?.takeIf { it.isRoleAvailable(RoleManager.ROLE_SMS) }
                ?.createRequestRoleIntent(RoleManager.ROLE_SMS)
        } else {
            @Suppress("DEPRECATION")
            Intent(Telephony.Sms.Intents.ACTION_CHANGE_DEFAULT)
                .putExtra(Telephony.Sms.Intents.EXTRA_PACKAGE_NAME, context.packageName)
        }
        if (intent != null) smsRoleLauncher.launch(intent)
    }

    val requestNotificationAccess = {
        settingsLauncher.launch(Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS))
    }

    val contactsPermissionLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) {
        contactsAccess = DroidLinkContacts.hasAccess(context)
    }
    val requestContactsAccess = {
        if (!DroidLinkContacts.hasAccess(context)) {
            contactsPermissionLauncher.launch(Manifest.permission.READ_CONTACTS)
        }
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

    val pairWithDesktop: (String) -> Unit = { code ->
        pairingBusy = true
        pairingStatus = "Connexion à DroidDesk…"
        scope.launch {
            val result = withContext(Dispatchers.IO) { requestPairing(context, code) }
            pairingBusy = false
            if (result.token != null && result.sessionId != null) {
                sessionToken = result.token
                pairingCode = ""
                pairingStatus = "DroidDesk est associé à ce téléphone."
            } else {
                pairingStatus = result.error ?: "Connexion impossible. Vérifie le code et l’accès HTTPS."
            }
        }
    }

    val disconnect = {
        val token = sessionToken
        val sessionId = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .getString(DroidLinkTransport.SESSION_ID_KEY, null)
        scope.launch(Dispatchers.IO) { if (token != null && sessionId != null) requestUnpair(context, token, sessionId) }
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit()
            .remove(TOKEN_KEY)
            .remove(DroidLinkTransport.SESSION_ID_KEY)
            .remove("desktop_last_seen_ms")
            .apply()
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
                contactsAccess = contactsAccess,
                mediaAccess = mediaAccess,
                postNotifications = postNotifications,
                defaultSmsApp = defaultSmsApp,
                smsRoleAvailable = smsRoleAvailable,
                onEnable = requestNotificationAccess,
                onDisconnect = disconnect,
                onRequestMediaAccess = requestMediaAccess,
                onRequestPostNotifications = requestPostNotifications,
                onRequestContactsAccess = requestContactsAccess,
                onRequestSmsRole = requestSmsRole,
            )
        }
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
        Text("Ton téléphone, relié à GNOME.", style = MaterialTheme.typography.headlineMedium, fontWeight = FontWeight.SemiBold)
        Text("DroidLink fait le lien avec DroidDesk. L’application Téléphone Android et la SIM restent celles du téléphone; les appels se lancent et se contrôlent depuis le PC.", color = MaterialTheme.colorScheme.onSurfaceVariant)

        DeviceCard(context)

        ElevatedCard(shape = RoundedCornerShape(24.dp)) {
            Column(Modifier.fillMaxWidth().padding(20.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text("Tuile DroidLink", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
                Text("Ajoute DroidLink aux Réglages rapides : balaie deux fois depuis le haut de l’écran, touche Modifier, puis fais glisser DroidLink dans les tuiles actives. La tuile ouvre les réglages de connexion du téléphone.", color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }

        ElevatedCard(shape = RoundedCornerShape(28.dp), colors = CardDefaults.elevatedCardColors(containerColor = MaterialTheme.colorScheme.surfaceContainerHigh)) {
            Column(Modifier.fillMaxWidth().padding(20.dp), verticalArrangement = Arrangement.spacedBy(14.dp)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Icon(Icons.Outlined.Link, contentDescription = null, tint = MaterialTheme.colorScheme.primary, modifier = Modifier.size(26.dp))
                    Spacer(Modifier.width(12.dp))
                    Column(Modifier.weight(1f)) {
                        Text(if (paired) "DroidDesk est associé" else "Associer à DroidDesk", style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.SemiBold)
                        Text(if (paired) "Connexion HTTPS via DroidLink" else "Démarre l’association sur le PC, puis saisis son code.", style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
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
        Text("DroidLink transmet les notifications autorisées par Android au moyen d’une connexion HTTPS. Les deux appareils peuvent être sur des réseaux différents.", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
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
    val context = LocalContext.current
    val diagnostics by RelayDiagnostics.state.collectAsState()
    val relayUrl = remember(context) {
        DroidLinkTransport.relayUrl(context).ifBlank { "Domaine du relais non configuré" }
    }
    LaunchedEffect(context) {
        while (true) {
            withContext(Dispatchers.IO) { DroidLinkTransport.checkHealth(context) }
            delay(15_000)
        }
    }

    Column(modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(20.dp), verticalArrangement = Arrangement.spacedBy(16.dp)) {
        Text("Alertes", style = MaterialTheme.typography.headlineMedium, fontWeight = FontWeight.SemiBold)
        RelayDiagnosticsPanel(diagnostics, relayUrl)
        if (!paired) {
            FeatureCard(Icons.Outlined.Link, "Associer d’abord DroidDesk", "Le relais HTTPS doit être configuré et joignable.", "Accueil") { }
        } else if (!accessGranted) {
            FeatureCard(Icons.Outlined.Security, "Autoriser l’accès aux notifications", "Android demande votre accord dans les réglages système.", "Autoriser", onEnable)
        } else {
            ElevatedCard(shape = RoundedCornerShape(24.dp)) {
                Column(Modifier.fillMaxWidth().padding(24.dp), horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(12.dp)) {
                    Icon(Icons.Outlined.Notifications, contentDescription = null, tint = MaterialTheme.colorScheme.primary, modifier = Modifier.size(36.dp))
                    Text("Les alertes arrivent dans GNOME", style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.SemiBold)
                    Text("DroidLink relaie les notifications, les photos et les icônes par HTTPS. DroidDesk garde l’application Téléphone Android standard.", color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
        }
        Text("Les notifications restent sur le téléphone si le relais est indisponible.", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

@Composable
private fun RelayDiagnosticsPanel(state: RelayDiagnosticsState, relayUrl: String) {
    val context = LocalContext.current
    val lightColor = when (state.reachable) {
        true -> Color(0xFF2E9B55)
        false -> MaterialTheme.colorScheme.error
        null -> MaterialTheme.colorScheme.outline
    }
    val status = when (state.reachable) {
        true -> "Serveur joignable"
        false -> "Serveur inaccessible"
        null -> "Vérification en cours…"
    }

    ElevatedCard(shape = RoundedCornerShape(24.dp)) {
        Column(
            Modifier.fillMaxWidth().padding(18.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Box(Modifier.size(13.dp).background(lightColor, CircleShape))
                Spacer(Modifier.width(10.dp))
                Text(status, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
            }
            Text(relayUrl, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            Text("Journal de communication", style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.SemiBold)
            if (state.entries.isEmpty()) {
                Text("En attente du premier échange avec le relais.", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            } else {
                state.entries.take(8).forEach { entry ->
                    Row(
                        Modifier.fillMaxWidth(),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Text(
                            DateFormat.getTimeFormat(context).format(Date(entry.timestampMs)),
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                        Spacer(Modifier.width(10.dp))
                        val entryColor = when (entry.success) {
                            true -> MaterialTheme.colorScheme.onSurfaceVariant
                            false -> MaterialTheme.colorScheme.error
                            null -> MaterialTheme.colorScheme.onSurfaceVariant
                        }
                        Text(
                            entry.message,
                            modifier = Modifier.weight(1f),
                            style = MaterialTheme.typography.bodySmall,
                            color = entryColor,
                            maxLines = 1,
                        )
                    }
                }
            }
        }
    }
}

@Composable
private fun SettingsScreen(
    modifier: Modifier,
    paired: Boolean,
    accessGranted: Boolean,
    contactsAccess: Boolean,
    mediaAccess: Boolean,
    postNotifications: Boolean,
    defaultSmsApp: Boolean,
    smsRoleAvailable: Boolean,
    onEnable: () -> Unit,
    onDisconnect: () -> Unit,
    onRequestContactsAccess: () -> Unit,
    onRequestMediaAccess: () -> Unit,
    onRequestPostNotifications: () -> Unit,
    onRequestSmsRole: () -> Unit,
) {
    val context = LocalContext.current
    var blockedApps by remember { mutableStateOf(readBlockedApps(context)) }
    var knownApps by remember { mutableStateOf(readKnownApps(context)) }
    var relayDomain by rememberSaveable { mutableStateOf(DroidLinkTransport.relayUrl(context)) }
    val normalizedRelayDomain = DroidLinkTransport.normalizeRelayUrl(relayDomain)
    var showSmsRoleWarning by rememberSaveable { mutableStateOf(false) }
    if (showSmsRoleWarning) {
        AlertDialog(
            onDismissRequest = { showSmsRoleWarning = false },
            title = { Text("Changer l’application SMS par défaut ?") },
            text = { Text("Android remettra les nouveaux SMS et MMS à DroidLink. DroidLink sait enregistrer les SMS, mais ne télécharge pas encore les pièces jointes MMS; le rôle SMS ne donne pas accès aux conversations RCS. Continue seulement si tu acceptes ces limites.") },
            confirmButton = {
                TextButton(onClick = {
                    showSmsRoleWarning = false
                    onRequestSmsRole()
                }) { Text("Continuer vers Android") }
            },
            dismissButton = {
                TextButton(onClick = { showSmsRoleWarning = false }) { Text("Annuler") }
            },
        )
    }
    Column(modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(20.dp), verticalArrangement = Arrangement.spacedBy(16.dp)) {
        Text("Accès et confidentialité", style = MaterialTheme.typography.headlineMedium, fontWeight = FontWeight.SemiBold)
        ElevatedCard(shape = RoundedCornerShape(24.dp)) {
            Column(Modifier.fillMaxWidth().padding(20.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                Text("Domaine du relais HTTPS", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
                OutlinedTextField(
                    value = relayDomain,
                    onValueChange = { relayDomain = it },
                    modifier = Modifier.fillMaxWidth(),
                    label = { Text("Nom de domaine") },
                    placeholder = { Text("relay.example.net") },
                    singleLine = true,
                    keyboardOptions = KeyboardOptions(keyboardType = androidx.compose.ui.text.input.KeyboardType.Uri),
                    supportingText = {
                        Text(if (paired) "Dissocie DroidLink avant de changer de domaine." else "Utilise le même domaine dans DroidDesk et DroidLink.")
                    },
                )
                Button(
                    onClick = {
                        val normalized = normalizedRelayDomain ?: return@Button
                        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit()
                            .putString(DroidLinkTransport.RELAY_URL_KEY, normalized)
                            .apply()
                        relayDomain = normalized
                    },
                    enabled = !paired && normalizedRelayDomain != null && normalizedRelayDomain != DroidLinkTransport.relayUrl(context),
                ) { Text("Enregistrer le domaine") }
                if (relayDomain.isNotBlank() && normalizedRelayDomain == null) {
                    Text("Entre un domaine HTTPS valide, sans chemin.", color = MaterialTheme.colorScheme.error)
                }
            }
        }
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
            subtitle = if (paired) "Associé au relais HTTPS DroidLink" else "Aucun ordinateur associé",
            action = if (paired) "Dissocier" else "—",
            onClick = if (paired) onDisconnect else ({}),
        )
        FeatureCard(
            icon = Icons.Outlined.PhoneAndroid,
            title = "Contacts",
            subtitle = if (contactsAccess) "Accès autorisé pour afficher les contacts dans DroidDesk" else "Autorise DroidLink à lire les contacts du téléphone",
            action = if (contactsAccess) "Autorisé" else "Autoriser",
            onClick = onRequestContactsAccess,
        )
        FeatureCard(
            icon = Icons.Outlined.PhoneAndroid,
            title = "Rôle SMS Android",
            subtitle = when {
                defaultSmsApp -> "DroidLink est l’application SMS par défaut"
                !smsRoleAvailable -> "Rôle SMS indisponible sur cet appareil"
                else -> "À activer dans Android pour autoriser DroidLink à gérer les SMS"
            },
            action = when {
                defaultSmsApp -> "Actif"
                !smsRoleAvailable -> "Indisponible"
                else -> "Demander"
            },
            onClick = if (!defaultSmsApp && smsRoleAvailable) ({ showSmsRoleWarning = true }) else ({}),
        )
        Text(
            "Le rôle fait de DroidLink l’application SMS par défaut. Android lui remet les SMS et MMS entrants; l’intégration MMS est encore limitée et RCS n’est pas accessible par ce rôle. Garde Google Messages par défaut si tu dépends des MMS/RCS.",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        ElevatedCard(shape = RoundedCornerShape(22.dp)) {
            Row(Modifier.fillMaxWidth().padding(18.dp), verticalAlignment = Alignment.CenterVertically) {
                Icon(Icons.Outlined.PhoneAndroid, contentDescription = null, tint = MaterialTheme.colorScheme.primary)
                Spacer(Modifier.width(14.dp))
                Column(verticalArrangement = Arrangement.spacedBy(3.dp)) {
                    Text("Téléphone Android standard", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
                    Text("DroidLink ne remplace pas le composeur Android. Certaines commandes DroidDesk utilisent encore ADB pendant leur migration vers le compagnon.", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
        }
        FeatureCard(
            icon = Icons.Outlined.PhoneAndroid,
            title = "Galerie du téléphone",
            subtitle = if (mediaAccess) "Accès aux photos autorisé" else "Autorise les images pour afficher les 2 000 plus récentes",
            action = if (mediaAccess) "Autorisé" else "Autoriser",
            onClick = onRequestMediaAccess,
        )
        if (Build.VERSION.SDK_INT >= 33) FeatureCard(
            icon = Icons.Outlined.Notifications,
            title = "Confirmations sur le téléphone",
            subtitle = if (postNotifications) "Notifications autorisées pour les demandes Android" else "Nécessaire pour confirmer une suppression de photo depuis le PC",
            action = if (postNotifications) "Autorisé" else "Autoriser",
            onClick = onRequestPostNotifications,
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
                Text("Les contenus des notifications et photos transitent par le relais HTTPS que vous avez configuré; l’accès aux photos reste soumis à l’autorisation Android.", color = MaterialTheme.colorScheme.onSurfaceVariant)
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

private fun readToken(context: Context): String? {
    val preferences = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
    val sessionId = preferences.getString(DroidLinkTransport.SESSION_ID_KEY, null)
    if (sessionId == null || !sessionId.matches(Regex("[A-Za-z0-9_-]{20,64}"))) return null
    return preferences.getString(TOKEN_KEY, null)
}

private fun batteryPercent(context: Context): Int =
    context.getSystemService(BatteryManager::class.java)
        ?.getIntProperty(BatteryManager.BATTERY_PROPERTY_CAPACITY)
        ?.takeIf { it in 0..100 } ?: -1

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

private fun isDefaultSmsApp(context: Context): Boolean =
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
        context.getSystemService(RoleManager::class.java)
            ?.isRoleHeld(RoleManager.ROLE_SMS) == true
    } else {
        @Suppress("DEPRECATION")
        Telephony.Sms.getDefaultSmsPackage(context) == context.packageName
    }

private fun isSmsRoleAvailable(context: Context): Boolean =
    context.packageManager.hasSystemFeature("android.hardware.telephony") &&
        (Build.VERSION.SDK_INT < Build.VERSION_CODES.Q ||
            context.getSystemService(RoleManager::class.java)
                ?.isRoleAvailable(RoleManager.ROLE_SMS) == true)

private suspend fun requestPairing(context: Context, code: String): PairingResult = withContext(Dispatchers.IO) {
    val baseUrl = DroidLinkTransport.relayUrl(context)
    if (baseUrl.isBlank()) return@withContext PairingResult(error = "Configure le domaine HTTPS du relais dans Accès.")
    val connection = (URL("$baseUrl/v1/pair").openConnection() as HttpURLConnection)
    try {
        connection.requestMethod = "POST"
        connection.connectTimeout = 3_000
        connection.readTimeout = 8_000
        connection.doOutput = true
        connection.setRequestProperty("Content-Type", "application/json; charset=utf-8")
        val body = JSONObject()
            .put("code", code)
            .put("phone", JSONObject()
                .put("manufacturer", Build.MANUFACTURER)
                .put("model", Build.MODEL)
                .put("android", Build.VERSION.RELEASE)
                .put("notificationAccess", hasNotificationAccess(context))
                .put("batteryPercent", batteryPercent(context)))
            .toString()
        connection.outputStream.use { it.write(body.toByteArray(Charsets.UTF_8)) }
        val status = connection.responseCode
        RelayDiagnostics.recordResponse("POST /v1/pair", status)
        val stream = if (status in 200..299) connection.inputStream else connection.errorStream
        val response = stream?.bufferedReader()?.use { it.readText() }?.let(::JSONObject)
        val token = response?.optString("token")?.takeIf { it.isNotBlank() && it != "null" }
        val sessionId = response?.optString("sessionId")?.takeIf { it.isNotBlank() && it != "null" }
        if (status in 200..299 && token != null && sessionId != null) {
            context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit()
                .putString(TOKEN_KEY, token)
                .putString(DroidLinkTransport.SESSION_ID_KEY, sessionId)
                .putString(DroidLinkTransport.RELAY_URL_KEY, baseUrl)
                .apply()
            PairingResult(token = token, sessionId = sessionId)
        } else {
            PairingResult(error = response?.optString("error")?.takeIf { it.isNotBlank() } ?: "Code refusé. Vérifie qu’il est encore valide.")
        }
    } catch (error: Exception) {
        RelayDiagnostics.recordFailure("POST /v1/pair", error)
        PairingResult(error = "Relais DroidLink inaccessible. Vérifie le DNS, HTTPS et le code affiché sur le PC.")
    } finally {
        connection.disconnect()
    }
}

private suspend fun requestUnpair(context: Context, token: String, sessionId: String) = withContext(Dispatchers.IO) {
    val baseUrl = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        .getString(DroidLinkTransport.RELAY_URL_KEY, "")
        ?.trimEnd('/') ?: ""
    if (baseUrl.isBlank()) return@withContext
    val connection = (URL("$baseUrl/v1/sessions/$sessionId/unpair").openConnection() as HttpURLConnection)
    try {
        connection.requestMethod = "POST"
        connection.connectTimeout = 3_000
        connection.readTimeout = 5_000
        connection.doOutput = true
        connection.setRequestProperty("Authorization", "Bearer $token")
        connection.setRequestProperty("Content-Type", "application/json")
        connection.outputStream.use { it.write("{}".toByteArray()) }
        RelayDiagnostics.recordResponse("POST /v1/sessions/{id}/unpair", connection.responseCode)
    } catch (error: Exception) {
        RelayDiagnostics.recordFailure("POST /v1/sessions/{id}/unpair", error)
        // Local credentials are still cleared if the relay is offline.
    } finally {
        connection.disconnect()
    }
}

private suspend fun publishPhoneCapabilities(context: Context) = withContext(Dispatchers.IO) {
    val token = readToken(context) ?: return@withContext
    val body = JSONObject()
        .put("manufacturer", Build.MANUFACTURER)
        .put("model", Build.MODEL)
        .put("android", Build.VERSION.RELEASE)
        .put("notificationAccess", hasNotificationAccess(context))
        .put("batteryPercent", batteryPercent(context))
    DroidLinkTransport.post(context, token, "phone", body)
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
