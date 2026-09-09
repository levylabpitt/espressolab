"""Turns a Decaid ShotRecord (as returned by GET /api/v1/shots/{id}) into rows
in `shots` and `shot_samples`."""

import re
from datetime import datetime

import asyncpg

_FRACTIONAL_SECONDS_RE = re.compile(r"(\.\d{6})\d+")


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.replace("Z", "+00:00")
    # datetime.fromisoformat only accepts up to microsecond precision;
    # truncate anything more precise than that instead of raising.
    text = _FRACTIONAL_SECONDS_RE.sub(r"\1", text)
    return datetime.fromisoformat(text)


async def _resolve_user_id(conn: asyncpg.Connection, *, extras: dict, drinker_name: str | None) -> str | None:
    candidate = (extras or {}).get("espressolab_user_id")
    if candidate:
        row = await conn.fetchrow("SELECT id FROM users WHERE id = $1", candidate)
        if row:
            return str(row["id"])
    if drinker_name:
        row = await conn.fetchrow(
            "SELECT id FROM users WHERE lower(display_name) = lower($1)", drinker_name
        )
        if row:
            return str(row["id"])
    return None


async def ingest_shot(pool: asyncpg.Pool, shot: dict) -> str:
    """Upserts a single shot and its samples. Returns the shot id."""
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

    async with pool.acquire() as conn:
        async with conn.transaction():
            user_id = await _resolve_user_id(
                conn, extras=extras, drinker_name=context.get("drinkerName")
            )

            await conn.execute(
                """
                INSERT INTO shots (
                    id, user_id, drinker_name, barista_name, started_at,
                    created_at_decaid, updated_at_decaid, duration_seconds, stop_reason,
                    profile_title, target_dose_weight, target_yield,
                    grinder_model, grinder_setting, grinder_id,
                    coffee_name, coffee_roaster, bean_batch_id,
                    actual_dose_weight, actual_yield, drink_tds, drink_ey, enjoyment,
                    espresso_notes, raw_workflow, raw_annotations
                ) VALUES (
                    $1, $2, $3, $4, $5,
                    $6, $7, $8, $9,
                    $10, $11, $12,
                    $13, $14, $15,
                    $16, $17, $18,
                    $19, $20, $21, $22, $23,
                    $24, $25::jsonb, $26::jsonb
                )
                ON CONFLICT (id) DO UPDATE SET
                    user_id = EXCLUDED.user_id,
                    drinker_name = EXCLUDED.drinker_name,
                    barista_name = EXCLUDED.barista_name,
                    updated_at_decaid = EXCLUDED.updated_at_decaid,
                    duration_seconds = EXCLUDED.duration_seconds,
                    stop_reason = EXCLUDED.stop_reason,
                    actual_dose_weight = EXCLUDED.actual_dose_weight,
                    actual_yield = EXCLUDED.actual_yield,
                    drink_tds = EXCLUDED.drink_tds,
                    drink_ey = EXCLUDED.drink_ey,
                    enjoyment = EXCLUDED.enjoyment,
                    espresso_notes = EXCLUDED.espresso_notes,
                    raw_workflow = EXCLUDED.raw_workflow,
                    raw_annotations = EXCLUDED.raw_annotations
                """,
                shot_id,
                user_id,
                context.get("drinkerName"),
                context.get("baristaName"),
                started_at,
                _parse_dt(shot.get("createdAt")),
                _parse_dt(shot.get("updatedAt")),
                duration_seconds,
                shot.get("stopReason"),
                profile.get("title"),
                context.get("targetDoseWeight"),
                context.get("targetYield"),
                context.get("grinderModel"),
                context.get("grinderSetting"),
                context.get("grinderId"),
                context.get("coffeeName"),
                context.get("coffeeRoaster"),
                context.get("beanBatchId"),
                annotations.get("actualDoseWeight"),
                annotations.get("actualYield"),
                annotations.get("drinkTds"),
                annotations.get("drinkEy"),
                annotations.get("enjoyment"),
                annotations.get("espressoNotes"),
                _as_json(workflow),
                _as_json(annotations),
            )

            await conn.execute("DELETE FROM shot_samples WHERE shot_id = $1", shot_id)

            rows = []
            base_time = sample_times[0] if sample_times else started_at
            for seq, m in enumerate(measurements):
                machine = m.get("machine") or {}
                state = machine.get("state") or {}
                scale = m.get("scale") or {}
                sample_time = (
                    _parse_dt(machine.get("timestamp"))
                    or _parse_dt(scale.get("timestamp"))
                    or base_time
                )
                elapsed = (sample_time - base_time).total_seconds()
                rows.append(
                    (
                        shot_id,
                        sample_time,
                        seq,
                        elapsed,
                        state.get("state"),
                        state.get("substate"),
                        machine.get("flow"),
                        machine.get("pressure"),
                        machine.get("targetFlow"),
                        machine.get("targetPressure"),
                        machine.get("mixTemperature"),
                        machine.get("groupTemperature"),
                        machine.get("targetMixTemperature"),
                        machine.get("targetGroupTemperature"),
                        machine.get("steamTemperature"),
                        machine.get("profileFrame"),
                        scale.get("weight"),
                        scale.get("weightFlow"),
                        m.get("volume"),
                    )
                )

            if rows:
                await conn.executemany(
                    """
                    INSERT INTO shot_samples (
                        shot_id, sample_time, seq, elapsed_seconds,
                        machine_state, machine_substate,
                        flow, pressure, target_flow, target_pressure,
                        mix_temperature, group_temperature,
                        target_mix_temperature, target_group_temperature,
                        steam_temperature, profile_frame,
                        weight, weight_flow, volume
                    ) VALUES (
                        $1, $2, $3, $4,
                        $5, $6,
                        $7, $8, $9, $10,
                        $11, $12,
                        $13, $14,
                        $15, $16,
                        $17, $18, $19
                    )
                    """,
                    rows,
                )

    return shot_id


def _as_json(value: dict) -> str:
    import json

    return json.dumps(value)
