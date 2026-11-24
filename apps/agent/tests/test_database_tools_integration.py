import hashlib
import json
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

APP_ROOT = Path(__file__).resolve().parents[1]
DB_ROOT = APP_ROOT / "db" / "main"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_manifest_checksums_match_delivered_files() -> None:
    manifest = json.loads((DB_ROOT / "sql" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["owner"] == "agent"
    assert manifest["databaseKey"] == "main"
    assert manifest["from"] == "empty"
    assert manifest["to"] == "0002_model_runtime"
    bootstrap = DB_ROOT / manifest["bootstrap"]
    incremental = DB_ROOT / manifest["incremental"]
    assert hashlib.sha256(bootstrap.read_bytes()).hexdigest() == manifest["bootstrapSha256"]
    assert hashlib.sha256(incremental.read_bytes()).hexdigest() == manifest["incrementalSha256"]
    for source in manifest["sources"]:
        assert sha256(APP_ROOT / source["source"]) == source["sha256"]
    assert "BEGIN;" in bootstrap.read_text(encoding="utf-8")
    assert "model_provider" in bootstrap.read_text(encoding="utf-8")
    assert "model_call" in incremental.read_text(encoding="utf-8")


def test_migration_chain_is_linear_and_matches_manifest() -> None:
    config = Config(str(APP_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(APP_ROOT / "migrations"))
    script = ScriptDirectory.from_config(config)
    heads = script.get_heads()
    assert heads == ["0002"]
    revisions = [revision.revision for revision in script.walk_revisions()]
    assert revisions == ["0002", "0001"]
    assert script.get_revision("0002").down_revision == "0001"


def test_generation_entry_is_registered() -> None:
    package = json.loads((APP_ROOT / "package.json").read_text(encoding="utf-8"))
    assert package["scripts"]["db:sql"] == "node scripts/db-sql.mjs"
    assert package["scripts"]["db:sql:check"] == "node scripts/db-sql.mjs --check"
    assert (APP_ROOT / "scripts" / "db-sql.mjs").exists()
