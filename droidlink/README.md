# DroidLink

DroidLink est le compagnon Android de DroidDesk. Le bureau affiche un code à usage unique, puis le téléphone se relie par le tunnel ADB local. L’accès aux notifications maintient aussi le canal de commandes utilisé par les fonctions du bureau.

## Fonctions

- Interface Compose Material 3 avec couleurs dynamiques Android.
- Détection du fabricant, du modèle, de la version Android et de la batterie.
- Appairage par code à usage unique via `adb reverse`.
- Relais des titres, textes et actions disponibles des notifications vers DroidDesk après autorisation explicite.
- Réponse rapide ou action d’appel depuis DroidDesk quand l’application Android source fournit une action dans sa notification.
- Filtre par application sur le téléphone; ouverture et rejet des notifications depuis le bureau.
- Épinglage local des notifications dans DroidDesk.
- Interface Téléphone par défaut, signalement des appels entrants/en cours, commandes répondre/refuser/terminer, sélection de route audio, micro et relais des 100 appels récents après autorisation.
- Galerie MediaStore des 2 000 images récentes, avec autorisation Photos explicite et vignettes chargées à la demande.
- Dissociation depuis le téléphone et retrait local du jeton.

Le rôle Téléphone Android est requis pour l’interface d’appel intégrée. Pour transmettre le son au PC, connecte Debian au téléphone comme casque Bluetooth mains libres et choisis **Audio PC · Bluetooth** dans DroidDesk. La route dépend de ce que le téléphone expose pendant l’appel. La galerie demande l’autorisation Photos et transfère les vignettes par le tunnel ADB local.

Les conversations et l’envoi SMS/MMS complets restent à intégrer. Android ne permet l’écriture et la réception complète dans le fournisseur SMS qu’à l’application SMS par défaut, laquelle doit aussi gérer la réception MMS. Les réponses de notification sont disponibles quand l’application source les expose. Consulte le tableau de livraison dans le [README de DroidDesk](../README.md#cible-phone-link).

DroidLink n’utilise aucun serveur cloud. Le serveur DroidDesk écoute seulement sur `localhost`; le téléphone le rejoint par le tunnel ADB appairé.

## Compiler

Il faut Java (JDK) 17 ou plus récent, Android SDK Platform 37 et Android SDK Build-Tools 36.0.0. Android Studio permet d’installer les deux composants dans **Settings → Android SDK → SDK Platforms / SDK Tools**. Le script détecte les SDK dans `ANDROID_SDK_ROOT`, `ANDROID_HOME`, `~/Android/Sdk` ou `/usr/lib/android-sdk`.

```sh
./build-apk.sh
```

Le Wrapper téléchargera automatiquement Gradle 9.6.0 et le projet téléchargera ses dépendances lors du premier build; une connexion Internet est donc nécessaire la première fois. Pour lancer la compilation directement :

```sh
./gradlew assembleDebug
```

L’APK de débogage sera créé ici : `app/build/outputs/apk/debug/app-debug.apk`. Pour l’installer sur un téléphone autorisé dans ADB :

```sh
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

Le script `./build-apk.sh` vérifie Java et les composants Android avant de lancer Gradle et affiche l’emplacement de l’APK. Pour changer le SDK détecté automatiquement, définis `ANDROID_SDK_ROOT`.

## Installation et appairage

1. Installe l’APK DroidLink et active le débogage USB ou ADB sans fil sur le téléphone.
2. Connecte le téléphone à DroidDesk et ouvre **DroidLink → Démarrer l’appairage**.
3. Entre le code à six chiffres dans DroidLink.
4. Dans DroidLink, autorise l’accès aux notifications pour les relayer et maintenir le canal de commandes.
5. Dans **Accès**, autorise les images pour la galerie, choisis DroidLink comme application Téléphone, puis autorise l’historique d’appels.

Le serveur local de DroidDesk et le transfert ADB sont nécessaires pour l’appairage. DroidLink est distribué sous GNU GPL-3.0-only.
