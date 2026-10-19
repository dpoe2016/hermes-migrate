# Hermes Migration Tool

Full migration of the [Hermes AI Agent](https://github.com/karrotcomm/hermes) to a new server.

## Features

- **Backup** — Stop gateway, synchronize SQLite, create `.tar.gz` archive
- **Restore** — Restore archive on new server, optionally preserve existing data
- **Verify** — Check archive for completeness
- **Info** — Show installation information
- **List** — List all components to be migrated

## Installation

```bash
sudo cp hermes-migrate.py /usr/local/bin/hermes-migrate
sudo chmod +x /usr/local/bin/hermes-migrate
```

## Usage

### Backup

```bash
# Automatic (timestamp name)
hermes-migrate backup

# With manual path
hermes-migrate backup -o /tmp/hermes-backup.tar.gz

# Including optional data (logs/, backups/)
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

## What gets migrated?

**Critical files (always):**
- `config.yaml` — Main configuration
- `.env` — Environment variables, API keys
- `auth.json` — Auth tokens
- `state.db` (+ WAL/SHM) — SQLite state database
- `install_id` — Installation identity
- `kanban.db`, `projects.db` — Task data

**Critical directories (always):**
- `skills/`, `sessions/`, `memories/`, `cron/`
- `scripts/`, `plugins/`, `plugin-data/`
- `mcp-tokens/`, `pairing/`, `vault/`, `profiles/`
- `gateway/`, `lsp/`, `hooks/`, `node/`

**Optional data (`--include-optional`):**
- `backups/`, `logs/`

**Automatically excluded:**
- Lock files (`*.lock`, `*.pid`, `*.sock`)
- Temporary files (`*.tmp`)
- Cache (`cache/`, `tools/`)
- Log files (`*.log`)

## Security notes

- The tool automatically stops the Hermes gateway before backup
- SQLite databases are flushed consistently (WAL checkpoint)
- Existing data on the target system can be backed up
- `install_id` is preserved — installation identity remains the same

## Requirements

- Python 3.8+
- Hermes AI Agent installed at `~/.hermes/`

## License

MIT
