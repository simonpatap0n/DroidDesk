# DroidDesk

DroidDesk est une application GTK4/libadwaita pour gérer un téléphone Android depuis GNOME avec ADB, scrcpy et BlueZ.

## Fonctionnalités

- **Appels** : pavé numérique, ouverture du composeur Android et commandes répondre/terminer. Le téléphone effectue l’appel avec sa SIM; il faut confirmer sur le téléphone.
- **Contacts** et **Récents** : recherche, rappel et ouverture des pages système correspondantes. Android peut refuser l’accès ADB au journal d’appels.
- **SMS** : préremplir le destinataire et le texte dans l’application Messages Android, puis confirmer l’envoi sur le téléphone.
- **Écran et apps** : ouvrir scrcpy, parcourir les applications avec une icône de lanceur, les ouvrir, afficher leurs réglages ou les désinstaller avec le clic droit.
- **Fichiers** : parcourir `/sdcard`, créer des dossiers, envoyer des fichiers vers Android et télécharger des fichiers vers Debian.
- **Bluetooth** : choisir un contrôleur (USB ou intégré), activer la radio, rechercher, appairer, connecter, renommer et oublier des appareils.
- **ADB** : appairage sans fil, connexions TCP/IP, redémarrage/récupération/bootloader, touches système, captures d’écran, logcat, propriétés Android et console `adb shell`.
- **Appareil** : afficher le fabricant, le modèle, Android, la batterie et le stockage partagé.

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

## Appels, écran et SMS

Le composeur ouvre l’application Téléphone Android avec le numéro préparé. La SIM du téléphone passe ensuite l’appel. **Répondre** et **Terminer** envoient des événements clavier Android; leur prise en charge dépend du modèle et de la version Android.

scrcpy s’ouvre dans sa propre fenêtre pour transmettre l’écran et les commandes souris/clavier. Il est lancé avec `--no-audio`; l’audio des appels reste sur le profil Bluetooth mains libres du téléphone. Voir la [documentation officielle de scrcpy sur l’audio](https://github.com/Genymobile/scrcpy/blob/master/doc/audio.md).

La page **SMS** ouvre l’application Messages du téléphone avec le numéro et le texte préremplis. L’utilisateur vérifie le message et appuie sur Envoyer dans Android; DroidDesk ne lit ni n’envoie les SMS en arrière-plan. Une future messagerie entièrement intégrée nécessiterait une application compagnon Android et le rôle SMS par défaut, avec les permissions Android correspondantes.

## Accès Android et limites

- Android peut refuser à ADB l’accès au journal d’appels. `READ_CALL_LOG` est une permission restreinte; le bouton **Ouvrir sur Android** permet toujours d’afficher l’historique dans l’application Téléphone.
- Le navigateur de fichiers est limité au stockage partagé `/sdcard`. Les données privées des applications nécessitent des privilèges root.
- La désinstallation depuis le menu contextuel retire l’application du profil Android utilisateur 0. Les composants préinstallés par le fabricant peuvent rester dans l’image système.
- La console exécute les commandes avec l’utilisateur `shell` d’ADB; elle ne donne pas accès à root.

## Licence

DroidDesk est distribué sous la licence **GNU GPL-3.0-only**; voir [`LICENSE`](LICENSE).
