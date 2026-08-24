"""Destination-bound, atomic scheduler idempotency state."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import tempfile

from .provider import ProviderConfigurationError, RacetimeProvider


class StateStoreError(ValueError):
    """Persistent scheduler state is unsafe, corrupt, or belongs elsewhere."""


ENTRY_KINDS = {"created_races", "sent_webhooks"}
UNCERTAIN_RACE = "__uncertain_room_creation__"
STATE_FIELDS = {"schema_version", "destination_key", "entries"}
MAX_STATE_BYTES = 4 * 1024 * 1024


def _timestamp_suffix():
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


class DestinationStateStore:
    def __init__(self, path, destination_key, entry_kind, *, data_dir=None):
        if entry_kind not in ENTRY_KINDS:
            raise StateStoreError("state entry kind is invalid")
        if (
            not isinstance(destination_key, str)
            or "|" not in destination_key
            or len(destination_key) > 500
            or any(character in destination_key for character in "\r\n\x00")
        ):
            raise StateStoreError("destination key is invalid")
        origin, category = destination_key.rsplit("|", 1)
        try:
            provider = RacetimeProvider(
                origin,
                category,
                allow_insecure_loopback=origin.startswith("http://"),
            )
        except ProviderConfigurationError as exc:
            raise StateStoreError("destination key is invalid") from exc
        if provider.destination_key != destination_key:
            raise StateStoreError("destination key is not canonical")

        declared = Path(path)
        root = Path(data_dir) if data_dir is not None else declared.parent
        self.data_dir = root.resolve()
        target = declared if declared.is_absolute() else self.data_dir / declared
        resolved_parent = target.parent.resolve()
        try:
            resolved_parent.relative_to(self.data_dir)
        except ValueError as exc:
            raise StateStoreError("state path escapes TTPBOT_DATA_DIR") from exc
        self.path = resolved_parent / target.name
        self.destination_key = destination_key
        self.entry_kind = entry_kind
        self.provider = provider

    def _guard_path(self, path, *, may_be_missing=False):
        target = Path(path)
        if target.is_symlink():
            raise StateStoreError("state symlinks are forbidden")
        if not may_be_missing and not target.is_file():
            raise StateStoreError("state file is missing or unsafe")
        if target.exists() and not target.is_file():
            raise StateStoreError("state path is not a regular file")
        try:
            target.parent.resolve().relative_to(self.data_dir)
        except ValueError as exc:
            raise StateStoreError("state path escapes TTPBOT_DATA_DIR") from exc
        return target

    def _validate_key(self, value):
        if not isinstance(value, str) or len(value) > 100:
            raise StateStoreError("state entry key is invalid")
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as exc:
            raise StateStoreError("state entry key must be an ISO timestamp") from exc
        if parsed.tzinfo is None:
            raise StateStoreError("state entry key must include a timezone")

    def _validate_entries(self, entries):
        if not isinstance(entries, dict) or len(entries) > 10000:
            raise StateStoreError("state entries are invalid")
        cleaned = {}
        for key, value in entries.items():
            self._validate_key(key)
            if self.entry_kind == "created_races":
                if not isinstance(value, str) or not value:
                    raise StateStoreError("created-race state value is invalid")
                if value == UNCERTAIN_RACE:
                    cleaned[key] = value
                    continue
                try:
                    value = self.provider.resolve_location(value)
                except ProviderConfigurationError as exc:
                    raise StateStoreError("created-race URL belongs to another destination") from exc
            elif value is not True:
                raise StateStoreError("sent-webhook state value is invalid")
            cleaned[key] = value
        return cleaned

    def _quarantine_corrupt(self):
        quarantine = self.path.with_name(
            "{}.corrupt-{}.bak".format(self.path.name, _timestamp_suffix())
        )
        try:
            os.replace(self.path, quarantine)
            try:
                os.chmod(quarantine, 0o400)
            except OSError:
                pass
        except OSError as exc:
            raise StateStoreError("corrupt state could not be quarantined") from exc

    def load(self):
        if not self.path.exists():
            return {}
        self._guard_path(self.path)
        if self.path.stat().st_size > MAX_STATE_BYTES:
            raise StateStoreError("state file exceeds the size limit")
        try:
            document = json.loads(self.path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            self._quarantine_corrupt()
            raise StateStoreError("state JSON was corrupt and has been quarantined") from exc
        if not isinstance(document, dict) or set(document) != STATE_FIELDS:
            raise StateStoreError("state document fields are invalid")
        if document["schema_version"] != 2:
            raise StateStoreError("state schema is unsupported")
        if document["destination_key"] != self.destination_key:
            raise StateStoreError("state belongs to another destination")
        return self._validate_entries(document["entries"])

    def save(self, entries):
        cleaned = self._validate_entries(entries)
        if self.path.exists():
            self._guard_path(self.path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        document = {
            "schema_version": 2,
            "destination_key": self.destination_key,
            "entries": cleaned,
        }
        descriptor = None
        temporary = None
        try:
            descriptor, temporary = tempfile.mkstemp(
                prefix=".{}.".format(self.path.name),
                suffix=".tmp",
                dir=str(self.path.parent),
            )
            try:
                os.chmod(temporary, 0o600)
            except OSError:
                pass
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                descriptor = None
                json.dump(document, stream, indent=2, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            temporary = None
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass
            if os.name != "nt":
                directory = os.open(str(self.path.parent), os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
        except (OSError, TypeError, ValueError) as exc:
            raise StateStoreError("state could not be saved atomically") from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except FileNotFoundError:
                    pass
        return cleaned

    def cleanup_before(self, cutoff):
        if isinstance(cutoff, str):
            try:
                cutoff = datetime.fromisoformat(cutoff)
            except ValueError as exc:
                raise StateStoreError("cleanup cutoff is invalid") from exc
        if not isinstance(cutoff, datetime) or cutoff.tzinfo is None:
            raise StateStoreError("cleanup cutoff must be timezone-aware")
        entries = self.load()
        retained = {
            key: value
            for key, value in entries.items()
            if datetime.fromisoformat(key) > cutoff
        }
        if retained != entries:
            self.save(retained)
        return retained

    def migrate_legacy(self, legacy_path, asserted_destination_key):
        if asserted_destination_key != self.destination_key:
            raise StateStoreError("legacy migration destination assertion does not match")
        if self.path.exists():
            return self.load()
        legacy = Path(legacy_path)
        if not legacy.is_absolute():
            legacy = self.data_dir / legacy
        if legacy.is_symlink() or not legacy.is_file():
            raise StateStoreError("legacy state is missing or unsafe")
        try:
            legacy = legacy.resolve(strict=True)
        except OSError as exc:
            raise StateStoreError("legacy state is missing or unsafe") from exc
        if legacy.stat().st_size > MAX_STATE_BYTES:
            raise StateStoreError("legacy state exceeds the size limit")
        try:
            value = json.loads(legacy.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise StateStoreError("legacy state is corrupt") from exc
        if self.entry_kind == "created_races":
            if isinstance(value, list):
                entries = {key: self.provider.origin + "/{}/legacy-unknown".format(self.provider.category) for key in value}
            elif isinstance(value, dict):
                entries = value
            else:
                raise StateStoreError("legacy created-race state is invalid")
        else:
            if not isinstance(value, list):
                raise StateStoreError("legacy webhook state is invalid")
            entries = {key: True for key in value}
        entries = self._validate_entries(entries)
        backup = legacy.with_name(
            "{}.legacy-{}.bak".format(legacy.name, _timestamp_suffix())
        )
        try:
            shutil.copy2(legacy, backup)
            try:
                os.chmod(backup, 0o400)
            except OSError:
                pass
        except OSError as exc:
            raise StateStoreError("legacy state backup could not be created") from exc
        self.save(entries)
        return entries
