"""Turns a Decaid ShotRecord (as returned by GET /api/v1/shots/{id}) into rows
in `shots` and `shot_samples`."""

import re
from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from .models import shot_samples, shots, users

_FRACTIONAL_SECONDS_RE = re.compile(r"(\.\d{6})\d+")


def _parse_dt(value: str | None) -> datetime | None:
    """Parses an ISO8601 timestamp to a naive UTC datetime (so it stores the
    same way on SQLite, which has no timezone-aware type, and Postgres)."""
    if not value:
        return None
    text = value.replace("Z", "+00:00")
    # datetime.fromisoformat only accepts up to microsecond precision;
    # truncate anything more precise than that instead of raising.
    text = _FRACTIONAL_SECONDS_RE.sub(r"\1", text)
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


async def _resolve_user_id(conn: AsyncConnection, *, extras: dict, drinker_name: str | None) -> str | None:
    candidate = (extras or {}).get("espressolab_user_id")
    if candidate:
        result = await conn.execute(sa.select(users.c.id).where(users.c.id == candidate))
        row = result.first()
        if row:
            return row[0]
    if drinker_name:
        result = await conn.execute(
            sa.select(users.c.id).where(sa.func.lower(users.c.display_name) == drinker_name.lower())
        )
        row = result.first()
        if row:
            return row[0]
    return None


async def ingest_shot(engine: AsyncEngine, shot: dict) -> str:
    """Replaces a shot and its samples. Returns the shot id."""
    shot_id = shot["id"]
    workflow = shot.get("workflow") or {}
    context = workflow.get("context") or {}
    profile = workflow.get("profile") or {}
    annotations = shot.get("annotations") or {}
    extras = context.get("extras") or {}
    measurements = shot.get("measurements") or []

    started_at = _parse_dt(shot.get("timestamp")) or _parse_dt(shot.get("createdAt"))
    sample_times = [
        _parse_dt(m.get("machine", {}).get("timestamp")) or _parse_dt(m.get("scale", {}).get("timestamp"))
        for m in measurements
    ]
    sample_times = [t for t in sample_times if t is not None]
    if sample_times:
        first_sample = min(sample_times)
        last_sample = max(sample_times)
        duration_seconds = (last_sample - first_sample).total_seconds()
        if started_at is None:
            started_at = first_sample
    else:
        duration_seconds = None

    if started_at is None:
        raise ValueError(f"shot {shot_id} has no usable timestamp")

    async with engine.begin() as conn:
        user_id = await _resolve_user_id(conn, extras=extras, drinker_name=context.get("drinkerName"))

        # Delete-then-insert instead of an upsert: keeps this portable across
        # SQLite and Postgres. ON DELETE CASCADE takes shot_samples with it.
        await conn.execute(sa.delete(shots).where(shots.c.id == shot_id))

        await conn.execute(
            sa.insert(shots).values(
                id=shot_id,
                user_id=user_id,
                drinker_name=context.get("drinkerName"),
                barista_name=context.get("baristaName"),
                started_at=started_at,
                created_at_decaid=_parse_dt(shot.get("createdAt")),
                updated_at_decaid=_parse_dt(shot.get("updatedAt")),
                duration_seconds=duration_seconds,
                stop_reason=shot.get("stopReason"),
                profile_title=profile.get("title"),
                target_dose_weight=context.get("targetDoseWeight"),
                target_yield=context.get("targetYield"),
                grinder_model=context.get("grinderModel"),
                grinder_setting=context.get("grinderSetting"),
                grinder_id=context.get("grinderId"),
                coffee_name=context.get("coffeeName"),
                coffee_roaster=context.get("coffeeRoaster"),
                bean_batch_id=context.get("beanBatchId"),
                actual_dose_weight=annotations.get("actualDoseWeight"),
                actual_yield=annotations.get("actualYield"),
                drink_tds=annotations.get("drinkTds"),
                drink_ey=annotations.get("drinkEy"),
                enjoyment=annotations.get("enjoyment"),
                espresso_notes=annotations.get("espressoNotes"),
                raw_workflow=workflow,
                raw_annotations=annotations,
            )
        )

        rows = []
        base_time = sample_times[0] if sample_times else started_at
        for seq, m in enumerate(measurements):
            machine = m.get("machine") or {}
            state = machine.get("state") or {}
            scale = m.get("scale") or {}
            sample_time = (
                _parse_dt(machine.get("timestamp")) or _parse_dt(scale.get("timestamp")) or base_time
            )
            rows.append(
                {
                    "shot_id": shot_id,
                    "sample_time": sample_time,
                    "seq": seq,
                    "elapsed_seconds": (sample_time - base_time).total_seconds(),
                    "machine_state": state.get("state"),
                    "machine_substate": state.get("substate"),
                    "flow": machine.get("flow"),
                    "pressure": machine.get("pressure"),
                    "target_flow": machine.get("targetFlow"),
                    "target_pressure": machine.get("targetPressure"),
                    "mix_temperature": machine.get("mixTemperature"),
                    "group_temperature": machine.get("groupTemperature"),
                    "target_mix_temperature": machine.get("targetMixTemperature"),
                    "target_group_temperature": machine.get("targetGroupTemperature"),
                    "steam_temperature": machine.get("steamTemperature"),
                    "profile_frame": machine.get("profileFrame"),
                    "weight": scale.get("weight"),
                    "weight_flow": scale.get("weightFlow"),
                    "volume": m.get("volume"),
                }
            )

        if rows:
            await conn.execute(sa.insert(shot_samples), rows)

    return shot_id
