# DroidLink

DroidLink est le compagnon Android de DroidDesk. Il relaie les notifications, les commandes, la batterie, les photos récentes et les icônes d'applications à travers un relais HTTPS auto-hébergé. Le téléphone et le PC ouvrent chacun une connexion sortante; ils peuvent se trouver sur des réseaux différents.

## Relais auto-hébergé

Le relais Docker et les instructions Nginx Proxy Manager sont dans [`relay/`](relay/README.md). Chaque installation choisit son propre domaine HTTPS. Configure la même adresse dans le menu **Configuration** de DroidDesk et dans **Accès** sur DroidLink. Le DNS public pointe vers NPM; le port privé du relais reste accessible uniquement depuis NPM sur le LAN ou un VPN.

Le relais conserve les jetons hachés et les informations d'état du téléphone dans SQLite. Les événements, photos et commandes ne sont conservés qu'en mémoire pour les transférer. Le relais et l'administrateur NPM voient le contenu en transit; l'hébergement doit rester sur un serveur de confiance.

## Fonctions de l'app compagnon

- Association par code temporaire à six chiffres, sans débogage USB ou options développeur.
- Relais des notifications après accord explicite d'Android, actions de notification et réponses rapides.
- Synchronisation de la batterie et sonnerie distante.
- Lecture des contacts après autorisation Android.
- Galerie MediaStore des 2 000 images récentes, avec vignettes et aperçus chargés à la demande.
- Envoi d'icônes d'applications Android.
- Accès photos et rôle SMS demandés dans Android lorsque l'utilisateur choisit ces fonctions.

Le composeur Android habituel reste celui du téléphone. La voix des appels exige le profil Bluetooth mains libres entre le téléphone et le PC. Le relais Internet ne transporte pas l'audio cellulaire. Les fonctions DroidDesk de contacts, journal, SMS, fichiers, contrôle d'écran et commandes d'appel sont encore en migration depuis ADB; l'association DroidLink elle-même ne le requiert plus.

## Compiler

Il faut Java (JDK) 17 ou plus récent, Android SDK Platform 37 et Android SDK Build-Tools 36.0.0. Android Studio permet d'installer les deux composants dans **Settings → Android SDK → SDK Platforms / SDK Tools**. Le script détecte les SDK dans `ANDROID_SDK_ROOT`, `ANDROID_HOME`, `~/Android/Sdk` ou `/usr/lib/android-sdk`.

```sh
./build-apk.sh
```

Le Wrapper téléchargera automatiquement Gradle 9.6.0 et les dépendances Gradle au premier build; une connexion Internet est nécessaire la première fois. L'APK sera créé ici : `app/build/outputs/apk/debug/app-debug.apk`. Transfère-le sur Android et ouvre-le pour l'installer; le fonctionnement de DroidLink ne demande pas ADB après l'installation.

## Installation et appairage

1. Déploie le relais Docker et configure ton domaine dans NPM.
2. Installe l'APK DroidLink comme une application Android ordinaire.
3. Avant l’association, configure le même domaine HTTPS dans **Configuration** sur DroidDesk et **Accès → Domaine du relais HTTPS** sur Android.
4. Dans DroidDesk, ouvre **DroidLink → Démarrer l'appairage**.
5. Entre le code affiché dans DroidLink.
6. Autorise l'accès aux notifications dans les réglages Android; autorise les photos si tu veux la galerie.

DroidLink est distribué sous GNU GPL-3.0-only.
