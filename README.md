# Clash Royale Analytics

## Setup

### 1. Environment Configuration

Create a `.env` file in the project root with the following configuration:

```env
# MongoDB Credentials
MONGO_ROOT_USER=root
MONGO_ROOT_PWD=YOUR_SECURE_ROOT_PASSWORD
MONGO_APP_DB=clash_royale
MONGO_APP_USER=data_scraper
MONGO_APP_PWD=YOUR_SECURE_USER_PASSWORD
MONGO_INITDB_DATABASE=clash_royale
MONGO_HOST=mongo
MONGO_PORT=27017

# MongoDB Backup Configuration
BACKUP_HOUR=03
BACKUP_MINUTE=30
BACKUP_RETENTION_DAYS=7

# Redis credentials
REDIS_PASSWORD=YOUR_SECURE_REDIS_PASSWORD

# JWT Secret for Admin Authentication
JWT_SECRET=YOUR_SECURE_JWT_SECRET_KEY

# Browser origins allowed to open the Halli Galli calibration WebSocket
HALLI_GALLI_WS_ALLOWED_ORIGINS=http://localhost,http://127.0.0.1

# Security Question Answers for Admin Access
MOST_ANNOYING_CARD="Card1"
MOST_SKILLFUL_CARD="Card2"
MOST_MOUSEY_CARD="Card3"

# Clash Royale API Keys
CR_API_APP_KEY_1=YOUR_APP_KEY
CR_API_SCRAPER_KEY_1=YOUR_SCRAPER_KEY
```

### Environment Variables Explained

#### MongoDB Configuration

- `MONGO_ROOT_USER`: MongoDB root administrator username (default: `root`)
- `MONGO_ROOT_PWD`: Secure password for the MongoDB root user
- `MONGO_APP_DB`: Database name for the application (default: `clash_royale`)
- `MONGO_APP_USER`: Application user for database operations (default: `data_scraper`)
- `MONGO_APP_PWD`: Secure password for the application user
- `MONGO_INITDB_DATABASE`: Database to initialize with the application user (must match `MONGO_APP_DB`)
- `MONGO_HOST`: Hostname of the MongoDB service (default: mongo when running in Docker Compose)
- `MONGO_PORT`: Port number where MongoDB is exposed (default: 27017)

#### Backup Configuration

- `BACKUP_HOUR`: Hour (0-23) when daily backups run (default: `03` = 3 AM)
- `BACKUP_MINUTE`: Minute (0-59) when daily backups run (default: `30`)
- `BACKUP_RETENTION_DAYS`: Number of days to keep backup files (default: `7`)

#### Redis credentials

- `REDIS_PASSWORD`: Secure password for the redis database

#### JWT Authentication

- `JWT_SECRET`: Secret key used for signing JWT tokens for admin authentication. Should be a long, random string for security.

#### Halli Galli connection and card loading

For another device on your home network, run `docker compose up -d --build` and open `http://HOST_LAN_IP`, replacing `HOST_LAN_IP` with the Docker host's private LAN address. `http://HOST_LAN_IP:8000` also works. No certificate or device installation is needed. For frontend development, run `npm start` in `frontend` and open `http://HOST_LAN_IP:5173`; Vite forwards `/api` and its WebSocket connection to the Docker frontend.

After Wordle, the browser opens `/api/auth/halli-galli/calibration` on the same host, using `ws://` for HTTP or `wss://` for HTTPS. A browser WebSocket cannot set the normal Bearer header, so its **first message** contains the Wordle token. The server verifies it, sends numbered probes with random nonces, and measures the matching replies. The returned calibration ID is short lived and can start one game. Game start POSTs that ID as `{"calibration_id": ...}` and the Wordle token in `Authorization: Bearer`; the server checks that both belong to the same Wordle attempt. Each verification token runs one session at a time (a new Wordle or game closes the previous one), yields a single next token, and allows a few retries (5 Wordles per CAPTCHA, 3 games per Wordle, 5 answer attempts per win, 3 removals per verification; see the `*_TOKEN_BUDGET` settings), tracked in the auth-state Redis. Nginx and Vite forward the WebSocket upgrade to the API.

The calibration WebSocket checks the browser's `Origin` before accepting it. `HALLI_GALLI_WS_ALLOWED_ORIGINS` is a comma-separated list of exact origins, including scheme and port; the repository's `.env` lists localhost. An HTTP origin with a private or loopback IP (`10/8`, `172.16/12`, `192.168/16`, `127/8`, or local IPv6) is also accepted automatically **only when it exactly matches the forwarded Host, including the port**. Other HTTP names, public IPs, and all HTTPS origins must be listed explicitly. This allows changing home LAN IPs without opening calibration to unrelated websites.

The game response names the initial cards and how many cards can remain visible. The browser preloads each prepared round once because another card POST would replace its encryption key and image version. Reveal returns the key, image ID, and version; the browser checks the saved ID and version before decrypting. Each encrypted image contains a 12-byte AES-GCM nonce followed by ciphertext and its authentication tag. Decryption uses the browser's `crypto.subtle` on HTTPS or localhost. When that API is unavailable on LAN HTTP, `@noble/ciphers` performs the same authenticated AES-GCM decryption in JavaScript. The PNG is shown through a temporary blob URL and that URL is revoked when the card leaves the pile. The next prepared card is preloaded while play continues.

LAN HTTP supports the game but does **not** protect Wordle tokens, reveal keys, or game traffic in transit. Use it on a home network you trust, without router port forwarding. For an HTTPS host or tunnel, point it at the Docker frontend on port `80`, add its exact browser origin to `HALLI_GALLI_WS_ALLOWED_ORIGINS` in `.env`, and recreate the API container. The host or tunnel must forward WebSocket upgrades. HTTPS protects transport and lets the browser use native Web Crypto; the card encryption alone does not replace HTTPS.

#### Security Questions

- `MOST_ANNOYING_CARD`: Answer to the first security question for admin access (Most annoying card in Clash Royale?)
- `MOST_SKILLFUL_CARD`: Answer to the second security question for admin access (Most skillful card in Clash Royale?)
- `MOST_MOUSEY_CARD`: Answer to the third security question for admin access (Most 'mousey/cutie/sweet' card in Clash Royale?)

**Note**: These security questions are used as a fun authentication method for admin operations like un-tracking players. They don't provide any real security, unless an actual non-guessable string is chosen for any of those answers, instead of a Clash Royale Card.

#### Clash Royale API Keys

- `CR_API_APP_KEY_<number>` and `CR_API_SCRAPER_KEY_<number>` in `.env` belong to separate app and scraper pools. Do not reuse a key across pools.
- Run `python backend/api_key_store/manage_env.py` to manage groups and keys. Adding a custom group prompts for its first key; deleting one removes all its keys. Option 7 keeps adding keys to one group until you press Enter. Key input is hidden. Custom groups are discovered from `.env` and need a caller that creates a `KeyStore` for that group. Restart affected services after edits.
- Per-key rates default to 1 request/second for both the app and scraper in their Python settings. Optional pool-wide caps are disabled by default; shared retry and timing defaults live in `KeyStoreConfig`.

The pools share a dedicated `redis-key-store` with `noeviction`. On startup, keys are validated once per boot generation; ready keys are leased least recently used across workers. HTTP 429 applies `Retry-After` or bounded backoff. Maintenance pauses requests and triggers sparse shared probes. After bounded acquisition retries, the API returns 503 with `Retry-After`, and the scraper defers its job. `/api/ready` reports usable key counts. For multiple replicas of one service, set a shared `CR_API_BOOT_ID` per deployment so they share startup validation.



### 2. Docker Setup (Production)

Create the MongoDB data volume once per machine, then start all services:

```bash
docker volume create clash-royale-analytics_mongo_data
docker compose up -d
```

The volume is marked `external` in `docker-compose.yml`, so Compose never
creates or deletes it. `docker compose down -v` resets the Redis and card image
volumes but keeps the database. Only `docker volume rm` removes it; restore from
`db/backups` afterwards (see [Restoring Data](#3-restoring-data)).

The API is available through nginx at `http://localhost/api` or
`http://localhost:8000/api`. Swagger UI is at `http://localhost:8000/docs`.
The optional `8000:80` mapping is marked in `docker-compose.yml`. Removing it
closes port 8000, but Swagger stays available at `/docs` on port 80 until the
documentation location in `frontend/nginx.conf` is removed or protected.

### 2. Local Development Setup

For local development, you can run the frontend and backend separately:

#### Backend

Start all required docker services for the backend (api, mongo, redis)

#### Frontend

1. Navigate to the frontend directory:

   ```bash
   cd frontend
   ```

2. Install Node.js dependencies:

   ```bash
   npm install
   ```

3. Start the development server:

   ```bash
   npm start
   ```

   The frontend will be available at `http://localhost:5173`

**Note**: The Vite development server proxies `/api` and `/card-images` through the Docker frontend at `http://localhost:80`, so the frontend container and backend services need to be running for local development.

### Card images

The data scraper mirrors the card art of the Clash Royale CDN on every card
refresh (every 6 hours): it scales each image to 240 px wide, converts it to
WebP (11-16 KB instead of ~150 KB), and writes the whole set to the
`card-images` Docker volume. The frontend's nginx serves that volume read-only
at `/card-images/<version>/<cardId>[-<variant>].webp`. Details are in
`backend/data_scraper/src/card_images.py`.

- **Versioned sets.** A set is published under a hash of its content and only
  once every file is written. The card list from `/api/cards` points to it
  through `imageUrls`, next to Clash Royale's original `iconUrls`. Set URLs
  never change content, so browsers cache them for a year.
- **Fallbacks.** The frontend tries the self-hosted image, then the CDN
  original, then a placeholder that names the card and its variant (art the
  CDN does not have yet, unknown cards).
- **Reconstructible.** The volume holds no data of its own. Deleting it (e.g.
  `docker compose down -v`) is safe: the scraper notices within 10 minutes and
  rebuilds the same set from the CDN; browsers use the CDN meanwhile. Replaced
  sets are removed after 7 days.
- **Paths.** The scraper writes to `CARD_IMAGES_DIR` (default
  `/data/card-images`), nginx reads `/usr/share/nginx/card-images`. Both are
  mount points of the same volume in `docker-compose.yml`; change them
  together.
- **Single scraper.** Run one data scraper process. The card loop is the only
  writer of the image volume and of the cached card list and has no leader
  election (see the NOTE in `backend/data_scraper/src/main.py`).
- **Tower troops.** The card list's `supportItems` (tower troops) are
  mirrored like the regular `items`. The frontend merges both into one list,
  tagged by `category`.

### Tower troops

A deck is its eight cards plus its tower troop (`supportCards` in the battle
log). The same cards with a different tower are a different deck in the deck
statistics; card levels and card order never split a deck. Deck statistics
and filters use the tracked player's own tower, never a teammate's.

- **None.** Every battle should have a tower. A battle without tower data
  (absent, null, or empty `supportCards`) falls into the "None" category
  instead of being shown as Tower Princess. The card filter and the Cards page
  only show None when such battles exist.
- **Card filter.** Tower troops have their own row in the card filter. In
  Include mode one tower can be selected, and a deck has to contain it next to
  all selected cards. In Match mode any number can be selected; together they
  count as one match term, met when the deck's tower is one of them.
- **Usage rate.** As with card filters, a deck's usage rate is its share of
  the battles of all decks currently shown, after card and tower filters. The
  shown decks always add up to 100%.
- **Copy link.** The game needs a tower in a copied deck, so a deck without
  tower data copies with Tower Princess, the default tower.
- **Statistics.** The Cards page lists tower usage and win rates in a separate
  section. Battle history shows every player's tower, with its level, without
  a tower filter. Towers sit on the outside of each deck: left for the
  player's own decks, right for opponents.

### 3. Restoring Data

Mongo backups use `mongodump --gzip`: collection data and metadata are
compressed as `.bson.gz` and `.metadata.json.gz` files inside timestamped
backup directories. Both restore scripts only accept these compressed dumps and
stop with an error, before touching the database, for a directory without
`.bson.gz` files.

Example Commands for restoring data

#### On Linux/macOS/WSL

```bash
cd db
./restore.sh ./backups/clash_royale_YYYY-MM-DD_HH-MM-SS
```

#### On Windows PowerShell

```bash
cd db
.\restore.ps1 .\backups\clash_royale_YYYY-MM-DD_HH-MM-SS
```

# TODO

2. Add celebratory animation upon successfully completing the full authentication flow for removal permissions + check and adjust how many tries one gets for the security questions (the token that unlocks security questions try should be locked after 3 attempts?, via a counter field or something the like in the token itself or as a session on the server tied to that token?)

3. Add another step into the auth flow, has to be time consuming and challenging game before the questions step + server-side verifiable game that tests accuracy/reaction time/skill? 🗿
