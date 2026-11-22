# DroidDesk

Application GTK4/libadwaita pour gérer un téléphone Android depuis GNOME avec ADB, scrcpy et BlueZ.

## Menu

- **Appels** : pavé numérique, ouverture du composeur du téléphone, commandes répondre/terminer et raccourcis vers les contacts et le journal d’appels. L’appel cellulaire est passé par le téléphone et sa SIM.
- **Contacts** et **Récents** : lire les numéros depuis Android, rechercher et rappeler.
- **SMS** : préparer le destinataire et le texte, puis ouvrir l’application Messages Android pour confirmer l’envoi sur le téléphone.
- **Écran et apps** : ouvrir scrcpy, lister les apps qui ont une icône de lanceur, les ouvrir, afficher leurs réglages et les désinstaller depuis le menu clic droit.
- **Fichiers** : parcourir `/sdcard`, créer des dossiers, envoyer des fichiers vers le téléphone et télécharger des fichiers vers Debian.
- **Bluetooth** : choisir l’adaptateur (avec son emplacement USB/intégré), activer la radio, rechercher, appairer, connecter, renommer et oublier des appareils.
- **ADB** : serveur, appairage sans fil, connexion TCP/IP, redémarrage/récupération/bootloader, touches système, captures, logcat, propriétés Android et console `adb shell`.
- **Appareil** : détection du fabricant, modèle, version Android, batterie et stockage, avec une miniature de téléphone colorée selon la marque.

scrcpy s’ouvre dans sa fenêtre dédiée avec `--no-audio`; l’audio reste sur le profil Bluetooth mains libres du téléphone. scrcpy documente `--serial` pour choisir un téléphone et `--no-audio` pour couper son transfert audio ([options officielles](https://github.com/Genymobile/scrcpy/blob/master/doc/audio.md)).

Le bouton **Ouvrir le composeur** prépare l’appel sur Android. Le téléphone effectue ensuite l’appel par sa SIM, après confirmation. **Répondre** et **Terminer** envoient les commandes Android correspondantes; leur disponibilité dépend du téléphone et de sa version d’Android.

L’envoi de SMS préremplit l’application Messages du téléphone; DroidDesk ne les envoie pas en arrière-plan. Lire et envoyer les SMS directement dans DroidDesk demanderait une application compagnon Android et l’autorisation SMS correspondante.

L’accès aux contacts et au journal d’appels est contrôlé par Android. `READ_CALL_LOG` est une permission restreinte; selon le téléphone, Android peut donc refuser la liste interne. Le bouton **Ouvrir sur Android** garde accès au journal du composeur système.

Le navigateur de fichiers est limité au stockage partagé `/sdcard`. Les dossiers privés des applications demandent des privilèges root et ne sont pas affichés.

## Dépendances

Il faut Python 3, PyGObject, GTK4, libadwaita, BlueZ, ADB et scrcpy. Sur Debian, installez généralement `bluez`, `adb`, `scrcpy`, `python3-gi`, `gir1.2-gtk-4.0` et `gir1.2-adw-1`.

Le téléphone doit être autorisé dans ADB. Activez **Options pour les développeurs → Débogage USB**, branchez le téléphone, puis acceptez la demande d’autorisation. ADB peut aussi utiliser le débogage sans fil.

## Lancer depuis les sources

```sh
python3 src/droiddesk.py
```

## Installer dans GNOME

```sh
meson setup build --prefix="$HOME/.local"
meson install -C build
```

L’application apparaît ensuite dans le lanceur sous **DroidDesk**.
