"""Destination-bound, atomic scheduler idempotency state."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import tempfile

from .provider import ProviderConfigurationError, RacetimeProvider


class StateStoreError(ValueError):
    """Persistent scheduler state is unsafe, corrupt, or belongs elsewhere."""


LEAGUE_ENTRY_KINDS = {
    "league_created_races", "league_sent_webhooks",
    "league_scheduling_threads", "league_results",
}
# The tournament's own kinds.
#
# Separate files rather than shared ones, and separate validation rather than
# looser validation. A tournament key is not a League key and not a timestamp:
# it names a *match*, because a match is the one thing about a race that does
# not move. Keying by the start time is what gave a postponed League race a
# second room, and the tournament does not get to repeat that.
AUTUMN_ENTRY_KINDS = {
    "autumn_bindings", "autumn_created_races",
    "autumn_sent_webhooks", "autumn_mirrored_times", "autumn_booth_notices", "autumn_results",
}
CREATED_ENTRY_KINDS = {
    "created_races", "league_created_races", "autumn_created_races",
}
ENTRY_KINDS = (
    {"created_races", "sent_webhooks"} | LEAGUE_ENTRY_KINDS | AUTUMN_ENTRY_KINDS
)

#: Kinds whose keys carry no timestamp, so `cleanup_before` must not touch them.
#: A binding outlives every reschedule and every restart: forgetting one is how
#: a grand-final row gets reassigned to the reset after the final is played.
TIMELESS_ENTRY_KINDS = {"autumn_bindings", "autumn_created_races",
                        "autumn_sent_webhooks", "autumn_mirrored_times", "autumn_booth_notices", "autumn_results"}

LEAGUE_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

#: A competition edition, so one relay can hold two tournaments at once and
#: neither can read the other's bindings.
AUTUMN_EVENT = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

#: A match in a double-elimination bracket, as the engine names them: W1-1 and
#: L3-2 for the two sides, GF-1 for the grand final and GF-2 for its reset.
AUTUMN_MATCH = re.compile(r"^(?:[WL][0-9]{1,3}-[0-9]{1,4}|GF-[12])$")

#: A row of the Schedule tab: the two racers flattened, and the time they
#: agreed. Not an identity -- it is how a *row* is recognised again next tick.
AUTUMN_ROW = re.compile(r"^[a-z0-9]+-vs-[a-z0-9]+$")

UNCERTAIN_RACE = "__uncertain_room_creation__"
STATE_FIELDS = {"schema_version", "destination_key", "entries"}
MAX_STATE_BYTES = 4 * 1024 * 1024
MAX_LEAGUE_KEY_LENGTH = 200
MAX_KEY_LENGTH = 100
MAX_AUTUMN_KEY_LENGTH = 300


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
        # Where it is recorded that this store's state was lost. Beside the state
        # file rather than inside it, because the state file is the thing that
        # could not be read.
        self._unrecovered_marker = self.path.with_name(
            "{}.unrecovered".format(self.path.name)
        )

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

    def _key_timestamp(self, value):
        """Return the ISO timestamp portion of a state key."""
        if self.entry_kind in LEAGUE_ENTRY_KINDS:
            return value.partition("|")[0]
        return value

    @property
    def prunes_by_time(self):
        """Whether `cleanup_before` means anything for this kind.

        It does not for the tournament. Those keys name a match rather than a
        moment, and there is no cutoff after which a binding stops being true --
        a match scheduled for tonight, postponed twice and raced next week is one
        binding the whole way through.
        """
        return self.entry_kind not in TIMELESS_ENTRY_KINDS

    def _validate_autumn_key(self, value):
        """`<event>|<match>`, or for a binding `<event>|<pair>|<time>`.

        Explicit rather than permissive. The store's other kinds are validated
        down to the shape of a slug, and a tournament key that is merely "a
        string with a pipe in it" would be the one place a typo reaches disk --
        where it becomes a room nobody can account for.
        """
        if not isinstance(value, str) or len(value) > MAX_AUTUMN_KEY_LENGTH:
            raise StateStoreError("state entry key is invalid")

        # Split on the exact number of parts the kind has, so a key with a
        # section missing is reported as that rather than as whatever the
        # remaining sections then fail to be. `bogie-vs-merks|<time>` is a
        # binding key that forgot its competition, and saying "must name two
        # racers" about it sends whoever reads the log the wrong way.
        parts = value.split("|")
        if self.entry_kind == "autumn_results":
            if (len(parts) != 3 or not AUTUMN_EVENT.fullmatch(parts[0])
                    or not AUTUMN_MATCH.fullmatch(parts[1])
                    or not re.fullmatch(r"[1-9][0-9]{0,2}", parts[2])):
                raise StateStoreError("autumn result key must be <edition>|<match>|<game>")
            return
        if self.entry_kind != 'autumn_bindings' and len(parts) == 3:
            if (not AUTUMN_EVENT.fullmatch(parts[0]) or not AUTUMN_MATCH.fullmatch(parts[1])
                    or not re.fullmatch(r'[1-7]', parts[2])):
                raise StateStoreError('game state key must be <competition-edition>|<match>|<game>')
            return
        wanted = 3 if self.entry_kind == "autumn_bindings" else 2
        if len(parts) != wanted:
            raise StateStoreError(
                "autumn state entry key must be {}, not {!r}".format(
                    "<competition>|<racers>|<time>" if wanted == 3
                    else "<competition>|<match>",
                    value,
                )
            )

        if not AUTUMN_EVENT.fullmatch(parts[0]):
            raise StateStoreError("autumn state entry key must name a competition")

        if self.entry_kind == "autumn_bindings":
            if not AUTUMN_ROW.fullmatch(parts[1]):
                raise StateStoreError("autumn binding key must name two racers")
            try:
                parsed = datetime.fromisoformat(parts[2])
            except ValueError as exc:
                raise StateStoreError(
                    "autumn binding key must end in an ISO timestamp") from exc
            if parsed.tzinfo is None:
                raise StateStoreError("autumn binding key must include a timezone")
            return

        if not AUTUMN_MATCH.fullmatch(parts[1]):
            raise StateStoreError("autumn state entry key must name a match")

    def _validate_key(self, value):
        if self.entry_kind in AUTUMN_ENTRY_KINDS:
            self._validate_autumn_key(value)
            return
        league = self.entry_kind in LEAGUE_ENTRY_KINDS
        limit = MAX_LEAGUE_KEY_LENGTH if league else MAX_KEY_LENGTH
        if not isinstance(value, str) or len(value) > limit:
            raise StateStoreError("state entry key is invalid")
        if league:
            timestamp, separator, slug = value.partition("|")
            if not separator or not LEAGUE_SLUG.fullmatch(slug):
                raise StateStoreError("league state entry key is invalid")
        else:
            if "|" in value:
                raise StateStoreError("state entry key is invalid")
            timestamp = value
        try:
            parsed = datetime.fromisoformat(timestamp)
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
            if self.entry_kind == "autumn_results":
                if (not isinstance(value, dict)
                        or not {"room", "racers", "status", "winner", "reason"}.issubset(value)
                        or not set(value).issubset({"room", "racers", "status", "winner", "reason", "observations"})):
                    raise StateStoreError("autumn result receipt fields are invalid")
                racers = value["racers"]
                if (not isinstance(racers, dict) or len(racers) != 2
                        or not all(isinstance(item, str) and 0 < len(item) <= 120
                                   and not any(c in item for c in "\r\n\x00")
                                   for pair in racers.items() for item in pair)
                        or len(set(racers.values())) != 2):
                    raise StateStoreError("autumn result needs two distinct verified racers")
                if value["status"] not in {"tracking", "suggested", "review", "recorded"}:
                    raise StateStoreError("autumn result status is invalid")
                if ((value["status"] in {"suggested", "recorded"} and value["winner"] not in racers)
                        or (value["status"] in {"tracking", "review"} and value["winner"] is not None)
                        or (value["reason"] is not None and
                            (not isinstance(value["reason"], str) or len(value["reason"]) > 500))):
                    raise StateStoreError("autumn result outcome is invalid")
                try:
                    room = self.provider.resolve_location(value["room"])
                except (ProviderConfigurationError, TypeError) as exc:
                    raise StateStoreError("autumn result room is invalid") from exc
                if any(other["room"] == room for other in cleaned.values()):
                    raise StateStoreError("autumn result room is bound twice")
                observations = value.get('observations', {})
                if not isinstance(observations, dict) or len(observations) > 32:
                    raise StateStoreError('autumn observations are invalid')
                for oid, item in observations.items():
                    if not isinstance(item, dict) or set(item) != {'facts', 'receipt'}:
                        raise StateStoreError('autumn observation fields are invalid')
                    observation = item['facts']
                    fields = {"event", "edition", "matchId", "game", "room", "status", "entrants", "observationId"}
                    if (not isinstance(observation, dict) or set(observation) != fields
                            or observation["room"] != room or observation["status"] not in {"finished", "cancelled"}
                            or not isinstance(observation["entrants"], list) or len(observation["entrants"]) > 16
                            or key != '{}-{}|{}|{}'.format(observation['event'], observation['edition'], observation['matchId'], observation['game'])
                            or not isinstance(observation['observationId'], str) or len(observation['observationId']) != 64 or observation['observationId'] != oid):
                        raise StateStoreError("autumn observation identity is invalid")
                    for entrant in observation['entrants']:
                        if (not isinstance(entrant, dict) or set(entrant) != {'id', 'status', 'finishSeconds'}
                                or not isinstance(entrant['id'], str) or not 0 < len(entrant['id']) <= 120
                                or not isinstance(entrant['status'], str) or not 0 < len(entrant['status']) <= 40
                                or entrant['finishSeconds'] is not None and
                                (not isinstance(entrant['finishSeconds'], str) or len(entrant['finishSeconds']) > 40)):
                            raise StateStoreError("autumn observation entrant is invalid")
                    receipt = item['receipt']
                    if receipt is not None and (not isinstance(receipt, dict)
                            or set(receipt) != {'observationId', 'proposalId'}
                            or receipt['observationId'] != oid
                            or not isinstance(receipt['proposalId'], str) or not 0 < len(receipt['proposalId']) <= 100):
                        raise StateStoreError('autumn observation receipt is invalid')
                cleaned[key] = {**value, "room": room, "racers": dict(racers)}
                continue
            if self.entry_kind == "autumn_bindings":
                # The match a row was given. Validated to the same shape the key
                # of a created room is, so a binding cannot quietly point at
                # something the bracket has never heard of.
                if not isinstance(value, str) or not AUTUMN_MATCH.fullmatch(value):
                    raise StateStoreError("autumn binding value must name a match")
                cleaned[key] = value
                continue
            if self.entry_kind == "autumn_mirrored_times":
                # What we last told the engine, so a tick knows whether the
                # sheet has moved since.
                if not isinstance(value, str):
                    raise StateStoreError("autumn mirrored-time value is invalid")
                try:
                    parsed = datetime.fromisoformat(value)
                except ValueError as exc:
                    raise StateStoreError(
                        "autumn mirrored-time value must be an ISO timestamp") from exc
                if parsed.tzinfo is None:
                    raise StateStoreError(
                        "autumn mirrored-time value must include a timezone")
                cleaned[key] = value
                continue
            if self.entry_kind in CREATED_ENTRY_KINDS:
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
        # The mark goes down *before* the file moves, and that order is the whole
        # guarantee.
        #
        # Quarantining moves the file away, and `load` returns {} for a file that
        # is not there -- so the read that found the corruption raised, and every
        # read after a restart said "no state yet, carry on". For the tournament
        # that is the worst available answer: forgotten bindings let a grand-final
        # row be reassigned to the reset, and a forgotten room is a second room.
        #
        # Marking afterwards was not enough either. A failed marker write left the
        # corrupt file already moved and nothing recording that, so a restart was
        # back to returning {}. So: mark first, and if the mark cannot be written,
        # leave the corrupt file exactly where it is. A file that still fails to
        # parse is a worse diagnostic than a marker and a far better one than
        # silence, because every later load raises on it too.
        #
        # Only the tournament blocks on this. The League has behaved the
        # forgiving way in production for months and changing that is its own
        # decision, made deliberately rather than as a side effect of this.
        marked = False
        if self.entry_kind in AUTUMN_ENTRY_KINDS:
            try:
                self._unrecovered_marker.write_text(
                    "{} was quarantined as {} at {}\n"
                    "Autumn stays stopped until this file is removed. Restore the "
                    "state from a backup first if there is one: an empty file is a "
                    "valid recovery only if losing every binding is acceptable.\n"
                    .format(self.path.name, quarantine.name, _timestamp_suffix()),
                    encoding="utf-8",
                )
                marked = True
            except OSError as exc:
                raise StateStoreError(
                    "corrupt state could not be marked unrecovered, so it has been "
                    "left in place"
                ) from exc

        try:
            os.replace(self.path, quarantine)
            try:
                os.chmod(quarantine, 0o400)
            except OSError:
                pass
        except OSError as exc:
            if marked:
                # The mark now describes a move that never happened. Withdraw it
                # rather than point somebody at a filename that does not exist:
                # the corrupt file is still there, and still raises on every read.
                try:
                    self._unrecovered_marker.unlink()
                except OSError:
                    pass
            raise StateStoreError("corrupt state could not be quarantined") from exc

    def load(self):
        if self.entry_kind in AUTUMN_ENTRY_KINDS and self._unrecovered_marker.exists():
            raise StateStoreError(
                "autumn state was quarantined and not recovered; remove {} once it "
                "is restored".format(self._unrecovered_marker.name)
            )
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
        if self.entry_kind in AUTUMN_ENTRY_KINDS and self._unrecovered_marker.exists():
            raise StateStoreError(
                "autumn state was quarantined and not recovered; refusing to write "
                "over it"
            )
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
        if not self.prunes_by_time:
            # Nothing here has a timestamp to compare, and dropping a binding
            # because it is old is exactly the bug this kind exists to avoid.
            return entries
        retained = {
            key: value
            for key, value in entries.items()
            if datetime.fromisoformat(self._key_timestamp(key)) > cutoff
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
        if self.entry_kind in CREATED_ENTRY_KINDS:
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
