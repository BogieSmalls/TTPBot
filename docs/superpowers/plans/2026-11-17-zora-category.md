# ZORA category — implementation plan

**Goal:** Serve the RaceTime.gg `zora` category from a second TTPBot service, with TTPBot's commands and a ZORA flag string beside the Z1R one, while the z1r category behaves exactly as it does today.

**Architecture:** One code base, two services. A process whose category is `zora` runs in zora mode: it joins every open room (unlisted ones too), reads ZORA presets from zora.gg, and runs nothing else. In a zora room, the existing `!flags`, `!race`, `!cancel`, `!summary` and `!help` take a zora branch; every other command is silent.

**Spec:** `docs/superpowers/specs/2026-11-17-zora-category-design.md`

## Global constraints

- **z1r is untouched.** No change to any reply, race info, room policy, state file, Discord post, configuration or `--check-config` output in the z1r category. Every new branch is entered only in a zora room or a zora process.
- **No new handler command.** No new `ex_` method on `TTPRaceHandler`; `tests/fixtures/flagsets-contract.json` and its `reservedCommands` stay as copied from Z1RR.Data.
- **Council's contract is unchanged.** `validate_snapshot(snapshot)` keeps its behaviour; the ZORA rules live behind `zora=True` with their own fixture.
- `deploy/ttpbot.env.example` and `deploy/ttpbot.service` are unchanged; `tests/test_deploy_contract.py` pins the former.
- Tests are `unittest` classes in `tests/`, run with `uv run pytest`, in the house style of `tests/test_handler_commands.py` and `tests/test_rehearsal_mode.py`.

---

### Task 1: ZORA seed text and built-in presets

**Files:**
- Create: `ttpbot/zora.py`
- Modify: `ttpbot/config.py` (add `ZORA_CATEGORY_SLUG`, `ZORA_PRESETS`, `ZORA_PRESET_LIST`)
- Modify: `ttpbot/room_policy.py` (add `is_zora_room`)
- Test: `tests/test_zora.py`

`ttpbot/zora.py` holds the network-free pieces: `SEED_MAX`, `is_zora_category(slug)`, `zora_flags(text)` (a ZORA flag string with `_` read as `!`, or `None`), `seed_link(seed, flags, zora)`, `race_info(seed, flags, zora)`, and `startup_problems(environ, config)`.

`ZORA_PRESETS` maps a key to `(z1r, zora)`: ZoraBot's `presets.json`, the Zora The Explorer weeks and zora.gg's built-ins.

### Task 2: ZORA presets through the flag registry

**Files:**
- Modify: `ttpbot/flagset_registry.py`
- Create: `tests/fixtures/zora-presets-contract.json`
- Test: `tests/test_zora_presets.py`

- `validate_snapshot(snapshot, zora=False)`: with `zora=True`, a string entry may carry `zora` (absent, `""` or a ZORA flag string), and uniqueness is on `(flags, zora)`.
- `FlagsetRegistry(..., zora_strings=None, setting='Z1RR_FLAGSETS_URL', label='Flag registry')`. With `zora_strings` it is a ZORA registry: `resolve_pair(word) -> (flags, zora)`. The defaults keep the module's `registry` and its log lines exactly as before.
- Module-level `zora_registry`, read from `ZORA_PRESETS_URL` and cached at `ZORA_PRESETS_CACHE` or `$TTPBOT_DATA_DIR/zora_presets.json`.

### Task 3: Roll ZORA seeds in zora rooms

**Files:**
- Modify: `ttpbot/handler.py`
- Test: `tests/test_zora_room.py`

- `begin()` sets `self.zora_room`.
- `chat_message`: in a zora room, `!flags`, `!race`, `!cancel`, `!summary` and `!help` dispatch, a ZORA preset shortcut rolls, and anything else is silent.
- Each of the five commands branches to a `_zora_*` method when `self.zora_room`.
- `chat_history`: a zora room reads the roll with `roll_state` (last bot word wins) and welcomes with `ZORA_WELCOME_MESSAGE`.

### Task 4: Zora mode in the bot

**Files:**
- Modify: `ttpbot/bot.py`, `ttpbot/__init__.py`
- Test: `tests/test_zora_mode.py`

- `should_handle`: a zora process handles every room the library would.
- `run()`: a zora process runs `_run_zora()`: `zora_registry.run()` and `join_category_rooms()`, which lists `/o/<category>/data` with the bot token (the public list if that fails) and joins rooms as rehearsal mode does.
- `main()`: refuse to start on `zora.startup_problems`.

### Task 5: z1r regression

**Files:**
- Test: `tests/test_zora_z1r_unchanged.py`

Drive a z1r room through `!flags`, `!race`, `!cancel`, `!summary`, `!help`, a registry shortcut and history replay, with a ZORA feed loaded whose shortcuts and strings overlap Council's, and check the replies and race info against today's. Check the z1r bot's `should_handle` and `run()` are unchanged.

### Task 6: Deploy and docs

**Files:**
- Create: `deploy/ttpbot-zora.service`, `deploy/ttpbot-zora.env.example`
- Modify: `docs/oci-service-runbook.md`, `README.md`
- Test: `tests/test_zora_deploy_contract.py`
