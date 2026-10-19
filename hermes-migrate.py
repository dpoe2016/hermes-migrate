#!/usr/bin/env python3
"""
Hermes Migration Tool - Vollständige Migration von Hermes Agent auf einen neuen Server.

Verwendung:
  hermes-migrate backup          - Erstellt ein Migration-Archiv der aktuellen Hermes-Installation
  hermes-migrate restore <file>  - Stellt Hermes aus einem Migration-Archiv wieder
  hermes-migrate info            - Zeigt Informationen über die aktuelle Installation
  hermes-migrate verify <file>   - Prüft ein Migration-Archiv auf Vollständigkeit
  hermes-migrate list            - Zeigt alle zu migrierenden Komponenten
"""

import argparse
import datetime
import fnmatch
import os
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import time
from pathlib import Path

HERMES_HOME = Path.home() / ".hermes"

# Dateien, die MISSMÜSSEN
CRITICAL_ITEMS = [
    "config.yaml",
    "config.yaml.bak*",
    ".env",
    ".env~",
    "auth.json",
    "state.db",
    "state.db-wal",
    "state.db-shm",
    "SOUL.md",
    "install_id",
    "kanban.db",
    "kanban.db-shm",
    "kanban.db-wal",
    "projects.db",
    "shared-state.db",
    ".hermes_history",
    ".skills_prompt_snapshot.json",
    "channel_directory.json",
    "gateway_state.json",
    "gateway-starts.log",
    "processes.json",
    "tui-theme-boot.json",
    "provider_models_cache.json",
    "ollama_cloud_models_cache.json",
    "models_dev_cache.json",
    "models_dev_cache.etag",
]

# Verzeichnisse, die MISSMÜSSEN (nutzdaten, keine Plattform-Binaries!)
# tools/, platforms/, node/, bin/, hermes-agent/ werden NICHT mitgenommen –
# sie werden auf dem Zielrechner automatisch neu installiert.
CRITICAL_DIRS = [
    "skills",
    "sessions",
    "memories",
    "cron",
    "scripts",
    "plugins",
    "plugin-data",
    "mcp-tokens",
    "pairing",
    "vault",
    "profiles",
    "hooks",
    "pastes",
    "pending",
    "pending_messages",
    "state",
    "telemetry",
    "terminal-sessions",
    "source-checks",
    "image_cache",
    "audio_cache",
    "images",
    "serve_restart_pending",
]

# Optionale Verzeichnisse
OPTIONAL_DIRS = [
    "backups",
    "logs",
    "verification_evidence.db",
]

# Immer ignorierte Dateien
IGNORE_PATTERNS = [
    "*.lock",
    "*.pid",
    "*.tmp",
    "*.log",
    "state.db.retired-wal-*",
    "*.bak*",
    "gateway.lock",
    "gateway.pid",
    "gateway.sock",
    "gateway_state_wdoaa3i6.tmp",
    ".hermes-update-in-progress",
    ".hermes-update-in-progress.lock",
    ".hermes-update-paused-gateways.lock",
    ".env.lock",
    ".install_id.lock",
    ".backup.lock",
    ".npm_lock_hash_*",
]

ARCHIVE_PREFIX = "hermes-migration-"


def fmt_size(nbytes):
    """Schöne Größendarstellung."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if nbytes < 1024:
            return "{:.1f} {}".format(nbytes, unit)
        nbytes /= 1024
    return "{:.1f} PB".format(nbytes)


def bold(text):
    return "\033[1m{}\033[0m".format(text)


def cyan(text):
    return "\033[96m{}\033[0m".format(text)


def green(text):
    return "\033[92m{}\033[0m".format(text)


def yellow(text):
    return "\033[93m{}\033[0m".format(text)


def red(text):
    return "\033[91m{}\033[0m".format(text)


def dim(text):
    return "\033[2m{}\033[0m".format(text)


def section(title):
    print("\n" + bold("=" * 60))
    print("  {}".format(title))
    print("=" * 60)


def print_progress(current, total, label=""):
    """Fortschrittsbalken."""
    if total > 0:
        pct = current / total * 100
        bar_len = 30
        filled = int(bar_len * current / total)
    else:
        pct = 100
        bar_len = 30
        filled = bar_len

    bar = "#" * filled + "-" * (bar_len - filled)
    line = "\r  [{}] {:5.1f}%  {}  ({}/{})".format(bar, pct, label, current, total)
    sys.stdout.write(line)
    sys.stdout.flush()
    if current == total:
        sys.stdout.write("\n")


def collect_hermes_info():
    """Sammelt Informationen über die aktuelle Hermes-Installation."""
    info = {
        "hermes_home": str(HERMES_HOME),
        "timestamp": datetime.datetime.now().isoformat(),
    }

    if not HERMES_HOME.exists():
        return {"error": str(HERMES_HOME) + " existiert nicht!"}

    # Gesamtgröße
    total_size = 0
    file_count = 0
    dir_count = 0
    for item in HERMES_HOME.rglob("*"):
        if item.is_file():
            total_size += item.stat().st_size
            file_count += 1
        elif item.is_dir():
            dir_count += 1
    info["total_size_bytes"] = total_size
    info["total_size"] = fmt_size(total_size)
    info["file_count"] = file_count
    info["dir_count"] = dir_count

    # Kritische Dateien prüfen
    critical_found = []
    critical_missing = []
    for item in CRITICAL_ITEMS:
        matches = list(HERMES_HOME.glob(item))
        if matches:
            for m in matches:
                if m.is_file():
                    critical_found.append("{} ({})".format(m.name, fmt_size(m.stat().st_size)))
        else:
            critical_missing.append(item)

    for d in CRITICAL_DIRS:
        dir_path = HERMES_HOME / d
        if dir_path.exists():
            if dir_path.is_dir():
                size = 0
                count = 0
                for f in dir_path.rglob("*"):
                    if f.is_file():
                        size += f.stat().st_size
                        count += 1
                critical_found.append("{}/ ({} Dateien, {})".format(d, count, fmt_size(size)))
            else:
                critical_found.append("{} ({})".format(d, fmt_size(dir_path.stat().st_size)))
        else:
            critical_missing.append(d)

    info["critical_found"] = critical_found
    info["critical_missing"] = critical_missing

    # Install ID
    install_id_file = HERMES_HOME / "install_id"
    if install_id_file.exists():
        info["install_id"] = install_id_file.read_text().strip()

    # Config
    config_file = HERMES_HOME / "config.yaml"
    info["config_exists"] = config_file.exists()
    if config_file.exists():
        info["config_size"] = fmt_size(config_file.stat().st_size)

    # State DB
    state_db = HERMES_HOME / "state.db"
    info["state_db_size"] = fmt_size(state_db.stat().st_size) if state_db.exists() else "nicht vorhanden"

    # Skills
    skills_dir = HERMES_HOME / "skills"
    if skills_dir.exists():
        skill_count = len([s for s in skills_dir.iterdir() if s.is_dir()])
        info["skill_count"] = skill_count

    # Kraken
    kraken_files = list(HERMES_HOME.glob("kraken_*_state.json"))
    info["kraken_trading_active"] = len(kraken_files) > 0
    info["kraken_pairs"] = [f.name.replace("_grid_state.json", "") for f in kraken_files]

    return info


def cmd_info(args):
    """Zeigt Informationen über die aktuelle Installation."""
    info = collect_hermes_info()

    section("Hermes Migration - Installationsinformationen")

    if "error" in info:
        print("\n  {}" .format(red("Fehler: " + info["error"])))
        return 1

    print("\n  Hermes Home:        {}" .format(cyan(info["hermes_home"])))
    print("  Gesamtgröße:        {}" .format(cyan(info["total_size"])))
    print("  Dateien:            {}" .format(info["file_count"]))
    print("  Verzeichnisse:      {}" .format(info["dir_count"]))
    print("  Install ID:         {}" .format(info.get("install_id", "N/A")))

    print("\n  Konfiguration:")
    config_str = "yes" if info.get("config_exists") else "no"
    print("    config.yaml:      {} {}" .format(config_str, info.get("config_size", "N/A")))
    print("    state.db:         {}" .format(info.get("state_db_size", "N/A")))
    print("    Skills:           {} Verzeichnisse" .format(info.get("skill_count", 0)))
    print("    Trading aktiv:    {} {}" .format(
        "yes" if info.get("kraken_trading_active") else "no",
        info.get("kraken_pairs", [])
    ))

    missing = info.get("critical_missing", [])
    if missing:
        print("\n  {}" .format(yellow("Warnung: Folgende kritische Dateien fehlen:")))
        for m in missing:
            print("    - {}".format(m))

    print("\n  {}" .format(dim('Verwenden Sie "backup" um eine Migration zu erstellen,')))
    print("  {}" .format(dim('"restore" um eine Migration wiederherzustellen.')))
    return 0


def stop_hermes_gateway():
    """Hermes Gateway stoppen für konsistente Backup-Daten."""
    section("Hermes Gateway")

    gateway_pid = HERMES_HOME / "gateway.pid"
    if gateway_pid.exists():
        try:
            pid = int(gateway_pid.read_text().strip())
            print("\n  Gateway PID: {}".format(pid))

            print("  Versuche Gateway graceful zu stoppen...")
            result = subprocess.run(
                ["kill", "-TERM", str(pid)],
                capture_output=True, text=True
            )

            for i in range(10):
                time.sleep(0.5)
                try:
                    os.kill(pid, 0)
                except OSError:
                    print("  {}" .format(green("Gateway gestoppt")))
                    return True

            print("  Gateway reagierte nicht - forcieren...")
            subprocess.run(["kill", "-9", str(pid)], capture_output=True)
            time.sleep(1)
            print("  {}" .format(green("Gateway forcirt gestoppt")))
            return True
        except (ValueError, FileNotFoundError) as e:
            print("  {}" .format(yellow("Konnte Gateway nicht stoppen: " + str(e))))
    else:
        print("  Kein Gateway-PID-File gefunden - nicht aktiv.")
    return False


def flush_sqlite_db(db_path):
    """SQLite WAL-Dateien flushen für konsistente Daten."""
    if db_path.exists():
        try:
            conn = sqlite3.connect(str(db_path))
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
            conn.close()
            return True
        except Exception as e:
            print("  {}" .format(yellow("SQLite Flush failed for {}: {}".format(db_path.name, e))))
    return False


def cmd_backup(args):
    """Erstellt ein Migration-Archiv."""
    section("Hermes Migration - Backup")

    if not HERMES_HOME.exists():
        print("\n  {}" .format(red("Fehler: " + str(HERMES_HOME) + " existiert nicht!")))
        return 1

    # Ziel bestimmen
    output = args.output or None
    if not output:
        timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        install_id = "unknown"
        id_file = HERMES_HOME / "install_id"
        if id_file.exists():
            install_id = id_file.read_text().strip()[:8]
        output = "{}-{}".format(ARCHIVE_PREFIX, timestamp) + "-" + install_id + ".tar.gz"

    output_path = Path(output)
    if output_path.exists():
        print("\n  {}".format(yellow("Warnung: Datei existiert bereits:")))
        response = input("  Überschreiben? [j/N]: ").strip().lower()
        if response not in ("j", "yes"):
            print("  Abgebrochen.")
            return 0

    # Gateway stoppen
    print("\n  Status: Hermes Gateway wird gestoppt...")
    gateway_stopped = stop_hermes_gateway()

    # SQLite flushen
    print("\n  Status: SQLite-Datenbanken synchronisieren...")
    sqlite_files = ["state.db", "kanban.db", "projects.db", "shared-state.db"]
    for db in sqlite_files:
        db_path = HERMES_HOME / db
        if db_path.exists():
            flush_sqlite_db(db_path)

    # Archiv erstellen
    print("\n  Status: Erstelle Archiv: {}".format(output_path))

    start_time = time.time()
    total_items = len(CRITICAL_ITEMS) + len(CRITICAL_DIRS)
    if args.include_optional:
        total_items += len(OPTIONAL_DIRS)
    current_item = 0

    try:
        with tarfile.open(str(output_path), "w:gz", compresslevel=6) as tar:
            # Kritische Dateien
            for item in CRITICAL_ITEMS:
                for match in HERMES_HOME.glob(item):
                    if match.is_file():
                        arcname = match.name
                        tar.add(str(match), arcname=arcname)
                        current_item += 1
                        print_progress(current_item, total_items, "Kritisch: " + arcname)

            # Kritische Verzeichnisse
            for d in CRITICAL_DIRS:
                dir_path = HERMES_HOME / d
                if dir_path.exists() and dir_path.is_dir():
                    tar.add(str(dir_path), arcname=d)
                    dir_files = sum(1 for _ in dir_path.rglob("*") if _.is_file())
                    current_item += 1
                    print_progress(current_item, total_items, "{}/ ({} Dateien)".format(d, dir_files))

            # Optionale Verzeichnisse
            if args.include_optional:
                for d in OPTIONAL_DIRS:
                    dir_path = HERMES_HOME / d
                    if dir_path.exists() and dir_path.is_dir():
                        tar.add(str(dir_path), arcname=d)
                        current_item += 1
                        print_progress(current_item, total_items, "{}/ (optional)".format(d))

        elapsed = time.time() - start_time
        archive_size = output_path.stat().st_size

        section("Backup abgeschlossen")

        if gateway_stopped:
            print("\n  {}" .format(yellow("Gateway wurde für das Backup gestoppt!")))
            print("  Starten Sie es nach der Migration mit:")
            print("    hermes gateway start")

        print("\n  {}" .format(green("Backup erfolgreich erstellt!")))
        print("\n  Datei:    {}" .format(cyan(str(output_path))))
        print("  Größe:    {}" .format(cyan(fmt_size(archive_size))))
        print("  Dauer:    {:.1f}s".format(elapsed))

        if args.clipboard:
            try:
                subprocess.run(["xclip", "-selection", "clipboard"],
                             input=str(output_path).encode(), check=True)
                print("  {}" .format(green("Pfad in Zwischenablage kopiert!")))
            except (subprocess.CalledProcessError, FileNotFoundError):
                try:
                    subprocess.run(["xsel", "--clipboard", "--input"],
                                 input=str(output_path).encode(), check=True)
                    print("  {}" .format(green("Pfad in Zwischenablage kopiert!")))
                except Exception:
                    print("\n  {}" .format(yellow("Kein Clipboard-Werkzeug gefunden.")))
                    print("  Manuell kopieren: {}".format(output_path))

        return 0

    except Exception as e:
        print("\n  {}" .format(red("Fehler beim Erstellen: " + str(e))))
        return 1


def get_existing_trading_state(hermes_home):
    """Prueft, ob auf dem Zielserver bereits Trading-State existiert."""
    state = {}
    for f in hermes_home.glob("kraken_*_state.json"):
        if f.name not in ("spawn-ledger.json",):
            state[f.name] = f.stat().st_size
    return state


def cmd_restore(args):
    """Stellt Hermes aus einem Migration-Archiv wieder her."""
    archive_path = Path(args.archive)

    section("Hermes Migration - Restore")

    if not archive_path.exists():
        print("\n  {}" .format(red("Fehler: Archiv nicht gefunden: " + str(archive_path))))
        return 1

    print("\n  Archiv: {}" .format(cyan(str(archive_path))))
    print("  Groesse: {}" .format(cyan(fmt_size(archive_path.stat().st_size))))

    if not HERMES_HOME.parent.exists():
        print("\n  {}" .format(red("Fehler: Parent exists not: " + str(HERMES_HOME.parent))))
        return 1

    # Vorhandene Daten prüfen
    gateway_stopped = False
    if HERMES_HOME.exists():
        existing = get_existing_trading_state(HERMES_HOME)
        if existing:
            print("\n  {}" .format(yellow("Vorhandene Trading-State-Dateien:")))
            for name, size in existing.items():
                print("    - {} ({})" .format(name, fmt_size(size)))
            print()
            response = input("  Vorhandene Daten BEWAHREN (m) oder ueberschreiben (j)? [m/j]: ").strip().lower()
            preserve = response in ("m", "me", "preserve")
        else:
            response = input("  Vorhandene Daten ueberschreiben? [J/n]: ").strip().lower()
            preserve = response not in ("n", "no", "nein")

        if not preserve and response not in ("j", "yes", "ja"):
            print("  Abgebrochen.")
            return 0

        gateway_stopped = stop_hermes_gateway()
    else:
        print("\n  Erstelle Zielverzeichnis: {}".format(HERMES_HOME))
        HERMES_HOME.mkdir(parents=True, exist_ok=True)

    # Vor dem Restore: Sicherungsverzeichnis
    backup_dir = None
    if preserve and HERMES_HOME.exists():
        backup_dir = HERMES_HOME / ".curator_backups" / "pre-migration-{}".format(
            datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
        print("\n  Status: Sichere vorhandene Dateien...")
        backup_dir.mkdir(parents=True, exist_ok=True)

        for item in CRITICAL_ITEMS:
            for match in HERMES_HOME.glob(item):
                if match.is_file():
                    dest = backup_dir / match.name
                    if not dest.exists():
                        shutil.copy2(str(match), str(dest))

        for d in CRITICAL_DIRS:
            dir_path = HERMES_HOME / d
            if dir_path.exists():
                dest = backup_dir / d
                if not dest.exists():
                    shutil.copytree(str(dir_path), str(dest), dirs_exist_ok=True)

    # Archiv entpacken
    print("\n  Status: Entpacke Archiv...")
    start_time = time.time()

    try:
        with tarfile.open(str(archive_path), "r:gz") as tar:
            members = tar.getmembers()
            total = len(members)

            for i, member in enumerate(members):
                tar.extract(member, str(HERMES_HOME))
                print_progress(i + 1, total, member.name)

        elapsed = time.time() - start_time

        # Berechtigungen
        print("\n  Status: Setze Berechtigungen...")
        for item in HERMES_HOME.iterdir():
            if item.is_dir():
                os.chmod(str(item), 0o700)
            else:
                os.chmod(str(item), 0o600)

        section("Restore abgeschlossen")

        install_id_file = HERMES_HOME / "install_id"
        if install_id_file.exists():
            new_id = install_id_file.read_text().strip()
            print("\n  Install ID: {}" .format(cyan(new_id)))
            print("  Identitaet der Installation wurde beibehalten.")

        if backup_dir:
            print("\n  {}" .format(green("Vorhandene Dateien gesichert in:")))
            print("    {}".format(backup_dir))

        if gateway_stopped:
            print("\n  {}" .format(yellow("Gateway wurde vor dem Restore gestoppt!")))
            print("  Starten Sie es mit: hermes gateway start")

        print("\n  {}" .format(green("Restore erfolgreich abgeschlossen!")))
        print("  Dauer: {:.1f}s".format(elapsed))
        print("\n  Naechste Schritte:")
        print("    1. Starten Sie Hermes: {}".format(cyan("hermes gateway start")))
        print("    2. Konfiguration pruefen: {}".format(cyan("hermes doctor")))
        print("    3. Trading-Grids testen")

        return 0

    except Exception as e:
        print("\n  {}" .format(red("Fehler beim Restore: " + str(e))))
        return 1


def cmd_verify(args):
    """Prueft ein Migration-Archiv auf Vollstaendigkeit."""
    archive_path = Path(args.archive)

    section("Hermes Migration - Archiv pruefen")

    if not archive_path.exists():
        print("\n  {}" .format(red("Fehler: Archiv nicht gefunden: " + str(archive_path))))
        return 1

    print("\n  Archiv: {}" .format(cyan(str(archive_path))))
    print("  Groesse: {}" .format(cyan(fmt_size(archive_path.stat().st_size))))

    try:
        with tarfile.open(str(archive_path), "r:gz") as tar:
            members = tar.getnames()
            archived_files = set()
            archived_dirs = set()

            for m in members:
                if m.endswith("/"):
                    archived_dirs.add(m)
                else:
                    archived_files.add(m)

            total_size = sum(m.size for m in tar.getmembers())

        section("Ergebnis")

        critical_ok = []
        critical_missing = []
        for item in CRITICAL_ITEMS:
            matched = [f for f in archived_files if fnmatch.fnmatch(f, item)]
            if matched:
                critical_ok.extend(matched)
            else:
                critical_missing.append(item)

        dirs_ok = []
        dirs_missing = []
        for d in CRITICAL_DIRS:
            if "{}/".format(d) in archived_dirs or d in archived_dirs:
                dirs_ok.append(d)
            else:
                dirs_missing.append(d)

        all_ok = not critical_missing and not dirs_missing

        print("\n  Kritische Dateien:  {} {} gefunden, {} fehlen" .format(
            green("OK") if not critical_missing else yellow("FEHLER"),
            len(critical_ok), len(critical_missing)))
        print("  Kritische Verzeichnisse: {} {} gefunden, {} fehlen" .format(
            green("OK") if not dirs_missing else yellow("FEHLER"),
            len(dirs_ok), len(dirs_missing)))
        print("  Dateien im Archiv:  {}" .format(len(archived_files)))
        print("  Gesamtgroesse:      {}" .format(fmt_size(total_size)))

        if critical_missing:
            print("\n  {}" .format(yellow("Fehlende kritische Dateien:")))
            for m in critical_missing:
                print("    - {}".format(m))

        if dirs_missing:
            print("\n  {}" .format(yellow("Fehlende kritische Verzeichnisse:")))
            for m in dirs_missing:
                print("    - {}/".format(m))

        if all_ok:
            print("\n  {}" .format(green("Archiv ist vollstaendig und einsatzbereit!")))
        else:
            print("\n  {}" .format(yellow("Archiv ist unvollstaendig.")))

        return 0 if all_ok else 1

    except Exception as e:
        print("\n  {}" .format(red("Fehler beim Pruefen: " + str(e))))
        return 1


def cmd_list_components(args):
    """Listet alle zu migrierenden Komponenten auf."""
    section("Hermes Migration - Zu migrierende Komponenten")

    if not HERMES_HOME.exists():
        print("\n  {}" .format(red("Fehler: " + str(HERMES_HOME) + " existiert nicht!")))
        return 1

    print("\n  {}" .format(bold("Kritische Dateien (MUESSEN migriert werden):")))
    for item in CRITICAL_ITEMS:
        matches = list(HERMES_HOME.glob(item))
        if matches:
            sizes = []
            for m in matches:
                if m.is_file():
                    sizes.append(fmt_size(m.stat().st_size))
            print("    {} {} ({})" .format(green("OK"), item, ", ".join(sizes)))
        else:
            print("    {} {} (nicht vorhanden)" .format(yellow("NEIN"), item))

    print("\n  {}" .format(bold("Kritische Verzeichnisse (MUESSEN migriert werden):")))
    for d in CRITICAL_DIRS:
        dir_path = HERMES_HOME / d
        if dir_path.exists() and dir_path.is_dir():
            size = sum(f.stat().st_size for f in dir_path.rglob("*") if f.is_file())
            count = sum(1 for _ in dir_path.rglob("*") if _.is_file())
            print("    {} {}/ ({} Dateien, {})" .format(green("OK"), d, count, fmt_size(size)))
        elif dir_path.exists():
            print("    {} {} ({})" .format(green("OK"), d, fmt_size(dir_path.stat().st_size)))
        else:
            print("    {} {}/ (nicht vorhanden)" .format(yellow("NEIN"), d))

    print("\n  {}" .format(bold("Optionale Verzeichnisse (KOENNEN migriert werden):")))
    for d in OPTIONAL_DIRS:
        dir_path = HERMES_HOME / d
        if dir_path.exists() and dir_path.is_dir():
            size = sum(f.stat().st_size for f in dir_path.rglob("*") if f.is_file())
            count = sum(1 for _ in dir_path.rglob("*") if _.is_file())
            print("    {} {}/ ({} Dateien, {})" .format(yellow("OPTIONAL"), d, count, fmt_size(size)))
        else:
            print("    {} {}/ (nicht vorhanden)" .format(yellow("NEIN"), d))

    print("\n  {}" .format(dim("Hinweis: Lock-, PID-Dateien und Logs werden automatisch ausgeschlossen.")))
    print("  {}" .format(dim("Cache-Verzeichnisse werden nicht mit migriert.")))

    return 0


def main():
    parser = argparse.ArgumentParser(
        prog="hermes-migrate",
        description="Hermes Agent Migration Tool - Vollstaendige Migration auf einen neuen Server",
        epilog="Beispiel:\n"
               "  hermes-migrate backup\n"
               "  hermes-migrate backup -o /tmp/hermes.tar.gz\n"
               "  hermes-migrate restore hermes-migration-*.tar.gz\n"
               "  hermes-migrate verify hermes-migration-*.tar.gz\n"
               "  hermes-migrate info\n"
               "  hermes-migrate list\n",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    subparsers = parser.add_subparsers(dest="command", help="Verfuegbarer Befehl")

    # backup
    backup_parser = subparsers.add_parser("backup", help="Migration-Archiv erstellen")
    backup_parser.add_argument("-o", "--output", help="Ausgabepfad (default: hermes-migration-<timestamp>.tar.gz)")
    backup_parser.add_argument("--include-optional", action="store_true",
                              help="Optionale Daten einbezuehen (backups, logs)")
    backup_parser.add_argument("--clipboard", action="store_true",
                              help="Pfad nach dem Backup in die Zwischenablage kopieren")

    # restore
    restore_parser = subparsers.add_parser("restore", help="Migration-Archiv wiederherstellen")
    restore_parser.add_argument("archive", help="Pfad zum Migration-Archiv")

    # verify
    verify_parser = subparsers.add_parser("verify", help="Migration-Archiv pruefen")
    verify_parser.add_argument("archive", help="Pfad zum Migration-Archiv")

    # info
    subparsers.add_parser("info", help="Installationsinformationen anzeigen")

    # list
    subparsers.add_parser("list", help="Zu migrierende Komponenten auflisten")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 0

    commands = {
        "backup": cmd_backup,
        "restore": cmd_restore,
        "verify": cmd_verify,
        "info": cmd_info,
        "list": cmd_list_components,
    }

    handler = commands.get(args.command)
    if handler:
        return handler(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
