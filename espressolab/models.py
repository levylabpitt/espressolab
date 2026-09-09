"""Schema, as SQLAlchemy Core tables so it works against SQLite or Postgres.
This is the single source of truth — tables are created from this
automatically on startup (see db.py), no manual migration step needed."""

import uuid

import sqlalchemy as sa

metadata = sa.MetaData()


def _uuid() -> str:
    return str(uuid.uuid4())


users = sa.Table(
    "users",
    metadata,
    sa.Column("id", sa.String, primary_key=True, default=_uuid),
    sa.Column("display_name", sa.String, nullable=False, unique=True),
    sa.Column("avatar_emoji", sa.String, nullable=False, default="☕"),
    sa.Column("avatar_color", sa.String, nullable=False, default="#6f4e37"),
    sa.Column("active", sa.Boolean, nullable=False, default=True),
    sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
)

# One row per shot pulled on the DE1, as reported by Decaid's REST API
# (GET /api/v1/shots/{id}). Attribution comes from workflow.context, set by
# the portal via PUT /api/v1/workflow before the shot starts.
shots = sa.Table(
    "shots",
    metadata,
    sa.Column("id", sa.String, primary_key=True),  # Decaid's own shot id
    sa.Column("user_id", sa.String, sa.ForeignKey("users.id", ondelete="SET NULL")),
    sa.Column("drinker_name", sa.String),
    sa.Column("barista_name", sa.String),
    sa.Column("started_at", sa.DateTime, nullable=False),
    sa.Column("created_at_decaid", sa.DateTime),
    sa.Column("updated_at_decaid", sa.DateTime),
    sa.Column("duration_seconds", sa.Float),
    sa.Column("stop_reason", sa.String),
    sa.Column("profile_title", sa.String),
    sa.Column("target_dose_weight", sa.Float),
    sa.Column("target_yield", sa.Float),
    sa.Column("grinder_model", sa.String),
    sa.Column("grinder_setting", sa.String),
    sa.Column("grinder_id", sa.String),
    sa.Column("coffee_name", sa.String),
    sa.Column("coffee_roaster", sa.String),
    sa.Column("bean_batch_id", sa.String),
    sa.Column("actual_dose_weight", sa.Float),
    sa.Column("actual_yield", sa.Float),
    sa.Column("drink_tds", sa.Float),
    sa.Column("drink_ey", sa.Float),
    sa.Column("enjoyment", sa.Float),
    sa.Column("espresso_notes", sa.Text),
    sa.Column("raw_workflow", sa.JSON),
    sa.Column("raw_annotations", sa.JSON),
    sa.Column("ingested_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
    sa.Index("idx_shots_user_id", "user_id"),
    sa.Index("idx_shots_started_at", "started_at"),
)

# Per-snapshot telemetry for a shot (pressure/flow/temperature/weight curves).
shot_samples = sa.Table(
    "shot_samples",
    metadata,
    sa.Column("shot_id", sa.String, sa.ForeignKey("shots.id", ondelete="CASCADE"), nullable=False),
    sa.Column("sample_time", sa.DateTime, nullable=False),
    sa.Column("seq", sa.Integer, nullable=False),  # ordinal within the shot, breaks timestamp ties
    sa.Column("elapsed_seconds", sa.Float),  # seconds since shot start
    sa.Column("machine_state", sa.String),
    sa.Column("machine_substate", sa.String),
    sa.Column("flow", sa.Float),
    sa.Column("pressure", sa.Float),
    sa.Column("target_flow", sa.Float),
    sa.Column("target_pressure", sa.Float),
    sa.Column("mix_temperature", sa.Float),
    sa.Column("group_temperature", sa.Float),
    sa.Column("target_mix_temperature", sa.Float),
    sa.Column("target_group_temperature", sa.Float),
    sa.Column("steam_temperature", sa.Float),
    sa.Column("profile_frame", sa.Integer),
    sa.Column("weight", sa.Float),
    sa.Column("weight_flow", sa.Float),
    sa.Column("volume", sa.Float),
    sa.PrimaryKeyConstraint("shot_id", "sample_time", "seq"),
    sa.Index("idx_shot_samples_shot_id", "shot_id"),
)
