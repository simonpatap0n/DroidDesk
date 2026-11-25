# DroidDesk

DroidDesk est une application GTK4/libadwaita pour gérer un téléphone Android depuis GNOME avec ADB, scrcpy et BlueZ.

## Télécharger DroidDesk 0.1.0

La première version publique est disponible dans les [releases GitHub](https://github.com/simonpatap0n/DroidDesk/releases/tag/v0.1.0), avec l’APK compagnon [DroidLink-0.1.0.apk](https://github.com/simonpatap0n/DroidDesk/releases/download/v0.1.0/DroidLink-0.1.0.apk).

## Fonctionnalités

- **Appels dans GNOME** : lancer un appel avec l’application Téléphone Android habituelle, répondre/refuser/raccrocher avec les commandes ADB. Le téléphone garde sa SIM; Bluetooth HFP/HSP transporte la voix entre Android et le PC.
- **Contacts** et **Récents** : recherche, rappel et journal d’appels lus directement via ADB.
- **SMS** : lire les conversations et l’historique existants depuis la base SMS Android via ADB; préparer une réponse dans l’application Messages du téléphone.
- **Écran et apps** : afficher scrcpy dans DroidDesk, parcourir les applications avec leurs vraies icônes Android, les ouvrir dans l’écran intégré, afficher leurs réglages ou les désinstaller avec le clic droit.
- **Fichiers et photos** : parcourir `/sdcard` par ADB, envoyer et télécharger des fichiers; ouvrir le stockage USB dans GNOME Files par MTP; charger jusqu’à 2 000 photos récentes avec vignettes et téléchargement.
- **Bluetooth** : choisir un contrôleur (USB ou intégré), activer la radio, rechercher, appairer, connecter, renommer et oublier des appareils.
- **ADB** : appairage sans fil, connexions TCP/IP, redémarrage/récupération/bootloader, touches système, captures d’écran, logcat, propriétés Android et console `adb shell`.
- **Infos téléphone** : le fabricant, le modèle, Android, la batterie et le stockage partagé restent visibles dans l’en-tête, avec un bouton d’actualisation.
- **Notifications** : les alertes Android arrivent comme notifications natives GNOME. Les fermer dans GNOME les ignorera aussi sur le téléphone; DroidDesk garde les 50 dernières alertes et permet d’effacer toute la liste.
- **DroidLink** : lien Android pour les notifications, la batterie, les photos et les icônes d’applications. DroidLink ne remplace pas l’application Téléphone.
- **Fonctions inspirées de KDE Connect** : batterie Android dans l’en-tête, sonnerie à distance, notifications et réponses rapides, partage de fichiers et de liens, ouverture MTP dans GNOME Files.

## Dépendances Debian

```sh
sudo apt update
sudo apt install bluez adb scrcpy ffmpeg gvfs-backends python3 python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 meson ninja-build
```

BlueZ doit être actif pour gérer le Bluetooth. Au besoin :

```sh
sudo systemctl enable --now bluetooth
```

## Connexion du téléphone

### USB

1. Sur Android, active **Options pour les développeurs → Débogage USB**.
2. Branche le téléphone et accepte l’empreinte d’ordinateur affichée.
3. Dans DroidDesk, choisis le téléphone dans le sélecteur en haut.

### ADB sans fil

Sur Android 11 ou plus récent, ouvre **Options pour les développeurs → Débogage sans fil**. Dans la page **ADB** de DroidDesk, utilise **Appairer avec un code…**, puis **Connecter à une adresse…** avec l’adresse IP et le port de connexion affichés sur Android.

## Lancer depuis les sources

Depuis le dossier `android-calls` :

```sh
python3 src/droiddesk.py
```

## Installer dans GNOME

Depuis le dossier `android-calls` :

```sh
meson setup build --prefix="$HOME/.local"
meson install -C build
```

DroidDesk apparaît alors dans le lanceur GNOME. Pour le lancer depuis un terminal :

```sh
droiddesk
```

## Compagnon Android DroidLink

Le projet Android est dans [`droidlink/`](droidlink/README.md). Il fournit une interface Material 3, l’association au bureau par un code à usage unique, le relais de notifications, les photos, les icônes et les commandes distantes. DroidDesk ouvre son serveur uniquement sur `127.0.0.1`; le téléphone le rejoint avec `adb reverse`. Aucun service cloud n’est utilisé.

Pour préparer l’APK, installe Java 17 ou plus récent, **Android SDK Platform 37** et **Android SDK Build-Tools 36.0.0** dans Android Studio (**Settings → Android SDK**), puis lance :

```sh
cd droidlink
./build-apk.sh
```

Le Wrapper fourni télécharge Gradle 9.6.0 et les dépendances Gradle au premier build; Internet est requis à cette étape. `ANDROID_SDK_ROOT` peut indiquer l’emplacement du SDK si le script ne le détecte pas automatiquement. L’APK de débogage se trouve dans `droidlink/app/build/outputs/apk/debug/app-debug.apk`. Installe-le avec Android Studio ou `adb install -r app/build/outputs/apk/debug/app-debug.apk`.

## Appels, écran et SMS

Dans **Appels**, DroidDesk lance l’action Android `ACTION_CALL` avec le numéro composé; Android ouvre le service d’appel associé à l’application Téléphone standard. Les boutons Répondre, Refuser et Terminer envoient les touches casque via ADB. DroidLink n’est pas le composeur et ne demande pas le rôle Téléphone. Android documente `ACTION_CALL` comme l’action qui lance l’appel directement; l’action `ACTION_DIAL` affiche le composeur et demande une confirmation sur le téléphone ([documentation Android](https://developer.android.com/guide/components/intents-common#Phone)).

ADB transmet les commandes, pas le son. Pour parler et écouter avec le PC, le téléphone doit être connecté à Debian en Bluetooth mains libres HFP/HSP; A2DP ne transporte pas le microphone. Utilise **Configurer le Bluetooth mains libres** dans Appels puis connecte le profil HFP de l’appareil Android.

Après installation de l’APK, ouvre les Réglages rapides Android, touche **Modifier** et ajoute la tuile **DroidLink**. Elle ouvre le compagnon de connexion; Android laisse l’utilisateur choisir la position de cette tuile.

Dans **Écran et apps**, choisir une application ouvre son affichage directement dans DroidDesk. L’application lance le serveur autonome scrcpy en mode flux H.264 brut; FFmpeg le décode vers GTK4. Les clics, glissements, touches clavier et commandes Retour/Accueil/Récents sont envoyés par ADB. L’audio du flux est désactivé; l’audio des appels reste sur le profil Bluetooth mains libres du téléphone. Voir la [documentation officielle du serveur autonome scrcpy](https://github.com/Genymobile/scrcpy/blob/master/doc/develop.md#standalone-server) et sa [documentation sur l’audio](https://github.com/Genymobile/scrcpy/blob/master/doc/audio.md).

Dans **Fichiers**, **Ouvrir dans GNOME · MTP** monte le stockage USB avec GVfs et l’ouvre dans Fichiers. Sur Android, choisis **Transfert de fichiers**. Pour ADB sans fil, utilise le navigateur intégré et les actions de transfert.

La page **SMS** lit les messages enregistrés dans `content://sms` via la commande ADB `content query`; les messages et numéros ne sont pas transmis au service DroidLink ni enregistrés par DroidDesk. Le bouton de réponse ouvre Messages Android avec le destinataire et le texte préremplis afin que l’application habituelle confirme l’envoi. Android protège les permissions SMS des applications tierces; la lecture directe ici utilise le fournisseur accessible à l’utilisateur `shell` d’ADB.

## Cible Phone Link

La liste suivante sert de critères de livraison; les fonctions marquées « à intégrer » ne sont pas encore disponibles dans cette version.

| Domaine | Disponible maintenant | À intégrer dans DroidLink |
| --- | --- | --- |
| SMS | Historique des SMS et conversations via ADB; réponse préremplie dans Messages Android | Envoi depuis le PC sans confirmation sur le téléphone, médias SMS/MMS et GIF |
| Appels | Lancement et commandes au moyen du Téléphone Android standard; notifications d’appel dans GNOME; audio par Bluetooth HFP/HSP | État d’appel en temps réel selon le téléphone et le profil Bluetooth |
| Photos | Galerie des 2 000 plus récentes, vignettes à la demande, aperçu, téléchargement et ouverture externe | Glisser-déposer directement depuis la galerie; l’aperçu permet déjà de glisser le fichier local |
| Intégration du téléphone | Notifications Android dans GNOME, réponses rapides, batterie, sonnerie à distance, partage de fichiers/liens et montage MTP | Contrôle multimédia, presse-papiers Android et pavé tactile distant |
| Applications | Lister avec les vraies icônes Android, lancer et contrôler l’écran intégré, désinstaller | Bascule et fenêtres d’applications Android simultanées; dépend de l’appareil |

Android garde le contrôle de l’appel dans son application Téléphone standard. L’accès aux appels entrants dans GNOME exige le relais de notifications DroidLink; les commandes d’appel sont envoyées par ADB. L’autorisation Photos est distincte. L’envoi est préparé dans Messages et doit être confirmé dans l’application Android. Le profil mains libres Bluetooth et les options d’appel varient selon l’appareil et l’opérateur.

## Accès Android et limites

- L’historique d’appels dépend de l’accès accordé au fournisseur `call_log` par Android; **Ouvrir sur Android** permet d’afficher le journal système si ADB ne le retourne pas.
- Le navigateur de fichiers est limité au stockage partagé `/sdcard`. Les données privées des applications nécessitent des privilèges root.
- La désinstallation depuis le menu contextuel retire l’application du profil Android utilisateur 0. Les composants préinstallés par le fabricant peuvent rester dans l’image système.
- La console exécute les commandes avec l’utilisateur `shell` d’ADB; elle ne donne pas accès à root.

## Licence

DroidDesk est distribué sous la licence **GNU GPL-3.0-only**; voir [`LICENSE`](LICENSE).
