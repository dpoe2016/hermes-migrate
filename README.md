# Hermes Migration Tool

Vollständige Migration des [Hermes AI Agent](https://github.com/karrotcomm/hermes) auf einen neuen Server.

## Funktionen

- **Backup** — Stoppet Gateway, synchronisiert SQLite, erstellt `.tar.gz`-Archiv
- **Restore** — Stellt Archiv auf neuem Server, sichert vorhandene Daten optional
- **Verify** — Prüft Archiv auf Vollständigkeit
- **Info** — Zeigt Installationsinformationen
- **List** — Listet alle zu migrierenden Komponenten

## Installation

```bash
sudo cp hermes-migrate.py /usr/local/bin/hermes-migrate
sudo chmod +x /usr/local/bin/hermes-migrate
```

## Verwendung

### Backup

```bash
# Automatisch (Zeitstempel-Namen)
hermes-migrate backup

# Mit manuellem Pfad
hermes-migrate backup -o /tmp/hermes-backup.tar.gz

# Inklusive optionaler Daten (logs/, backups/)
hermes-migrate backup --include-optional
```

### Restore

```bash
hermes-migrate restore hermes-migration-20250101-120000-abc123.tar.gz
```

### Verify

```bash
hermes-migrate verify hermes-migration-20250101-120000-abc123.tar.gz
```

### Info

```bash
hermes-migrate info
hermes-migrate list
```

## Was wird migriert?

**Kritische Dateien (immer):**
- `config.yaml` — Hauptkonfiguration
- `.env` — Umgebungsvariablen, API-Keys
- `auth.json` — Auth-Tokens
- `state.db` (+ WAL/SHM) — SQLite Zustandsdatenbank
- `install_id` — Installationsidentität
- `kanban.db`, `projects.db` — Task-Daten

**Kritische Verzeichnisse (immer):**
- `skills/`, `sessions/`, `memories/`, `cron/`
- `scripts/`, `plugins/`, `plugin-data/`
- `mcp-tokens/`, `pairing/`, `vault/`, `profiles/`
- `gateway/`, `lsp/`, `hooks/`, `node/`

**Optionale Daten (`--include-optional`):**
- `backups/`, `logs/`

**Automatisch ausgeschlossen:**
- Lock-Dateien (`*.lock`, `*.pid`, `*.sock`)
- Temporäre Dateien (`*.tmp`)
- Cache (`cache/`, `tools/`)
- Protokolldateien (`*.log`)

## Sicherheitshinweise

- Das Tool stoppt das Hermes Gateway vor dem Backup automatisch
- SQLite-Datenbanken werden konsistent flushen (WAL-Checkpoint)
- Vorhandene Daten auf dem Zielsystem können gesichert werden
- `install_id` bleibt erhalten — Identität der Installation

## Anforderungen

- Python 3.8+
- Hermes AI Agent installiert unter `~/.hermes/`

## Lizenz

MIT
