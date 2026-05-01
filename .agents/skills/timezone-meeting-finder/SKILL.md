---
name: timezone-meeting-finder
description: Finds meeting times for participants spread across multiple IANA timezones (with correct DST handling). Three modes — `overlap` finds windows where everyone is in their normal working hours; `squeeze` suggests "least bad" slots when no strict overlap exists, allowing each person to flex N minutes earlier or later and ranking candidates by who pays what cost; `convert` converts a proposed datetime into each participant's local clock and flags anyone outside their working hours. Use when scheduling meetings across timezones, planning international standups, finding a "squeeze corner" for a critical call when no clean overlap exists, or sanity-checking a proposed time. Inputs are a list of participants (each with name, IANA timezone, and working hours) plus either a date range or a proposed datetime. Output is a ranked list of candidate windows with per-participant local times and disruption metrics, or a per-participant local-time table with working-hours flags.
---

# timezone-meeting-finder

A reusable skill for the small, error-prone slice of meeting scheduling that pure prose models get wrong: timezone arithmetic across DST transitions, intersecting working windows for several people, and verifying a proposed time is inside everyone's working hours.

The model orchestrates (collects participants, parses the user's request, picks reasonable defaults, presents results), and the Python script does the actual time math.

## When to use this skill

Activate this skill when the user is doing any of these:

- "When can we meet?" / "Find a time that works for X, Y, Z" across cities or timezones.
- Setting up an international standup, all-hands, or interview panel.
- Asking whether a specific proposed time is reasonable for everyone.
- Converting a single time (e.g. "3pm Pacific") into every teammate's local clock.
- Comparing windows over a date range, especially across DST transitions.

## When NOT to use this skill

- Booking, sending, or modifying calendar events. This skill only computes times; it does not touch a calendar API.
- Single-timezone scheduling (just one participant or one city). Plain prose can do that fine.
- Recurring-event RRULE expansion or complex iCal logic. Use a calendar library for that.
- Finding rooms, resources, or other non-time constraints.

## Inputs

The skill accepts participants in two interchangeable forms; the model should pick whichever is easiest given how the user phrased the request.

### Compact CLI form (best for short, ad-hoc requests)

Repeat `--participant` once per person, in this exact format:

```
"Name|IANA_TZ|HH:MM-HH:MM[|Mon,Tue,Wed,Thu,Fri]"
```

- `Name` — free text, no `|` allowed.
- `IANA_TZ` — e.g. `America/New_York`, `Europe/London`, `Asia/Kolkata`. See `references/iana_quickref.md` for common city → IANA mappings.
- `HH:MM-HH:MM` — local working hours, 24-hour. Start must be strictly before end (overnight shifts not supported).
- Optional days — comma-separated subset of `Mon,Tue,Wed,Thu,Fri,Sat,Sun`. Defaults to Mon–Fri.

### JSON form (best for many participants or reusable groups)

Save a file (e.g. `team.json`) and pass `--input team.json`:

```json
{
  "participants": [
    {"name": "Alice", "tz": "America/New_York", "work_start": "09:00", "work_end": "17:00"},
    {"name": "Bob",   "tz": "Europe/London",   "work_start": "09:00", "work_end": "17:00"},
    {"name": "Carol", "tz": "Asia/Tokyo",      "work_start": "10:00", "work_end": "18:00",
                      "work_days": ["Mon","Tue","Wed","Thu","Fri"]}
  ]
}
```

JSON and `--participant` flags can be mixed; later entries with the same name override earlier ones.

## How to invoke

Run the script with one of three subcommands. Always use the project's Python interpreter; the script depends only on the standard library (Python 3.9+).

### `overlap` — find common working windows across a date range

```
python scripts/tz_meeting.py overlap \
    --participant "Alice|America/New_York|09:00-17:00" \
    --participant "Bob|Europe/London|09:00-17:00" \
    --start 2026-05-04 --end 2026-05-08 \
    --duration 30 \
    --anchor America/New_York
```

Required: `--start`, `--end`. Optional: `--duration` (minutes, default 30), `--anchor` (IANA TZ used to interpret dates and label the report; default UTC), `--json`.

### `squeeze` — suggest "least bad" slots when no strict overlap exists

Use this when `overlap` returns nothing, or when the user describes the meeting as critical and asks how to make it work despite the timezone spread.

```
python scripts/tz_meeting.py squeeze \
    --participant "Pat|America/Phoenix|09:00-17:00" \
    --participant "Cara|Australia/Sydney|09:00-17:00" \
    --participant "Liam|Europe/London|09:00-17:00" \
    --start 2026-05-04 --end 2026-05-08 \
    --duration 30 --flex 240 \
    --anchor America/Phoenix
```

`--flex N` lets each participant flex up to N minutes earlier than their `work_start` and N minutes later than their `work_end`. Use `--flex-before` and `--flex-after` for asymmetric flex (e.g. "early starts are fine, late nights are not"). Optional: `--max-results` (top N suggestions, default 5), `--step` (slot-search granularity in minutes, default 15).

Each candidate is ranked by: fewest participants forced onto an off-day → smallest worst-individual disruption → smallest team-total disruption. The "disruption" for one participant on a candidate slot is `early_required + late_required` minutes, where `early_required` is how many minutes before *their own* configured `work_start` the meeting begins (in their local time) and `late_required` is how many minutes after *their own* `work_end` it ends. A 30-minute meeting at 05:00 when that participant's `work_start` is 09:00 yields `early_required = 240` minutes — they are being asked to start four hours early, even though only thirty minutes of meeting time falls outside their hours.

The disruption is always relative to that specific participant's configured hours — there is no universal "normal." When the agent surfaces results to the user, it must (a) name the participant's local time and IANA zone (e.g. "05:15 Tue Australia/Sydney"), and (b) anchor the comparison to that participant's own `work_start` / `work_end` (e.g. "begins 3h45m before their 09:00 start"). Never say "before normal" — different people have different normals.

### `convert` — show one time in everyone's local clock

```
python scripts/tz_meeting.py convert \
    --when "2026-05-04T10:30" --when-tz America/New_York \
    --participant "Alice|America/New_York|09:00-17:00" \
    --participant "Bob|Europe/London|09:00-17:00"
```

Required: `--when` (ISO 8601 local datetime), `--when-tz` (IANA TZ for `--when`).

## Step-by-step instructions for the agent

1. **Collect the inputs.** From the user's request, extract each participant's name, city or timezone, and working hours. If the user says "Tokyo", convert to `Asia/Tokyo` (use `references/iana_quickref.md` if unsure). If working hours are unstated, ask once or assume 09:00–17:00 weekdays and tell the user you assumed it.
2. **Choose the subcommand.**
   - User asks "when can we meet" / "find a time" / "what days work" → `overlap`. If `overlap` returns no windows, fall back to `squeeze` and tell the user you did so.
   - User says "we have to find something — even outside hours" / "this is critical / one-off" / asks for "least bad" / "best compromise" times → `squeeze` directly. Default to `--flex 180` (3 hours per side) unless the user states a tolerance.
   - User proposes a specific time and asks "does this work" / "what time is this for everyone" → `convert`.
3. **Pick an anchor timezone for `overlap`.** Default to the requester's timezone; otherwise UTC. State your choice in the response so the user can override.
4. **Run the script** and capture stdout. Use `--json` if you intend to post-process the result; use the default text output if you're showing it directly to the user.
5. **Summarize the result.** For `overlap`, present windows in the anchor TZ first, then the local time for each participant. For `convert`, lead with whether everyone is in working hours; if anyone is outside, surface that prominently.
6. **Handle errors.** If the script exits with a non-zero status, read the `ERROR:` line from stderr and ask the user the minimum question needed to fix the input (e.g. "Which IANA timezone for Bombay — `Asia/Kolkata`?").

## Expected output format

`overlap` (text): a header line with the anchor TZ, then one bullet per overlapping window followed by an indented list of each participant's local equivalent. `overlap` (JSON): an object with `anchor_tz`, `duration_min`, and a `windows` array; each window has `start_utc`, `end_utc`, `duration_min`, and a `per_participant` map.

`squeeze` (text): a header explaining the flex budget and ranking criterion, then a numbered list of candidates. Each candidate shows the slot in the anchor TZ, every participant's local time and IANA zone, a parenthesized disruption tag that always references that participant's configured hours (`inside their 09:00–17:00 hours`, `begins 3h45m before their 09:00 start`, `ends 2h after their 17:00 end`, or `OFF-DAY — outside their 09:00–17:00 working days`), and a summary line with worst-individual and team-total disruption. `squeeze` (JSON): a `candidates` array sorted by rank, each with `start_utc`, `end_utc`, `off_day_count`, `max_individual_disruption_min`, `total_team_disruption_min`, and a `per_participant` array carrying `work_start`, `work_end`, `local_start`, `local_end`, `early_required_min`, `late_required_min`, `personal_disruption_min`, and `off_day`.

`convert` (text): proposed time, UTC equivalent, then a table with one row per participant (local time, in/out of working hours, IANA TZ). Ends with a warning line if anyone is outside hours. `convert` (JSON): an object with `proposed`, `proposed_tz`, `utc`, and a `participants` array.

## Important limitations and checks

- **Overnight shifts are not supported.** Working hours must be `start < end`. If the user says "10pm-6am", split into two days or rephrase.
- **Weekends are excluded by default** in each participant's local time. Pass `work_days` in JSON or the optional `|Sat,Sun` segment in the compact form to include them. `squeeze` will not push someone onto a non-workday via flex alone — if the only viable squeeze slot lands on a participant's off-day, they are flagged with the `OFF-DAY` tag and that candidate is ranked below all in-workday candidates.
- **Date semantics use the anchor timezone.** `--start 2026-05-04` means midnight on May 4 in `--anchor`, not in any participant's local time. The agent should pick the anchor deliberately, not leave it as the UTC default unless the user asked for UTC.
- **Cross-day overlaps are real and correct.** A window that shows e.g. `Mon 16:00–17:00 LA / Tue 09:00–10:00 Sydney` is not a bug — Sydney is far enough ahead that LA Monday afternoon = Sydney Tuesday morning. Surface this clearly so the user isn't confused.
- **DST is handled by `zoneinfo`.** The script will produce correct times across spring-forward and fall-back days, but the model should not try to reason about DST itself — always defer to the script.
- **`squeeze` reports "personal disruption", not "minutes outside hours".** The ranked metric is how far before/after each person's *normal hours* the meeting begins or ends — a fairer proxy for human cost than counting overlap minutes. The agent should pass this metric through to the user verbatim ("Cara starts 3h45m before normal") rather than re-deriving it.
- **No calendar integration.** This skill computes possible meeting times; booking is out of scope.
