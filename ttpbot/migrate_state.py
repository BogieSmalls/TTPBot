"""Explicitly migrate legacy scheduler state into a destination-bound v2 set."""

import argparse
import os
from pathlib import Path
import sys

from .provider import ProviderConfigurationError, RacetimeProvider
from .state import DestinationStateStore, StateStoreError


class MigrationError(ValueError):
    """Legacy state cannot be migrated safely."""


def _directory(value, label, *, must_exist):
    path = Path(value)
    if path.is_symlink():
        raise MigrationError("{} is unsafe".format(label))
    if must_exist and not path.is_dir():
        raise MigrationError("{} is missing".format(label))
    if not must_exist:
        path.mkdir(parents=True, exist_ok=True)
        if path.is_symlink() or not path.is_dir():
            raise MigrationError("{} is unsafe".format(label))
    return path.resolve()


def migrate_legacy_state(legacy_dir, data_dir, *, origin, category):
    legacy_root = _directory(legacy_dir, "legacy directory", must_exist=True)
    target_root = _directory(data_dir, "data directory", must_exist=False)
    if legacy_root == target_root:
        raise MigrationError("legacy and target directories must be distinct")
    legacy_created = legacy_root / "created_races.json"
    legacy_sent = legacy_root / "sent_webhooks.json"
    for path in (legacy_created, legacy_sent):
        if path.is_symlink() or not path.is_file():
            raise MigrationError("both legacy state documents are required")
    try:
        provider = RacetimeProvider(
            origin,
            category,
            allow_insecure_loopback=str(origin).startswith("http://"),
        )
        created = DestinationStateStore(
            target_root / "created_races.json",
            provider.destination_key,
            "created_races",
            data_dir=target_root,
        )
        sent = DestinationStateStore(
            target_root / "sent_webhooks.json",
            provider.destination_key,
            "sent_webhooks",
            data_dir=target_root,
        )
        created_entries = created.migrate_legacy(
            legacy_created, provider.destination_key
        )
        sent_entries = sent.migrate_legacy(
            legacy_sent, provider.destination_key
        )
    except (ProviderConfigurationError, StateStoreError, OSError) as exc:
        raise MigrationError("legacy state migration failed safely") from exc
    return {
        "created_races": len(created_entries),
        "sent_webhooks": len(sent_entries),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--legacy-dir", required=True)
    parser.add_argument("--data-dir", default=os.environ.get("TTPBOT_DATA_DIR"))
    parser.add_argument("--origin", required=True)
    parser.add_argument("--category", required=True)
    arguments = parser.parse_args(argv)
    if not arguments.data_dir:
        parser.error("--data-dir or TTPBOT_DATA_DIR is required")
    try:
        result = migrate_legacy_state(
            arguments.legacy_dir,
            arguments.data_dir,
            origin=arguments.origin,
            category=arguments.category,
        )
        destination = RacetimeProvider(
            arguments.origin,
            arguments.category,
            allow_insecure_loopback=arguments.origin.startswith("http://"),
        ).destination_key
    except (MigrationError, ProviderConfigurationError):
        sys.stderr.write("TTPBOT_STATE_MIGRATION=FAIL\n")
        return 1
    print(
        "TTPBOT_STATE_MIGRATION=PASS destination={} created_races={} sent_webhooks={}".format(
            destination, result["created_races"], result["sent_webhooks"]
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
