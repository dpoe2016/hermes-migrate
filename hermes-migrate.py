#!/usr/bin/env python3
"""
Hermes Migration Tool - Full migration of Hermes Agent to a new server.

Usage:
  hermes-migrate backup          - Creates a migration archive of the current Hermes installation
  hermes-migrate restore <file>  - Restores Hermes from a migration archive
  hermes-migrate info            - Shows information about the current installation
  hermes-migrate verify <file>   - Checks a migration archive for completeness
  hermes-migrate list            - Shows all components to be migrated
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

# Files that MUST be included
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

# Directories that MUST be included (user data, not platform binaries!)
# tools/, platforms/, node/, bin/, hermes-agent/ are NOT included –
# they will be automatically reinstalled on the target machine.
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

            # Optional directories
OPTIONAL_DIRS = [
    "backups",
    "logs",
    "verification_evidence.db",
]

# Files always ignored
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
    """Nice size representation."""
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
    """Progress bar."""
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
    """Collects information about the current Hermes installation."""
    info = {
        "hermes_home": str(HERMES_HOME),
        "timestamp": datetime.datetime.now().isoformat(),
    }

    if not HERMES_HOME.exists():
        return {"error": str(HERMES_HOME) + " does not exist!"}

    # Total size
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

    # Check critical files
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
                critical_found.append("{}/ ({} files, {})".format(d, count, fmt_size(size)))
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
    info["state_db_size"] = fmt_size(state_db.stat().st_size) if state_db.exists() else "not present"

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
    """Shows information about the current installation."""
    info = collect_hermes_info()

    section("Hermes Migration - Installation information")

    if "error" in info:
        print("\n  {}" .format(red("Error: " + info["error"])))
        return 1

    print("\n  Hermes Home:        {}" .format(cyan(info["hermes_home"])))
    print("  Total size:         {}" .format(cyan(info["total_size"])))
    print("  Files:              {}" .format(info["file_count"]))
    print("  Directories:        {}" .format(info["dir_count"]))
    print("  Install ID:         {}" .format(info.get("install_id", "N/A")))

    print("\n  Configuration:")
    config_str = "yes" if info.get("config_exists") else "no"
    print("    config.yaml:      {} {}" .format(config_str, info.get("config_size", "N/A")))
    print("    state.db:         {}" .format(info.get("state_db_size", "N/A")))
    print("    Skills:           {} directories" .format(info.get("skill_count", 0)))
    print("    Trading active:   {} {}" .format(
        "yes" if info.get("kraken_trading_active") else "no",
        info.get("kraken_pairs", [])
    ))

    missing = info.get("critical_missing", [])
    if missing:
        print("\n  {}" .format(yellow("Warning: The following critical files are missing:")))
        for m in missing:
            print("    - {}".format(m))

    print("\n  {}" .format(dim('Use "backup" to create a migration,')))
    print("  {}" .format(dim('"restore" to restore a migration.')))
    return 0


def stop_hermes_gateway():
    """Stop Hermes gateway for consistent backup data."""
    section("Hermes Gateway")

    gateway_pid = HERMES_HOME / "gateway.pid"
    if gateway_pid.exists():
        try:
            pid = int(gateway_pid.read_text().strip())
            print("\n  Gateway PID: {}".format(pid))

            print("  Attempting to gracefully stop gateway...")
            result = subprocess.run(
                ["kill", "-TERM", str(pid)],
                capture_output=True, text=True
            )

            for i in range(10):
                time.sleep(0.5)
                try:
                    os.kill(pid, 0)
                except OSError:
                    print("  {}" .format(green("Gateway stopped")))
                    return True

            print("  Gateway did not respond - forcing...")
            subprocess.run(["kill", "-9", str(pid)], capture_output=True)
            time.sleep(1)
            print("  {}" .format(green("Gateway force stopped")))
            return True
        except (ValueError, FileNotFoundError) as e:
            print("  {}" .format(yellow("Could not stop gateway: " + str(e))))
    else:
        print("  No gateway PID file found - not active.")
    return False


def flush_sqlite_db(db_path):
    """Flush SQLite WAL files for consistent data."""
    if db_path.exists():
        try:
            conn = sqlite3.connect(str(db_path))
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
            conn.close()
            return True
        except Exception as e:
            print("  {}" .format(yellow("SQLite flush failed for {}: {}".format(db_path.name, e))))
    return False


def cmd_backup(args):
    """Creates a migration archive."""
    section("Hermes Migration - Backup")

    if not HERMES_HOME.exists():
        print("\n  {}" .format(red("Error: " + str(HERMES_HOME) + " does not exist!")))
        return 1

    # Determine output
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
        print("\n  {}".format(yellow("Warning: File already exists:")))
        response = input("  Overwrite? [y/N]: ").strip().lower()
        if response not in ("j", "yes"):
            print("  Aborted.\n")
            return 0

    # Stop Hermes gateway
    print("\n  Status: Stopping Hermes gateway...")
    gateway_stopped = stop_hermes_gateway()

    # Flush SQLite
    print("\n  Status: Synchronizing SQLite databases...")
    sqlite_files = ["state.db", "kanban.db", "projects.db", "shared-state.db"]
    for db in sqlite_files:
        db_path = HERMES_HOME / db
        if db_path.exists():
            flush_sqlite_db(db_path)

    # Create archive
    print("\n  Status: Creating archive: {}".format(output_path))

    start_time = time.time()
    total_items = len(CRITICAL_ITEMS) + len(CRITICAL_DIRS)
    if args.include_optional:
        total_items += len(OPTIONAL_DIRS)
    current_item = 0

    try:
        with tarfile.open(str(output_path), "w:gz", compresslevel=6) as tar:
            # Critical files
            for item in CRITICAL_ITEMS:
                for match in HERMES_HOME.glob(item):
                    if match.is_file():
                        arcname = match.name
                        tar.add(str(match), arcname=arcname)
                        current_item += 1
                        print_progress(current_item, total_items, "Critical: " + arcname)

            # Critical directories
            for d in CRITICAL_DIRS:
                dir_path = HERMES_HOME / d
                if dir_path.exists() and dir_path.is_dir():
                    tar.add(str(dir_path), arcname=d)
                    dir_files = sum(1 for _ in dir_path.rglob("*") if _.is_file())
                    current_item += 1
                    print_progress(current_item, total_items, "{}/ ({} files)".format(d, dir_files))

# Optional directories
            if args.include_optional:
                for d in OPTIONAL_DIRS:
                    dir_path = HERMES_HOME / d
                    if dir_path.exists() and dir_path.is_dir():
                        tar.add(str(dir_path), arcname=d)
                        current_item += 1
                        print_progress(current_item, total_items, "{}/ (optional)".format(d))

        elapsed = time.time() - start_time
        archive_size = output_path.stat().st_size

        section("Backup complete")

        if gateway_stopped:
            print("\n  {}" .format(yellow("Gateway was stopped for the backup!")))
            print("  Start it after migration with:")
            print("    hermes gateway start")

        print("\n  {}" .format(green("Backup created successfully!")))
        print("\n  File:     {}" .format(cyan(str(output_path))))
        print("  Size:     {}" .format(cyan(fmt_size(archive_size))))
        print("  Duration: {:.1f}s".format(elapsed))

        if args.clipboard:
            try:
                subprocess.run(["xclip", "-selection", "clipboard"],
                             input=str(output_path).encode(), check=True)
                print("  {}" .format(green("Path copied to clipboard!")))
            except (subprocess.CalledProcessError, FileNotFoundError):
                try:
                    subprocess.run(["xsel", "--clipboard", "--input"],
                                 input=str(output_path).encode(), check=True)
                    print("  {}" .format(green("Path copied to clipboard!")))
                except Exception:
                    print("\n  {}" .format(yellow("No clipboard tool found.")))
                    print("  Copy manually: {}".format(output_path))

        return 0

    except Exception as e:
        print("\n  {}" .format(red("Error creating: " + str(e))))
        return 1


def get_existing_trading_state(hermes_home):
    """Checks if trading state already exists on the target server."""
    state = {}
    for f in hermes_home.glob("kraken_*_state.json"):
        if f.name not in ("spawn-ledger.json",):
            state[f.name] = f.stat().st_size
    return state


def cmd_restore(args):
    """Restores Hermes from a migration archive."""
    archive_path = Path(args.archive)

    section("Hermes Migration - Restore")

    if not archive_path.exists():
        print("\n  {}" .format(red("Error: Archive not found: " + str(archive_path))))
        return 1

    print("\n  Archive: {}" .format(cyan(str(archive_path))))
    print("  Size: {}" .format(cyan(fmt_size(archive_path.stat().st_size))))

    if not HERMES_HOME.parent.exists():
        print("\n  {}" .format(red("Error: Parent does not exist: " + str(HERMES_HOME.parent))))
        return 1

    # Check existing data
    gateway_stopped = False
    if HERMES_HOME.exists():
        existing = get_existing_trading_state(HERMES_HOME)
        if existing:
            print("\n  {}" .format(yellow("Existing trading state files:")))
            for name, size in existing.items():
                print("    - {} ({})" .format(name, fmt_size(size)))
            print()
            response = input("  Preserve existing data (p) or overwrite (y)? [p/y]: ").strip().lower()
            preserve = response in ("p", "preserve")
        else:
            response = input("  Overwrite existing data? [Y/n]: ").strip().lower()
            preserve = response not in ("n", "no")

        if not preserve and response not in ("y", "yes"):
            print("  Aborted.\n")
            return 0

        gateway_stopped = stop_hermes_gateway()
    else:
        print("\n  Creating target directory: {}".format(HERMES_HOME))
        HERMES_HOME.mkdir(parents=True, exist_ok=True)

    # Pre-restore: backup directory
    backup_dir = None
    if preserve and HERMES_HOME.exists():
        backup_dir = HERMES_HOME / ".curator_backups" / "pre-migration-{}".format(
            datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
        print("\n  Status: Backing up existing files...")
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

    # Extract archive
    print("\n  Status: Extracting archive...")
    start_time = time.time()

    try:
        with tarfile.open(str(archive_path), "r:gz") as tar:
            members = tar.getmembers()
            total = len(members)

            for i, member in enumerate(members):
                tar.extract(member, str(HERMES_HOME))
                print_progress(i + 1, total, member.name)

        elapsed = time.time() - start_time

        # Set permissions
        print("\n  Status: Setting permissions...")
        for item in HERMES_HOME.iterdir():
            if item.is_dir():
                os.chmod(str(item), 0o700)
            else:
                os.chmod(str(item), 0o600)

        section("Restore complete")

        install_id_file = HERMES_HOME / "install_id"
        if install_id_file.exists():
            new_id = install_id_file.read_text().strip()
            print("\n  Install ID: {}" .format(cyan(new_id)))
            print("  Installation identity has been preserved.")

        if backup_dir:
            print("\n  {}" .format(green("Existing files backed up to:")))
            print("    {}".format(backup_dir))

        if gateway_stopped:
            print("\n  {}" .format(yellow("Gateway was stopped before restore!")))
            print("  Start it with: hermes gateway start")

        print("\n  {}" .format(green("Restore completed successfully!")))
        print("  Duration: {:.1f}s".format(elapsed))
        print("\n  Next steps:")
        print("    1. Start Hermes: {}".format(cyan("hermes gateway start")))
        print("    2. Check configuration: {}".format(cyan("hermes doctor")))
        print("    3. Test trading grids")

        return 0

    except Exception as e:
        print("\n  {}" .format(red("Error during restore: " + str(e))))
        return 1


def cmd_verify(args):
    """Checks a migration archive for completeness."""
    archive_path = Path(args.archive)

    section("Hermes Migration - Verify archive")

    if not archive_path.exists():
        print("\n  {}" .format(red("Error: Archive not found: " + str(archive_path))))
        return 1

    print("\n  Archive: {}" .format(cyan(str(archive_path))))
    print("  Size: {}" .format(cyan(fmt_size(archive_path.stat().st_size))))

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

        section("Result")

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

        print("\n  Critical files:       {} {} found, {} missing" .format(
            green("OK") if not critical_missing else yellow("ERROR"),
            len(critical_ok), len(critical_missing)))
        print("  Critical directories: {} {} found, {} missing" .format(
            green("OK") if not dirs_missing else yellow("ERROR"),
            len(dirs_ok), len(dirs_missing)))
        print("  Files in archive:     {}" .format(len(archived_files)))
        print("  Total size:           {}" .format(fmt_size(total_size)))

        if critical_missing:
            print("\n  {}" .format(yellow("Missing critical files:")))
            for m in critical_missing:
                print("    - {}".format(m))

        if dirs_missing:
            print("\n  {}" .format(yellow("Missing critical directories:")))
            for m in dirs_missing:
                print("    - {}/".format(m))

        if all_ok:
            print("\n  {}" .format(green("Archive is complete and ready to use!")))
        else:
            print("\n  {}" .format(yellow("Archive is incomplete.")))

        return 0 if all_ok else 1

    except Exception as e:
        print("\n  {}" .format(red("Error checking: " + str(e))))
        return 1


def cmd_list_components(args):
    """Lists all components to be migrated."""
    section("Hermes Migration - Components to migrate")

    if not HERMES_HOME.exists():
        print("\n  {}" .format(red("Error: " + str(HERMES_HOME) + " does not exist!")))
        return 1

    print("\n  {}" .format(bold("Critical files (MUST be migrated):")))
    for item in CRITICAL_ITEMS:
        matches = list(HERMES_HOME.glob(item))
        if matches:
            sizes = []
            for m in matches:
                if m.is_file():
                    sizes.append(fmt_size(m.stat().st_size))
            print("    {} {} ({})" .format(green("OK"), item, ", ".join(sizes)))
        else:
            print("    {} {} (not present)" .format(yellow("NO"), item))

    print("\n  {}" .format(bold("Critical directories (MUST be migrated):")))
    for d in CRITICAL_DIRS:
        dir_path = HERMES_HOME / d
        if dir_path.exists() and dir_path.is_dir():
            size = sum(f.stat().st_size for f in dir_path.rglob("*") if f.is_file())
            count = sum(1 for _ in dir_path.rglob("*") if _.is_file())
            print("    {} {}/ ({} files, {})" .format(green("OK"), d, count, fmt_size(size)))
        elif dir_path.exists():
            print("    {} {} ({})" .format(green("OK"), d, fmt_size(dir_path.stat().st_size)))
        else:
            print("    {} {}/ (not present)" .format(yellow("NO"), d))

    print("\n  {}" .format(bold("Optional directories (CAN be migrated):")))
    for d in OPTIONAL_DIRS:
        dir_path = HERMES_HOME / d
        if dir_path.exists() and dir_path.is_dir():
            size = sum(f.stat().st_size for f in dir_path.rglob("*") if f.is_file())
            count = sum(1 for _ in dir_path.rglob("*") if _.is_file())
            print("    {} {}/ ({} files, {})" .format(yellow("OPTIONAL"), d, count, fmt_size(size)))
        else:
            print("    {} {}/ (not present)" .format(yellow("NO"), d))

    print("\n  {}" .format(dim("Note: Lock files, PID files, and logs are automatically excluded.")))
    print("  {}" .format(dim("Cache directories are not migrated.")))

    return 0


def main():
    parser = argparse.ArgumentParser(
        prog="hermes-migrate",
        description="Hermes Agent Migration Tool - Full migration to a new server",
        epilog="Example:\n"
               "  hermes-migrate backup\n"
               "  hermes-migrate backup -o /tmp/hermes.tar.gz\n"
               "  hermes-migrate restore hermes-migration-*.tar.gz\n"
               "  hermes-migrate verify hermes-migration-*.tar.gz\n"
               "  hermes-migrate info\n"
               "  hermes-migrate list\n",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # backup
    backup_parser = subparsers.add_parser("backup", help="Create migration archive")
    backup_parser.add_argument("-o", "--output", help="Output path (default: hermes-migration-<timestamp>.tar.gz)")
    backup_parser.add_argument("--include-optional", action="store_true",
                               help="Include optional data (backups, logs)")
    backup_parser.add_argument("--clipboard", action="store_true",
                               help="Copy path to clipboard after backup")

    # restore
    restore_parser = subparsers.add_parser("restore", help="Restore migration archive")
    restore_parser.add_argument("archive", help="Path to migration archive")

    # verify
    verify_parser = subparsers.add_parser("verify", help="Check migration archive")
    verify_parser.add_argument("archive", help="Path to migration archive")

    # info
    subparsers.add_parser("info", help="Show installation information")

    # list
    subparsers.add_parser("list", help="List components to be migrated")

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
