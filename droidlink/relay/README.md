# Relais DroidLink

Petit relais auto-hébergeable pour DroidDesk et le compagnon Android. Les deux appareils ouvrent des connexions HTTPS sortantes vers le relais; aucun port entrant n'est nécessaire sur le PC ou le téléphone.

## Déploiement avec Docker et Nginx Proxy Manager sur deux sites

Le conteneur Docker et NPM peuvent être sur deux machines et deux sites différents. Ils doivent toutefois avoir une route réseau entre eux. Si leurs adresses LAN ne se joignent pas, établis d'abord un VPN privé entre les sites (par exemple WireGuard), puis utilise les adresses VPN des deux hôtes. NPM reste le seul service exposé sur Internet.

1. Fais pointer le DNS `droidlink.patapon.lol` vers l'adresse publique de Nginx Proxy Manager.
2. Dans le dossier `droidlink/relay`, copie `.env.example` vers `.env`. Renseigne `DROIDLINK_BIND_ADDRESS` avec l'adresse de la machine Docker joignable depuis NPM : l'adresse LAN si les hôtes partagent un réseau, ou l'adresse VPN de Docker s'ils sont sur des sites séparés.
3. Lance `docker compose up -d --build` sur l'hôte Docker. Le relais écoutera sur le port privé `17892` de cette machine.
4. Configure le pare-feu de Docker pour autoriser TCP `17892` uniquement depuis l'adresse NPM sur le réseau partagé ou le VPN. N'ouvre pas ce port sur Internet et ne fais pas de redirection de port publique vers Docker.
5. Dans NPM, crée un Proxy Host :
   - **Domain Names** : `droidlink.patapon.lol`
   - **Scheme** : `http`
   - **Forward Hostname / IP** : l'adresse LAN ou VPN de la machine Docker, routable depuis l'hôte NPM
   - **Forward Port** : `17892`
   - active **Block Common Exploits**; **Websockets Support** n'est pas nécessaire
   - dans **SSL**, demande le certificat Let's Encrypt et active **Force SSL**.
6. Vérifie `https://droidlink.patapon.lol/healthz`; la réponse doit être `{"ok":true}`.

Le service exige `X-Forwarded-Proto: https` pour toutes les routes de compte. Le lien NPM → Docker utilise HTTP; il doit rester sur un réseau privé de confiance ou sur le VPN intersite, avec le pare-feu limité à NPM. Si les serveurs sont dans deux endroits sans VPN ou route privée entre eux, NPM ne pourra pas joindre ce relais. Sur Internet, seuls les ports 80/443 de NPM sont nécessaires.

La base SQLite du volume Docker conserve les identifiants hachés et les renseignements du téléphone. Le contenu des notifications, photos et commandes est transmis uniquement dans des files en mémoire; il n'est pas écrit dans la base. Les files sont perdues au redémarrage du conteneur, mais l'association demeure.

Les identifiants sont différents pour le bureau et le téléphone. L'appairage utilise un code temporaire à six chiffres, limité dans le temps et soumis à une limite de tentatives. Le lien HTTPS protège les échanges sur Internet. Le serveur et l'administrateur NPM peuvent néanmoins voir le contenu en transit, puisque le relais doit le transférer; auto-héberge-le sur ton propre réseau de confiance.

## Routes

- `POST /v1/sessions` : enregistrer une session de bureau et son code à usage unique.
- `POST /v1/pair` : associer DroidLink au code et recevoir le jeton téléphone.
- `/v1/sessions/{id}/events` : événements du téléphone vers le bureau.
- `/v1/sessions/{id}/commands` : commandes du bureau vers le téléphone.
- `/v1/sessions/{id}/phone` et `/status` : état de connexion et renseignements de l'appareil.
- `POST /v1/sessions/{id}/unpair` : révoquer la session.
- `GET /healthz` : état de santé du conteneur.
