"""
Comprehensive Unit & Integration Tests for Storage, SQLite WAL, Backups, and Safe A/B Updates.
Validates:
1. SQLite configuration: busy_timeout=5000, synchronous=NORMAL in WAL mode.
2. Nightly VACUUM INTO atomic database backup, 7-day rotation pruning, and rclone push.
3. Safe update command: SHA-256 validation, path traversal prevention, A/B slot toggle,
   health check verification, and automatic rollback on failure.
"""

import asyncio
import hashlib
import io
import os
from pathlib import Path
import sqlite3
import tarfile
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from app.config import system_config
from app.core.state_manager import StateManager
from app.cli import backup_database, switch_symlink, update_app


@pytest_asyncio.fixture
async def temp_storage_env(tmp_path: Path):
    """Sets up an isolated database and backup directory environment."""
    db_file = tmp_path / "system.db"
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)

    old_db_path = system_config.database.path
    old_backup_dir = system_config.database.backup_dir
    old_retention = system_config.database.backup_retention_days
    old_busy_timeout = system_config.database.busy_timeout_ms
    old_rclone = system_config.database.rclone_remote

    system_config.database.path = str(db_file)
    system_config.database.backup_dir = str(backup_dir)
    system_config.database.backup_retention_days = 7
    system_config.database.busy_timeout_ms = 5000
    system_config.database.rclone_remote = None

    mgr = StateManager(db_path=db_file)
    await mgr.init_db()

    yield mgr, db_file, backup_dir

    system_config.database.path = old_db_path
    system_config.database.backup_dir = old_backup_dir
    system_config.database.backup_retention_days = old_retention
    system_config.database.busy_timeout_ms = old_busy_timeout
    system_config.database.rclone_remote = old_rclone


# -----------------------------------------------------------------------------
# 1. SQLite Pragmas & WAL Configuration Tests
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_sqlite_pragmas(temp_storage_env):
    """Verifies that SQLite connections enforce busy_timeout, WAL mode, and synchronous=NORMAL."""
    mgr, db_file, _ = temp_storage_env

    with mgr._get_connection() as conn:
        # Journal mode
        cur = conn.execute("PRAGMA journal_mode;")
        journal_mode = cur.fetchone()[0]
        assert journal_mode.upper() == "WAL"

        # Synchronous (1 = NORMAL, 2 = FULL)
        cur = conn.execute("PRAGMA synchronous;")
        sync_mode = cur.fetchone()[0]
        assert sync_mode == 1  # 1 corresponds to NORMAL in SQLite

        # Busy timeout (5000 ms)
        cur = conn.execute("PRAGMA busy_timeout;")
        busy_timeout = cur.fetchone()[0]
        assert busy_timeout == 5000

        # Temp store (2 = MEMORY)
        cur = conn.execute("PRAGMA temp_store;")
        temp_store = cur.fetchone()[0]
        assert temp_store == 2


# -----------------------------------------------------------------------------
# 2. Nightly Atomic VACUUM INTO Backup & Rotation Tests
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_nightly_backup_creation_and_content(temp_storage_env):
    """Verifies that run_nightly_backup creates a valid SQLite backup via VACUUM INTO."""
    mgr, db_file, backup_dir = temp_storage_env

    # Seed data
    await mgr.log_event("test_backup", "INFO", "Pre-backup record", {"run": 1})

    backup_path = await mgr.run_nightly_backup()
    assert backup_path is not None
    assert backup_path.exists()
    assert backup_path.parent == backup_dir
    assert backup_path.name.startswith("smart_water_backup_")
    assert backup_path.name.endswith(".db")

    # Validate backup is a valid SQLite file with identical event data
    with sqlite3.connect(str(backup_path)) as conn:
        cur = conn.execute("SELECT message FROM events WHERE event_type = 'test_backup';")
        row = cur.fetchone()
        assert row is not None
        assert row[0] == "Pre-backup record"


@pytest.mark.asyncio
async def test_backup_retention_pruning(temp_storage_env):
    """Verifies that backups older than retention window are pruned, keeping at least 1."""
    mgr, _, backup_dir = temp_storage_env

    # Create dummy old backups
    old_bkp1 = backup_dir / "smart_water_backup_20260101_000000.db"
    old_bkp2 = backup_dir / "smart_water_backup_20260102_000000.db"
    old_bkp1.write_bytes(b"SQLite format 3\x00dummy1")
    old_bkp2.write_bytes(b"SQLite format 3\x00dummy2")

    # Set old mtime (e.g. 15 days ago)
    old_time = time.time() - (15 * 86400)
    os.utime(old_bkp1, (old_time, old_time))
    os.utime(old_bkp2, (old_time, old_time))

    # Run backup with 7-day retention
    new_bkp = await mgr.run_nightly_backup(keep_days=7)
    assert new_bkp is not None

    # The 15-day-old backups should be pruned
    assert not old_bkp1.exists()
    assert not old_bkp2.exists()
    assert new_bkp.exists()


@pytest.mark.asyncio
async def test_backup_with_rclone_push(temp_storage_env):
    """Verifies that optional rclone push is triggered when rclone_remote is configured."""
    mgr, _, _ = temp_storage_env
    system_config.database.rclone_remote = "gdrive:smart-water-backups"

    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.communicate = AsyncMock(return_value=(b"", b""))

    with patch("shutil.which", return_value="/usr/bin/rclone"), \
         patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
        backup_path = await mgr.run_nightly_backup()
        assert backup_path is not None
        mock_exec.assert_called_once()
        args = mock_exec.call_args[0]
        assert args[0] == "/usr/bin/rclone"
        assert args[1] == "copy"
        assert args[2] == str(backup_path)
        assert args[3] == "gdrive:smart-water-backups"


# -----------------------------------------------------------------------------
# 3. Safe A/B Updates, Checksum Verification & Rollback Tests
# -----------------------------------------------------------------------------

def _create_test_tarball(tar_path: Path, files: dict[str, str]) -> str:
    """Helper to create a tar.gz archive and return its SHA-256."""
    with tarfile.open(tar_path, "w:gz") as tar:
        for fname, content in files.items():
            data = content.encode("utf-8")
            ti = tarfile.TarInfo(name=fname)
            ti.size = len(data)
            ti.mtime = int(time.time())
            tar.addfile(ti, io.BytesIO(data))

    hasher = hashlib.sha256()
    with open(tar_path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def test_update_app_sha256_mismatch(tmp_path: Path):
    """Verifies update immediately aborts on SHA-256 checksum mismatch."""
    tar_path = tmp_path / "update.tar.gz"
    _create_test_tarball(tar_path, {"run.py": "# test"})

    success = update_app(
        tarball_path_str=str(tar_path),
        expected_sha256="0000000000000000000000000000000000000000000000000000000000000000",
        install_dir_str=str(tmp_path / "install"),
        slots_dir_str=str(tmp_path),
        skip_health_check=True,
    )
    assert success is False


def test_update_app_path_traversal_rejection(tmp_path: Path):
    """Verifies malicious tarball with path traversal is rejected."""
    tar_path = tmp_path / "malicious.tar.gz"
    # Create tar with directory escape
    with tarfile.open(tar_path, "w:gz") as tar:
        ti = tarfile.TarInfo(name="../../etc/shadow")
        data = b"evil"
        ti.size = len(data)
        tar.addfile(ti, io.BytesIO(data))

    hasher = hashlib.sha256(tar_path.read_bytes())
    success = update_app(
        tarball_path_str=str(tar_path),
        expected_sha256=hasher.hexdigest(),
        install_dir_str=str(tmp_path / "install"),
        slots_dir_str=str(tmp_path),
        skip_health_check=True,
    )
    assert success is False


def test_update_app_ab_toggle_success(tmp_path: Path):
    """Verifies clean A/B slot toggle and health check success."""
    slots_dir = tmp_path / "slots"
    slots_dir.mkdir(parents=True, exist_ok=True)
    install_link = slots_dir / "smart-water"
    slot_a = slots_dir / "smart-water-a"
    slot_b = slots_dir / "smart-water-b"

    # Seed initial slot A
    slot_a.mkdir()
    (slot_a / "version.txt").write_text("v1.0.0")
    assert switch_symlink(install_link, slot_a) is True

    assert install_link.resolve() == slot_a.resolve()

    # Package v1.2.0 tarball
    tar_path = tmp_path / "release-1.2.0.tar.gz"
    sha = _create_test_tarball(tar_path, {
        "version.txt": "v1.2.0",
        "run.py": "print('v1.2.0')",
    })

    # Mock health check and systemctl
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.__enter__.return_value.status = 200

    with patch("urllib.request.urlopen", return_value=mock_resp), \
         patch("shutil.which", return_value=None):  # No systemctl in unit test
        success = update_app(
            tarball_path_str=str(tar_path),
            expected_sha256=sha,
            install_dir_str=str(install_link),
            slots_dir_str=str(slots_dir),
            timeout_s=2.0,
        )

    assert success is True
    # Should now point to Slot B
    assert install_link.resolve() == slot_b.resolve()
    assert (slot_b / "version.txt").read_text() == "v1.2.0"


def test_update_app_automatic_rollback_on_health_failure(tmp_path: Path):
    """Verifies automatic rollback when post-restart health check fails."""
    slots_dir = tmp_path / "slots"
    slots_dir.mkdir(parents=True, exist_ok=True)
    install_link = slots_dir / "smart-water"
    slot_a = slots_dir / "smart-water-a"

    # Seed initial slot A
    slot_a.mkdir()
    (slot_a / "version.txt").write_text("v1.0.0")
    assert switch_symlink(install_link, slot_a) is True

    # Package broken release tarball
    tar_path = tmp_path / "broken.tar.gz"
    sha = _create_test_tarball(tar_path, {"version.txt": "vbroken"})

    # Health check raises exception (connection refused / timeout)
    with patch("urllib.request.urlopen", side_effect=OSError("Connection Refused")), \
         patch("shutil.which", return_value=None):
        success = update_app(
            tarball_path_str=str(tar_path),
            expected_sha256=sha,
            install_dir_str=str(install_link),
            slots_dir_str=str(slots_dir),
            timeout_s=1.0,
        )

    assert success is False
    # Verified automatic rollback back to Slot A!
    assert install_link.resolve() == slot_a.resolve()
    assert (install_link / "version.txt").read_text() == "v1.0.0"


@pytest.mark.asyncio
async def test_cli_backup_command(temp_storage_env):
    """Verifies the backup_database CLI wrapper executes and reports success."""
    mgr, _, _ = temp_storage_env
    res = await asyncio.to_thread(backup_database)
    assert res is True
