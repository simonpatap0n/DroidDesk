# DroidDesk

DroidDesk est une application GTK4/libadwaita pour gérer un téléphone Android depuis GNOME avec ADB, scrcpy et BlueZ.

## Fonctionnalités

- **Appels** : pavé numérique et intégration d’appels entrants/en cours avec DroidLink quand il est choisi comme application Téléphone Android; répondre, refuser, terminer, sélectionner la route audio et couper le micro.
- **Contacts** et **Récents** : recherche, rappel et journal d’appels via DroidLink quand l’autorisation Android est accordée; ouverture des pages système correspondantes en secours.
- **SMS** : afficher les messages que l’application SMS expose dans ses notifications, répondre avec ses actions rapides et préparer un nouveau message dans Messages Android.
- **Écran et apps** : ouvrir scrcpy, parcourir les applications avec leurs vraies icônes Android, les ouvrir, afficher leurs réglages ou les désinstaller avec le clic droit.
- **Fichiers et photos** : parcourir `/sdcard`, envoyer et télécharger des fichiers; charger jusqu’à 2 000 photos récentes avec vignettes, aperçu, ouverture dans l’application Debian associée et téléchargement. Glisser une photo depuis son aperçu pour la déposer dans une autre application.
- **Bluetooth** : choisir un contrôleur (USB ou intégré), activer la radio, rechercher, appairer, connecter, renommer et oublier des appareils.
- **ADB** : appairage sans fil, connexions TCP/IP, redémarrage/récupération/bootloader, touches système, captures d’écran, logcat, propriétés Android et console `adb shell`.
- **Appareil** : afficher le fabricant, le modèle, Android, la batterie et le stockage partagé.
- **Notifications** : chaque alerte Android est aussi envoyée au centre de notifications GNOME; les réponses rapides Android restent utilisables depuis le bureau.
- **DroidLink** : association ADB locale, appels intégrés, commandes d’appel, photos et icônes d’applications. La tuile Réglages rapides ouvre directement l’écran Appels.

## Dépendances Debian

```sh
sudo apt update
sudo apt install bluez adb scrcpy python3 python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 meson ninja-build
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

Le projet Android est dans [`droidlink/`](droidlink/README.md). Il fournit une interface Material 3, l’association au bureau par un code à usage unique, les appels, les photos, les icônes et les commandes distantes. DroidDesk ouvre son serveur uniquement sur `127.0.0.1`; le téléphone le rejoint avec `adb reverse`. Aucun service cloud n’est utilisé.

Pour préparer l’APK, installe Java 17 ou plus récent, **Android SDK Platform 37** et **Android SDK Build-Tools 36.0.0** dans Android Studio (**Settings → Android SDK**), puis lance :

```sh
cd droidlink
./build-apk.sh
```

Le Wrapper fourni télécharge Gradle 9.6.0 et les dépendances Gradle au premier build; Internet est requis à cette étape. `ANDROID_SDK_ROOT` peut indiquer l’emplacement du SDK si le script ne le détecte pas automatiquement. L’APK de débogage se trouve dans `droidlink/app/build/outputs/apk/debug/app-debug.apk`. Installe-le avec Android Studio ou `adb install -r app/build/outputs/apk/debug/app-debug.apk`.

## Appels, écran et SMS

Pour les appels intégrés, ouvre DroidLink sur le téléphone et choisis **DroidLink comme application Téléphone**; autorise aussi les appels sortants dans **DroidLink → Accès**. Le cellulaire garde sa SIM et son réseau; Debian peut composer, afficher les appels entrants, répondre, refuser, terminer et régler l’audio. Pour parler et écouter sur le PC, le téléphone doit être appairé à Debian comme casque mains libres HFP/HSP. Dans **Bluetooth → appareil du téléphone → ⋮ → Connecter pour les appels · HFP**, DroidDesk peut demander explicitement ce profil.

Après installation de l’APK, ouvre les Réglages rapides Android, touche **Modifier** et ajoute la tuile **DroidLink**. Android laisse l’utilisateur choisir la position de cette tuile.

scrcpy s’ouvre dans sa propre fenêtre pour transmettre l’écran et les commandes souris/clavier. Il est lancé avec `--no-audio`; l’audio des appels reste sur le profil Bluetooth mains libres du téléphone. Voir la [documentation officielle de scrcpy sur l’audio](https://github.com/Genymobile/scrcpy/blob/master/doc/audio.md).

La page **SMS** affiche les messages récents visibles dans les notifications de l’application SMS par défaut; elle peut ouvrir la conversation sur Android ou envoyer une réponse rapide quand l’application expose cette action. Les nouveaux messages et les destinataires multiples s’ouvrent dans Messages avec le texte prérempli. L’accès à l’historique complet, l’envoi direct et les pièces jointes SMS/MMS restent à intégrer.

## Cible Phone Link

La liste suivante sert de critères de livraison; les fonctions marquées « à intégrer » ne sont pas encore disponibles dans cette version.

| Domaine | Disponible maintenant | À intégrer dans DroidLink |
| --- | --- | --- |
| SMS | Messages récents exposés par les notifications, réponse rapide et composition SMS/groupe dans Messages Android | Historique complet, envoi direct, médias SMS/MMS et GIF |
| Appels | Composer, suivre les appels entrants/en cours, répondre/refuser/terminer, choisir la route audio, couper le micro et afficher les 100 appels récents avec les autorisations DroidLink | Historique plus long et permissions qui varient selon Android |
| Photos | Galerie des 2 000 plus récentes, vignettes à la demande, aperçu, téléchargement et ouverture externe | Glisser-déposer directement depuis la galerie; l’aperçu permet déjà de glisser le fichier local |
| Notifications | Notifications Android dans GNOME, actions Android et réponses rapides, ouvrir/ignorer, épinglage local et filtre par application sur Android | Historique complet des alertes déjà retirées du téléphone |
| Applications | Lister avec les vraies icônes Android, lancer, désinstaller et contrôler l’écran avec scrcpy | Bascule et fenêtres d’applications intégrées; le multi-fenêtre dépend d’Android et de l’appareil |

Android demande le rôle Téléphone et les permissions d’appel pour l’historique intégré, l’autorisation Photos pour la galerie et l’accès aux notifications pour le canal local de commandes. Le rôle SMS et les permissions associées sont séparés; DroidDesk ne le demande pas encore. Android réserve la réception et l’écriture du fournisseur SMS à l’application SMS par défaut. Le profil mains libres Bluetooth et les fonctions multimédias varient selon l’appareil et l’opérateur.

## Accès Android et limites

- DroidLink demande le rôle Téléphone puis l’autorisation Android `READ_CALL_LOG` pour synchroniser les 100 appels récents. Si l’accès est refusé, **Ouvrir sur Android** affiche toujours le journal du téléphone.
- Le navigateur de fichiers est limité au stockage partagé `/sdcard`. Les données privées des applications nécessitent des privilèges root.
- La désinstallation depuis le menu contextuel retire l’application du profil Android utilisateur 0. Les composants préinstallés par le fabricant peuvent rester dans l’image système.
- La console exécute les commandes avec l’utilisateur `shell` d’ADB; elle ne donne pas accès à root.

## Licence

DroidDesk est distribué sous la licence **GNU GPL-3.0-only**; voir [`LICENSE`](LICENSE).
