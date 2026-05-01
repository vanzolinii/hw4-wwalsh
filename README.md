# hw4-wwalsh — `timezone-meeting-finder` skill

A reusable AI skill that finds overlapping working-hour windows for participants spread across multiple timezones, and converts a proposed meeting time into every participant's local clock with a working-hours flag.

> **Note on numbering:** This is **HW4**. The assignment text refers to itself as "Week 5 / HW5" in places, but in the course's homework sequence this submission is HW4 — that is why the repo, folder, and identifier are `hw-4` / `hw4-wwalsh`.

**Walkthrough video:** _TODO — paste Zoom Cloud or unlisted YouTube link here before submission._

## Layout

```
hw-4/
├── .agents/
│   └── skills/
│       └── timezone-meeting-finder/
│           ├── SKILL.md
│           ├── scripts/
│           │   └── tz_meeting.py
│           ├── references/
│           │   └── iana_quickref.md
│           └── assets/                 (empty — reserved)
├── examples/
│   └── team.json                       (sample participant list)
├── PROJECT_NOTES.md                    (running log of decisions)
└── README.md
```

## What the skill does

`timezone-meeting-finder` exposes three operations:

`overlap` — given a list of participants (each with name, IANA timezone, and working hours) and a date range, it returns every common working-hour window of at least the requested duration. Windows are reported in an anchor timezone with each participant's local equivalent.

`squeeze` — when no strict overlap exists (think Phoenix + London + Sydney for a 30-minute call), it suggests "least bad" slots. Each participant is allowed to flex up to N minutes earlier or later than their *own* configured hours, and candidates are ranked by who pays what cost — fewest off-day participants first, then smallest worst-individual disruption, then smallest team-total disruption. The output anchors every disruption tag to the participant's own configured `work_start` / `work_end` rather than an abstract "normal," because what counts as normal varies per person. A typical line reads: `Cara  Tue 2026-05-05 05:15–Tue 05:45  Australia/Sydney  (begins 3h45m before their 09:00 start)` — Cara's local time, Cara's IANA zone, Cara's configured 09:00 start, all on the same line.

`convert` — given a single proposed datetime in some timezone, it shows that moment in each participant's local clock and flags anyone outside their working hours.

## Why I chose this task

LLMs are unreliable at IANA timezone math. They confuse abbreviations like `EST` and `IST`, miss DST transitions, lose half-hour zones (Asia/Kolkata at +05:30, Asia/Kathmandu at +05:45), and especially struggle to correctly intersect *several* people's working windows over a date range. The Python `zoneinfo` module is reliable for all of this. So this is a clean fit for the skill pattern: the model orchestrates (parses what the user asked, picks defaults, presents results) and the script does the deterministic math.

The script is genuinely load-bearing — without it, the agent would produce confidently-wrong meeting times.

## How to use

### From an agent

Drop the `.agents/skills/timezone-meeting-finder/` folder into any project that uses agent skills (Claude Code, Codex, etc.) and prompt the assistant naturally:

> "Find a 30-minute window for Alice in NYC, Bob in London, and Carol in Tokyo next week."

> "We have to find time for Pat in Phoenix, Cara in Canberra, and Liam in London — what's the least painful slot?"

> "Does Tuesday May 5 at 10:30am Eastern work for the team?"

The agent reads `SKILL.md`, picks the right subcommand, fills in the participants, and runs the script.

### Direct CLI

```
python3 .agents/skills/timezone-meeting-finder/scripts/tz_meeting.py overlap \
    --participant "Alice|America/New_York|09:00-17:00" \
    --participant "Bob|Europe/London|09:00-17:00" \
    --start 2026-05-04 --end 2026-05-08 --duration 30 \
    --anchor America/New_York
```

```
python3 .agents/skills/timezone-meeting-finder/scripts/tz_meeting.py squeeze \
    --participant "Pat|America/Phoenix|09:00-17:00" \
    --participant "Cara|Australia/Sydney|09:00-17:00" \
    --participant "Liam|Europe/London|09:00-17:00" \
    --start 2026-05-04 --end 2026-05-08 --duration 30 \
    --flex 240 --anchor America/Phoenix
```

```
python3 .agents/skills/timezone-meeting-finder/scripts/tz_meeting.py convert \
    --when "2026-05-04T10:30" --when-tz America/New_York \
    --participant "Alice|America/New_York|09:00-17:00" \
    --participant "Bob|Europe/London|09:00-17:00" \
    --participant "Carol|Asia/Tokyo|10:00-18:00"
```

A JSON participant file works too:

```
python3 .agents/skills/timezone-meeting-finder/scripts/tz_meeting.py overlap \
    --input examples/team.json \
    --start 2026-05-04 --end 2026-05-08 --duration 30 \
    --anchor America/New_York
```

Add `--json` to either subcommand for machine-readable output.

## What the script does

`scripts/tz_meeting.py` is a single-file Python program (stdlib only — no `pip install`) that:

1. Parses participants from CLI flags or JSON, validating IANA timezone names against the system tzdata.
2. For `overlap`: for each participant, generates their daily working windows in UTC across the requested date range (handling DST automatically because each window is built fresh in the participant's local zone, then converted). Then intersects all participants' window lists with a two-pointer sweep, filters by minimum duration, and renders the result either as a human-readable report or JSON.
3. For `squeeze`: builds *stretched* per-participant windows (working hours ± `--flex` minutes), intersects them, then for each candidate region slides a `--duration`-minute meeting in `--step`-minute increments and picks the slot that minimizes (off-day count, max individual disruption, team total disruption). Per-participant disruption is "minutes before normal start the meeting begins" + "minutes after normal end the meeting ends" — a fairer proxy for human cost than counting overlap minutes.
4. For `convert`: converts the proposed datetime to UTC, then to each participant's local zone, and checks the result against their working hours and weekday constraints.

The sort-and-sweep intersection plus the disruption-minimizing slot search is what would be tedious and error-prone for an LLM to do in prose — especially when participants have different work-day sets and DST transitions land mid-range.

## Tests run

The script was exercised on the three cases the assignment requires:

1. **Normal:** Alice (NYC) + Bob (London), Mon–Fri 09:00–17:00 each, May 4–8 2026 → `overlap` returns five 3-hour windows (09:00–12:00 NYC = 14:00–17:00 London).
2. **Edge — no strict overlap:** Alice (NYC) + Priya (Mumbai), strict 09:00–17:00 Mon–Fri → `overlap` correctly reports "No overlapping working windows found in the given range" (Mumbai's day ends at 11:30 UTC, NYC's begins at 13:00 UTC).
3. **Cautious / partial decline:** `convert` for 2026-05-04 10:30 NYC across NYC + London + Tokyo → returns each local time and flags Carol (Tokyo, 23:30 local) as outside working hours, ending with a `WARNING:` line.

A fourth, harder case driven by the `squeeze` subcommand: Pat (Phoenix, never observes DST) + Cara (Canberra, just fell back to AEST) + Liam (London, on BST). The three zones span ~17 hours; even with ±3h flex, no overlap exists. With `--flex 240`, `squeeze` returns the unique squeeze corner of 12:15–12:45 Phoenix (Pat's lunch hour). For Cara that lands on Tue 05:15–05:45 Australia/Sydney, which begins 3h45m before her configured 09:00 start; for Liam that's Mon 20:15–20:45 Europe/London, which ends 3h45m after his configured 17:00 end. The 15-minute slot search picks 12:15 (not 12:00) because that splits the burden evenly between the two flexing participants.

A DST sanity check on 2026-03-08 (US spring-forward day) returned the correct EDT offset (`-04:00`), confirming `zoneinfo` is doing its job.

## What worked well

The split between prose and code felt natural: there is no temptation for the model to "reason" about DST or weekday math, because the script handles it. The compact `--participant` form keeps short asks ergonomic, while the JSON `--input` form scales to recurring teams.

`squeeze` was the most satisfying addition. Picking the right disruption metric mattered — an early version counted "minutes of meeting outside work hours," which made a 30-minute meeting at 5 AM look like only 30 minutes of flex when in reality the participant has to start four hours early. Switching to "minutes before *their* configured start" + "minutes after *their* configured end" made the output honest about who pays what cost. Wording also mattered: a first pass said "before normal," but there is no universal normal — every participant has their own configured hours. The label now anchors to the specific participant's `work_start` / `work_end` in their own local time on every line. The 15-minute slot-search step is what surfaces 12:15 Phoenix instead of 12:00 — splitting the flex burden evenly between Cara and Liam — which is exactly the kind of optimization that's tedious to do by hand and trivial in code.

The `references/iana_quickref.md` file is small but high-value — it heads off the most common ambiguity (`EST`, `IST`, `CST`, `BST` all having multiple meanings) before the script ever runs.

## Limitations

- Overnight shifts (`22:00–06:00`) aren't supported; working hours must be `start < end`. Workaround: split into two windows or shift to UTC.
- Holidays are ignored. The script knows weekdays but not country-specific holidays. A future version could accept a list of holiday dates per participant.
- `squeeze` will not push a participant onto a non-workday via flex alone — flexing four hours into Saturday is treated as off-day, not late-Friday. If the user wants to consider Saturday morning meetings, they need to add `Sat` to the participant's `work_days` explicitly.
- The disruption metric weights early and late minutes equally. In practice some people prefer staying late to waking early; the asymmetric `--flex-before` / `--flex-after` flags partially address this by capping the flex on each side, but the ranking metric itself is still symmetric.
- No calendar integration — this skill computes possible meeting times; it does not book them.
- The script has no automated test suite checked into the repo. Verification was by manual run-through of the four cases listed above.
