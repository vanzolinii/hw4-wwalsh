#!/usr/bin/env python3
"""tz_meeting.py — find meeting times across timezones, or convert a proposed
time into every participant's local clock.

This is the deterministic core of the `timezone-meeting-finder` skill. LLMs
are unreliable at IANA timezone math (DST transitions, half-hour zones,
historic offsets, intersecting working windows across many people). zoneinfo
is reliable, so we let the model orchestrate and let Python do the
arithmetic.

Subcommands
-----------
  overlap  — given participants and a date range, find common working windows
             where everyone is in their normal hours.
  squeeze  — same problem, but allow each participant to flex N minutes earlier
             or later than their normal hours. Returns ranked suggestions with
             a per-participant flex breakdown so the user can see who is
             bearing the cost.
  convert  — given a proposed datetime, show each participant's local time
             and flag whether it falls within their working hours.

Inputs
------
- JSON file via --input, OR
- Compact CLI form via --participant flags (repeatable).

Compact participant form:  "Name|IANA_TZ|HH:MM-HH:MM[|days]"
  days = comma-separated subset of {Mon,Tue,Wed,Thu,Fri,Sat,Sun}; default Mon-Fri.

Examples
--------
  python tz_meeting.py overlap \\
      --participant "Alice|America/New_York|09:00-17:00" \\
      --participant "Bob|Europe/London|09:00-17:00" \\
      --participant "Carol|Asia/Tokyo|10:00-18:00" \\
      --start 2026-05-04 --end 2026-05-08 --duration 30 \\
      --anchor America/New_York

  python tz_meeting.py squeeze \\
      --participant "Pat|America/Phoenix|09:00-17:00" \\
      --participant "Cara|Australia/Sydney|09:00-17:00" \\
      --participant "Liam|Europe/London|09:00-17:00" \\
      --start 2026-05-04 --end 2026-05-08 --duration 30 \\
      --flex 180 --anchor America/Phoenix

  python tz_meeting.py convert \\
      --when "2026-05-04T10:30" --when-tz America/New_York \\
      --participant "Alice|America/New_York|09:00-17:00" \\
      --participant "Bob|Europe/London|09:00-17:00"
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, time, date
from typing import List, Optional, Tuple

try:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
except ImportError:  # pragma: no cover
    print(
        "ERROR: zoneinfo not available. Requires Python 3.9+. "
        "On older Python install backports.zoneinfo and tzdata.",
        file=sys.stderr,
    )
    sys.exit(2)


WEEKDAY_MAP = {"Mon": 0, "Tue": 1, "Wed": 2, "Thu": 3, "Fri": 4, "Sat": 5, "Sun": 6}
WEEKDAY_REVERSE = {v: k for k, v in WEEKDAY_MAP.items()}
UTC = ZoneInfo("UTC")


@dataclass
class Participant:
    name: str
    tz: ZoneInfo
    tz_name: str
    work_start: time
    work_end: time
    work_days: List[int]  # ints 0..6 (Mon..Sun)


# ---------- input parsing ----------


def _make_zone(tz_name: str, who: str) -> ZoneInfo:
    try:
        return ZoneInfo(tz_name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Unknown IANA timezone '{tz_name}' for {who}") from exc


def parse_participant_str(spec: str) -> Participant:
    parts = spec.split("|")
    if len(parts) < 3:
        raise ValueError(
            f"Bad participant spec '{spec}'. "
            "Expected 'Name|IANA_TZ|HH:MM-HH:MM[|days]'"
        )
    name = parts[0].strip()
    tz_name = parts[1].strip()
    hours = parts[2].strip()
    tz = _make_zone(tz_name, name)

    if "-" not in hours:
        raise ValueError(f"Bad hours '{hours}' for {name}; expected HH:MM-HH:MM")
    start_s, end_s = hours.split("-", 1)
    try:
        ws = time.fromisoformat(start_s.strip())
        we = time.fromisoformat(end_s.strip())
    except ValueError as exc:
        raise ValueError(f"Bad hours '{hours}' for {name}; expected HH:MM-HH:MM") from exc
    if not (ws < we):
        raise ValueError(
            f"Working hours must be start < end for {name}; got {ws}..{we} "
            "(overnight shifts are not currently supported)"
        )

    if len(parts) >= 4 and parts[3].strip():
        days_tokens = [d.strip() for d in parts[3].split(",")]
        try:
            work_days = sorted({WEEKDAY_MAP[t] for t in days_tokens})
        except KeyError as e:
            raise ValueError(f"Bad day token {e} for {name}; use Mon..Sun") from e
    else:
        work_days = [0, 1, 2, 3, 4]  # Mon..Fri

    return Participant(name, tz, tz_name, ws, we, work_days)


def parse_participants_json(path: str) -> List[Participant]:
    with open(path, "r", encoding="utf-8") as f:
        blob = json.load(f)
    out: List[Participant] = []
    for p in blob.get("participants", []):
        name = p["name"]
        tz_name = p["tz"]
        tz = _make_zone(tz_name, name)
        ws = time.fromisoformat(p["work_start"])
        we = time.fromisoformat(p["work_end"])
        if not (ws < we):
            raise ValueError(
                f"Working hours must be start < end for {name}; got {ws}..{we}"
            )
        days = p.get("work_days") or ["Mon", "Tue", "Wed", "Thu", "Fri"]
        try:
            work_days = sorted({WEEKDAY_MAP[d] for d in days})
        except KeyError as e:
            raise ValueError(f"Bad day token {e} for {name}") from e
        out.append(Participant(name, tz, tz_name, ws, we, work_days))
    return out


# ---------- core algorithm ----------


def participant_windows(
    p: Participant, span_start_utc: datetime, span_end_utc: datetime
) -> List[Tuple[datetime, datetime]]:
    """All UTC working windows for `p` that intersect [span_start_utc, span_end_utc].

    Iterates the participant's *local* dates (with ±1 day padding so that
    windows whose UTC equivalent lands inside the span aren't missed when the
    participant's local date sits just outside it).
    """
    local_start = span_start_utc.astimezone(p.tz).date() - timedelta(days=1)
    local_end = span_end_utc.astimezone(p.tz).date() + timedelta(days=1)

    out: List[Tuple[datetime, datetime]] = []
    d = local_start
    while d <= local_end:
        if d.weekday() in p.work_days:
            local_open = datetime.combine(d, p.work_start, tzinfo=p.tz)
            local_close = datetime.combine(d, p.work_end, tzinfo=p.tz)
            uo = local_open.astimezone(UTC)
            uc = local_close.astimezone(UTC)
            s = max(uo, span_start_utc)
            e = min(uc, span_end_utc)
            if s < e:
                out.append((s, e))
        d += timedelta(days=1)
    out.sort()
    return out


def intersect_intervals(
    a: List[Tuple[datetime, datetime]], b: List[Tuple[datetime, datetime]]
) -> List[Tuple[datetime, datetime]]:
    """Two-pointer intersection of two sorted, internally-non-overlapping lists."""
    out: List[Tuple[datetime, datetime]] = []
    i = j = 0
    while i < len(a) and j < len(b):
        s = max(a[i][0], b[j][0])
        e = min(a[i][1], b[j][1])
        if s < e:
            out.append((s, e))
        if a[i][1] < b[j][1]:
            i += 1
        else:
            j += 1
    return out


def find_overlap(
    participants: List[Participant],
    start_date: date,
    end_date: date,
    duration_min: int,
    anchor_tz: ZoneInfo,
) -> List[Tuple[datetime, datetime]]:
    if not participants:
        return []
    span_start_local = datetime.combine(start_date, time(0, 0), tzinfo=anchor_tz)
    span_end_local = datetime.combine(
        end_date + timedelta(days=1), time(0, 0), tzinfo=anchor_tz
    )
    span_start_utc = span_start_local.astimezone(UTC)
    span_end_utc = span_end_local.astimezone(UTC)

    common = participant_windows(participants[0], span_start_utc, span_end_utc)
    for p in participants[1:]:
        common = intersect_intervals(
            common, participant_windows(p, span_start_utc, span_end_utc)
        )
        if not common:
            return []

    min_delta = timedelta(minutes=duration_min)
    return [(s, e) for s, e in common if (e - s) >= min_delta]


# ---------- squeeze (stretched-hour suggestions with flex cost) ----------


def participant_stretched_windows(
    p: Participant,
    span_start_utc: datetime,
    span_end_utc: datetime,
    flex_before_min: int,
    flex_after_min: int,
) -> List[Tuple[datetime, datetime]]:
    """Like `participant_windows`, but each daily window is extended by
    `flex_before_min` earlier and `flex_after_min` later in the participant's
    local zone. Still restricted to `p.work_days` (no weekend invasion via
    flex). Adjacent days that overlap after flexing are merged."""
    local_start = span_start_utc.astimezone(p.tz).date() - timedelta(days=2)
    local_end = span_end_utc.astimezone(p.tz).date() + timedelta(days=2)

    windows: List[Tuple[datetime, datetime]] = []
    d = local_start
    while d <= local_end:
        if d.weekday() in p.work_days:
            local_open = datetime.combine(d, p.work_start, tzinfo=p.tz) - timedelta(
                minutes=flex_before_min
            )
            local_close = datetime.combine(d, p.work_end, tzinfo=p.tz) + timedelta(
                minutes=flex_after_min
            )
            uo = local_open.astimezone(UTC)
            uc = local_close.astimezone(UTC)
            s = max(uo, span_start_utc)
            e = min(uc, span_end_utc)
            if s < e:
                windows.append((s, e))
        d += timedelta(days=1)
    windows.sort()

    # Merge any adjacent/overlapping windows so the intersection algorithm
    # receives non-overlapping input.
    merged: List[Tuple[datetime, datetime]] = []
    for w in windows:
        if merged and merged[-1][1] >= w[0]:
            prev = merged[-1]
            merged[-1] = (prev[0], max(prev[1], w[1]))
        else:
            merged.append(w)
    return merged


def compute_flex_breakdown(
    p: Participant, m_start_utc: datetime, m_end_utc: datetime
) -> dict:
    """How disruptive is this meeting for `p`?

    The metric is *personal disruption* — how far from `p`'s normal working
    hours are they being asked to be present. A 30-minute meeting at 05:00
    when work_start is 09:00 yields `early_required_min = 240`, not 30: the
    person is being asked to start four hours early, even though only thirty
    minutes of meeting time falls outside their hours.

    Fields:
      - early_required_min: minutes before normal work_start the meeting begins
      - late_required_min:  minutes after  normal work_end   the meeting ends
      - off_day:            primary date is not in `p.work_days`
      - personal_disruption_min: early + late (off_day handled separately
                                 in ranking — it is a categorical penalty)
    """
    m_start_local = m_start_utc.astimezone(p.tz)
    m_end_local = m_end_utc.astimezone(p.tz)
    total_min = (m_end_utc - m_start_utc).total_seconds() / 60

    # Pick a primary local date — prefer the start date if it is a workday,
    # otherwise fall back to the end date. If both are off, the meeting is on
    # an off-day for this participant.
    primary_date = m_start_local.date()
    if primary_date.weekday() not in p.work_days:
        if m_end_local.date().weekday() in p.work_days:
            primary_date = m_end_local.date()
    off_day = primary_date.weekday() not in p.work_days

    early_required_min = 0.0
    late_required_min = 0.0
    if not off_day:
        ws = datetime.combine(primary_date, p.work_start, tzinfo=p.tz)
        we = datetime.combine(primary_date, p.work_end, tzinfo=p.tz)
        if m_start_local < ws:
            early_required_min = (ws - m_start_local).total_seconds() / 60
        if m_end_local > we:
            late_required_min = (m_end_local - we).total_seconds() / 60

    personal_disruption_min = early_required_min + late_required_min
    return {
        "name": p.name,
        "tz": p.tz_name,
        "work_start": p.work_start.strftime("%H:%M"),
        "work_end": p.work_end.strftime("%H:%M"),
        "local_start": m_start_local.strftime("%a %Y-%m-%d %H:%M"),
        "local_end": m_end_local.strftime("%a %H:%M"),
        "total_min": int(round(total_min)),
        "off_day": off_day,
        "early_required_min": int(round(early_required_min)),
        "late_required_min": int(round(late_required_min)),
        "personal_disruption_min": int(round(personal_disruption_min)),
    }


def best_slot_in_region(
    participants: List[Participant],
    region_start: datetime,
    region_end: datetime,
    duration_min: int,
    step_min: int = 15,
) -> Optional[dict]:
    """Slide a `duration_min` window across [region_start, region_end] in
    `step_min` increments and pick the slot that minimizes (any-weekend,
    max-individual-flex, total-flex). Returns the slot's metadata or None if
    the region is too small."""
    duration = timedelta(minutes=duration_min)
    if region_end - region_start < duration:
        return None
    step = timedelta(minutes=step_min)
    best = None
    cur = region_start
    while cur + duration <= region_end:
        slot_start, slot_end = cur, cur + duration
        breakdowns = [compute_flex_breakdown(p, slot_start, slot_end) for p in participants]
        off_day_count = sum(1 for b in breakdowns if b["off_day"])
        max_disruption = max(b["personal_disruption_min"] for b in breakdowns)
        total_disruption = sum(b["personal_disruption_min"] for b in breakdowns)
        # Sort key: fewest off-day participants → smallest worst case →
        # smallest team total → earliest start (stable tiebreak).
        key = (off_day_count, max_disruption, total_disruption, slot_start)
        if best is None or key < best["key"]:
            best = {
                "key": key,
                "slot_start_utc": slot_start,
                "slot_end_utc": slot_end,
                "off_day_count": off_day_count,
                "max_disruption_min": max_disruption,
                "total_disruption_min": total_disruption,
                "breakdowns": breakdowns,
            }
        cur += step
    return best


def find_squeeze_options(
    participants: List[Participant],
    start_date: date,
    end_date: date,
    duration_min: int,
    anchor_tz: ZoneInfo,
    flex_before_min: int,
    flex_after_min: int,
    step_min: int = 15,
) -> List[dict]:
    """Find candidate stretched-hour regions, then pick the best `duration_min`
    slot inside each. Returns a list of slot dicts (see `best_slot_in_region`)
    sorted by least disruption."""
    if not participants:
        return []
    span_start_local = datetime.combine(start_date, time(0, 0), tzinfo=anchor_tz)
    span_end_local = datetime.combine(
        end_date + timedelta(days=1), time(0, 0), tzinfo=anchor_tz
    )
    span_start_utc = span_start_local.astimezone(UTC)
    span_end_utc = span_end_local.astimezone(UTC)

    common = participant_stretched_windows(
        participants[0], span_start_utc, span_end_utc, flex_before_min, flex_after_min
    )
    for p in participants[1:]:
        common = intersect_intervals(
            common,
            participant_stretched_windows(
                p, span_start_utc, span_end_utc, flex_before_min, flex_after_min
            ),
        )
        if not common:
            return []

    options: List[dict] = []
    for r_start, r_end in common:
        slot = best_slot_in_region(
            participants, r_start, r_end, duration_min, step_min=step_min
        )
        if slot is not None:
            options.append(slot)
    options.sort(key=lambda o: o["key"])
    return options


# ---------- output formatting ----------


def format_overlap_report(
    windows: List[Tuple[datetime, datetime]],
    participants: List[Participant],
    anchor_tz: ZoneInfo,
    anchor_label: str,
) -> str:
    if not windows:
        return "No overlapping working windows found in the given range."
    lines = [
        f"Found {len(windows)} overlapping window(s) (anchor TZ = {anchor_label}):",
        "",
    ]
    name_w = max(len(p.name) for p in participants)
    for s, e in windows:
        s_anchor = s.astimezone(anchor_tz)
        e_anchor = e.astimezone(anchor_tz)
        dur = int((e - s).total_seconds() / 60)
        lines.append(
            f"• {s_anchor.strftime('%a %Y-%m-%d %H:%M')} – "
            f"{e_anchor.strftime('%H:%M')} {anchor_label}  ({dur} min)"
        )
        for p in participants:
            ls = s.astimezone(p.tz)
            le = e.astimezone(p.tz)
            lines.append(
                f"    {p.name:<{name_w}}  "
                f"{ls.strftime('%a %H:%M')}–{le.strftime('%H:%M')}  {p.tz_name}"
            )
        lines.append("")
    return "\n".join(lines).rstrip()


def _human_minutes(m: int) -> str:
    if m <= 0:
        return "0m"
    h, mn = divmod(int(m), 60)
    if h and mn:
        return f"{h}h{mn:02d}m"
    if h:
        return f"{h}h"
    return f"{mn}m"


def _flex_label(b: dict) -> str:
    """Compose a human-readable disruption tag for one participant.

    Always references the participant's configured `work_start`/`work_end`
    (in their local zone, shown earlier on the same line) — never an abstract
    "normal", because what counts as normal varies per person.
    """
    if b["off_day"]:
        return f"OFF-DAY — outside their {b['work_start']}–{b['work_end']} working days"
    if b["personal_disruption_min"] == 0:
        return f"inside their {b['work_start']}–{b['work_end']} hours"
    parts: List[str] = []
    if b["early_required_min"] > 0:
        parts.append(
            f"begins {_human_minutes(b['early_required_min'])} before their "
            f"{b['work_start']} start"
        )
    if b["late_required_min"] > 0:
        parts.append(
            f"ends {_human_minutes(b['late_required_min'])} after their "
            f"{b['work_end']} end"
        )
    return ", ".join(parts)


def format_squeeze_report(
    options: List[dict],
    participants: List[Participant],
    anchor_tz: ZoneInfo,
    anchor_label: str,
    flex_before_min: int,
    flex_after_min: int,
    max_results: int,
) -> str:
    if not options:
        return (
            f"No squeeze candidates found within ±{flex_before_min}/"
            f"{flex_after_min} min of normal hours. Increase --flex or widen "
            "the date range."
        )
    shown = options[: max(1, max_results)]
    name_w = max(len(p.name) for p in participants)
    lines = [
        f"Found {len(options)} squeeze candidate(s); showing top {len(shown)} "
        f"(flex allowed: up to {_human_minutes(flex_before_min)} early / "
        f"{_human_minutes(flex_after_min)} late per person). Anchor TZ = {anchor_label}.",
        "Ranked by: fewest off-day participants → smallest worst-individual "
        "disruption → smallest team total disruption.",
        "",
    ]
    for i, opt in enumerate(shown, start=1):
        s = opt["slot_start_utc"].astimezone(anchor_tz)
        e = opt["slot_end_utc"].astimezone(anchor_tz)
        dur = int((opt["slot_end_utc"] - opt["slot_start_utc"]).total_seconds() / 60)
        off_tag = (
            f"  [{opt['off_day_count']} on off-day]" if opt["off_day_count"] else ""
        )
        lines.append(
            f"#{i} — {s.strftime('%a %Y-%m-%d %H:%M')}–{e.strftime('%H:%M')} "
            f"{anchor_label}  ({dur} min){off_tag}"
        )
        for b in opt["breakdowns"]:
            lines.append(
                f"    {b['name']:<{name_w}}  {b['local_start']}–{b['local_end']}  "
                f"{b['tz']:<24} ({_flex_label(b)})"
            )
        lines.append(
            f"    -> worst individual flex: "
            f"{_human_minutes(opt['max_disruption_min'])} "
            f"outside their configured hours; team total: "
            f"{_human_minutes(opt['total_disruption_min'])}"
        )
        lines.append("")
    return "\n".join(lines).rstrip()


def convert_time(
    when_local: datetime,
    when_tz: ZoneInfo,
    when_tz_name: str,
    participants: List[Participant],
) -> dict:
    if when_local.tzinfo is None:
        when_local = when_local.replace(tzinfo=when_tz)
    when_utc = when_local.astimezone(UTC)
    result = {
        "proposed": when_local.isoformat(),
        "proposed_tz": when_tz_name,
        "utc": when_utc.isoformat(),
        "participants": [],
    }
    for p in participants:
        local = when_utc.astimezone(p.tz)
        in_work_day = local.weekday() in p.work_days
        in_hours = p.work_start <= local.time() < p.work_end
        result["participants"].append(
            {
                "name": p.name,
                "tz": p.tz_name,
                "local": local.strftime("%a %Y-%m-%d %H:%M"),
                "weekday": WEEKDAY_REVERSE[local.weekday()],
                "within_working_hours": bool(in_work_day and in_hours),
            }
        )
    return result


def format_convert_report(result: dict) -> str:
    lines = [
        f"Proposed: {result['proposed']} ({result['proposed_tz']})",
        f"UTC:      {result['utc']}",
        "",
        f"{'Participant':<15} {'Local time':<22} {'Working hours?':<22} {'TZ'}",
        f"{'-'*15} {'-'*22} {'-'*22} {'-'*30}",
    ]
    any_outside = False
    for p in result["participants"]:
        flag = "yes" if p["within_working_hours"] else "NO — outside hours"
        if not p["within_working_hours"]:
            any_outside = True
        lines.append(f"{p['name']:<15} {p['local']:<22} {flag:<22} {p['tz']}")
    if any_outside:
        lines.append("")
        lines.append(
            "WARNING: at least one participant is outside their working hours."
        )
    return "\n".join(lines)


# ---------- CLI plumbing ----------


def collect_participants(args) -> List[Participant]:
    parts: List[Participant] = []
    if args.input:
        parts.extend(parse_participants_json(args.input))
    if args.participant:
        for s in args.participant:
            parts.append(parse_participant_str(s))
    if not parts:
        raise ValueError("no participants given. Use --input or --participant.")
    # Deduplicate by name (last-write-wins) so JSON+CLI can override cleanly.
    seen = {}
    for p in parts:
        seen[p.name] = p
    return list(seen.values())


def cmd_overlap(args) -> int:
    participants = collect_participants(args)
    anchor_tz = _make_zone(args.anchor, "anchor")
    start_d = date.fromisoformat(args.start)
    end_d = date.fromisoformat(args.end)
    if end_d < start_d:
        raise ValueError("--end must be >= --start")

    windows = find_overlap(participants, start_d, end_d, args.duration, anchor_tz)

    if args.json:
        out = {
            "anchor_tz": args.anchor,
            "duration_min": args.duration,
            "windows": [
                {
                    "start_utc": s.isoformat(),
                    "end_utc": e.isoformat(),
                    "duration_min": int((e - s).total_seconds() / 60),
                    "per_participant": {
                        p.name: {
                            "tz": p.tz_name,
                            "local_start": s.astimezone(p.tz).isoformat(),
                            "local_end": e.astimezone(p.tz).isoformat(),
                        }
                        for p in participants
                    },
                }
                for s, e in windows
            ],
        }
        print(json.dumps(out, indent=2))
    else:
        print(format_overlap_report(windows, participants, anchor_tz, args.anchor))
    return 0


def cmd_squeeze(args) -> int:
    participants = collect_participants(args)
    anchor_tz = _make_zone(args.anchor, "anchor")
    start_d = date.fromisoformat(args.start)
    end_d = date.fromisoformat(args.end)
    if end_d < start_d:
        raise ValueError("--end must be >= --start")

    flex_before = args.flex if args.flex_before is None else args.flex_before
    flex_after = args.flex if args.flex_after is None else args.flex_after
    if flex_before < 0 or flex_after < 0:
        raise ValueError("--flex/--flex-before/--flex-after must be >= 0")

    options = find_squeeze_options(
        participants,
        start_d,
        end_d,
        args.duration,
        anchor_tz,
        flex_before,
        flex_after,
        step_min=args.step,
    )

    if args.json:
        out = {
            "anchor_tz": args.anchor,
            "duration_min": args.duration,
            "flex_before_min": flex_before,
            "flex_after_min": flex_after,
            "step_min": args.step,
            "candidates": [
                {
                    "rank": i,
                    "start_utc": opt["slot_start_utc"].isoformat(),
                    "end_utc": opt["slot_end_utc"].isoformat(),
                    "off_day_count": opt["off_day_count"],
                    "max_individual_disruption_min": opt["max_disruption_min"],
                    "total_team_disruption_min": opt["total_disruption_min"],
                    "per_participant": [
                        {
                            "name": b["name"],
                            "tz": b["tz"],
                            "work_start": b["work_start"],
                            "work_end": b["work_end"],
                            "local_start": b["local_start"],
                            "local_end": b["local_end"],
                            "off_day": b["off_day"],
                            "early_required_min": b["early_required_min"],
                            "late_required_min": b["late_required_min"],
                            "personal_disruption_min": b["personal_disruption_min"],
                        }
                        for b in opt["breakdowns"]
                    ],
                }
                for i, opt in enumerate(options[: max(1, args.max_results)], start=1)
            ],
        }
        print(json.dumps(out, indent=2))
    else:
        print(
            format_squeeze_report(
                options,
                participants,
                anchor_tz,
                args.anchor,
                flex_before,
                flex_after,
                args.max_results,
            )
        )
    return 0


def cmd_convert(args) -> int:
    participants = collect_participants(args)
    when_tz = _make_zone(args.when_tz, "--when-tz")
    try:
        when_local = datetime.fromisoformat(args.when)
    except ValueError as exc:
        raise ValueError(
            f"--when must be ISO 8601 (e.g. 2026-05-04T10:30); got {args.when!r}"
        ) from exc
    if when_local.tzinfo is None:
        when_local = when_local.replace(tzinfo=when_tz)
    result = convert_time(when_local, when_tz, args.when_tz, participants)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(format_convert_report(result))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="tz_meeting",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--input", help="JSON file with a 'participants' array (see references/)"
    )
    common.add_argument(
        "--participant",
        action="append",
        default=[],
        help="Compact form 'Name|IANA_TZ|HH:MM-HH:MM[|Mon,Tue,...]'. Repeatable.",
    )
    common.add_argument(
        "--json", action="store_true", help="Emit JSON instead of human-readable text"
    )

    o = sub.add_parser("overlap", parents=[common], help="Find common working windows")
    o.add_argument("--start", required=True, help="Start date YYYY-MM-DD (in --anchor TZ)")
    o.add_argument("--end", required=True, help="End date YYYY-MM-DD (inclusive)")
    o.add_argument(
        "--duration", type=int, default=30, help="Minimum window length in minutes (default 30)"
    )
    o.add_argument(
        "--anchor",
        default="UTC",
        help="IANA TZ used to interpret --start/--end and to label the report (default UTC)",
    )
    o.set_defaults(func=cmd_overlap)

    sq = sub.add_parser(
        "squeeze",
        parents=[common],
        help=(
            "Suggest meeting slots when no strict overlap exists, allowing "
            "each participant to flex N minutes earlier or later"
        ),
    )
    sq.add_argument("--start", required=True, help="Start date YYYY-MM-DD (in --anchor TZ)")
    sq.add_argument("--end", required=True, help="End date YYYY-MM-DD (inclusive)")
    sq.add_argument(
        "--duration", type=int, default=30,
        help="Meeting length in minutes (default 30)",
    )
    sq.add_argument(
        "--anchor", default="UTC",
        help="IANA TZ used to interpret --start/--end and to label the report",
    )
    sq.add_argument(
        "--flex", type=int, default=120,
        help="Symmetric flex per side in minutes (default 120). Overridden by --flex-before/--flex-after.",
    )
    sq.add_argument(
        "--flex-before", type=int, default=None,
        help="Asymmetric: minutes a participant may start earlier than work_start",
    )
    sq.add_argument(
        "--flex-after", type=int, default=None,
        help="Asymmetric: minutes a participant may end later than work_end",
    )
    sq.add_argument(
        "--max-results", type=int, default=5,
        help="How many top suggestions to print (default 5)",
    )
    sq.add_argument(
        "--step", type=int, default=15,
        help="Granularity of slot search in minutes (default 15)",
    )
    sq.set_defaults(func=cmd_squeeze)

    c = sub.add_parser(
        "convert",
        parents=[common],
        help="Convert a single time into every participant's local clock",
    )
    c.add_argument("--when", required=True, help="ISO 8601 datetime, e.g. 2026-05-04T10:30")
    c.add_argument("--when-tz", required=True, help="IANA TZ for --when")
    c.set_defaults(func=cmd_convert)

    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (ValueError, FileNotFoundError, json.JSONDecodeError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
