# Relais DroidLink

Relais auto-hébergé pour DroidDesk et le compagnon Android. Le PC et le téléphone ouvrent chacun une connexion HTTPS sortante; ils peuvent être sur des réseaux différents.

## Déploiement Docker Compose et Nginx Proxy Manager

Tu as besoin d'un nom de domaine que tu contrôles, d'un DNS public pointant vers Nginx Proxy Manager (NPM) et d'un chemin réseau privé entre NPM et l'hôte Docker. Si les machines sont sur des sites différents, relie-les par VPN. Seul NPM doit être exposé sur Internet.

1. Sur l'hôte Docker, va dans `droidlink/relay` et prépare la configuration :

   ```sh
   cp .env.example .env
   nano .env
   ```

   Remplace `DROIDLINK_BIND_ADDRESS` par l'adresse LAN de l'hôte Docker joignable depuis NPM. Si NPM atteint le serveur par VPN, utilise plutôt l'adresse VPN. Cette adresse limite l'écoute du port du relais à l'interface privée voulue.

2. Construis et démarre le relais :

   ```sh
   docker compose up -d --build
   docker compose ps
   ```

   Le service écoute sur le port `17892` de l'adresse privée choisie. Les données SQLite sont conservées dans le volume `droidlink_relay_data`.

3. Dans le DNS public, crée un enregistrement `A` (et, si utilisé, `AAAA`) pour ton domaine, qui pointe vers l'adresse publique de NPM. Le pare-feu Internet n'a besoin d'exposer que les ports 80/443 de NPM; n'ouvre pas le port 17892.

4. Dans NPM, ajoute un **Proxy Host** :
   - **Domain Names** : ton nom de domaine, par exemple `relay.example.net`
   - **Scheme** : `http`
   - **Forward Hostname / IP** : l'adresse LAN ou VPN de l'hôte Docker
   - **Forward Port** : `17892`
   - active **Block Common Exploits**
   - dans **SSL**, demande un certificat Let's Encrypt et active **Force SSL**
   - **Websockets Support** n'est pas nécessaire

5. Limite le pare-feu de l'hôte Docker pour que TCP `17892` soit accessible depuis l'adresse NPM seulement. Garde ce trafic sur le LAN de confiance ou le VPN.

6. Vérifie le certificat et le relais :

   ```sh
   curl -i https://relay.example.net/healthz
   ```

   Remplace le nom d'exemple par ton domaine. La réponse attendue est `200` avec `{"ok":true}`.

7. Configure exactement la même adresse HTTPS dans le menu **Configuration** de DroidDesk et dans **Accès → Domaine du relais HTTPS** sur Android. Fais-le avant de lancer l'association par code.

Le relais exige `X-Forwarded-Proto: https` sur les routes de compte; NPM transmet normalement cet en-tête. La liaison NPM → Docker utilise HTTP et doit rester privée. Les notifications, photos et commandes transitent par le serveur; auto-héberge-le sur un système de confiance.

## Données et disponibilité

SQLite conserve les jetons hachés et les renseignements d'état du téléphone. Les notifications, photos, commandes, images webcam et blocs audio transitent uniquement en mémoire; pour la caméra, le relais garde seulement la dernière image JPEG par session, et pour le micro une file limitée à 30 blocs PCM. Le relais et son administrateur peuvent voir ces contenus en transit. Les données en mémoire disparaissent au redémarrage, mais les associations persistent.

## Routes

- `POST /v1/sessions` : enregistrer la session de bureau et son code à usage unique.
- `POST /v1/pair` : associer DroidLink au code et recevoir le jeton du téléphone.
- `/v1/sessions/{id}/events` : événements du téléphone vers le bureau.
- `POST /v1/sessions/{id}/camera/frame` et `GET /v1/sessions/{id}/camera/frame?after=…&wait=…` : dernière image webcam binaire du téléphone vers le bureau; maximum 512 Ko par image.
- `POST /v1/sessions/{id}/audio/chunk` et `GET /v1/sessions/{id}/audio/chunk?after=…&wait=…` : blocs PCM mono 16 kHz du micro vers le bureau; maximum 32 Ko par bloc, file en mémoire limitée.
- `/v1/sessions/{id}/commands` : commandes du bureau vers le téléphone.
- `/v1/sessions/{id}/phone` et `/status` : état et renseignements de l'appareil.
- `POST /v1/sessions/{id}/unpair` : révoquer l'association.
- `GET /healthz` : état de santé du conteneur.
