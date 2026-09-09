-- espressolab schema
-- Run against the lab's existing Postgres + TimescaleDB database.
-- Requires the timescaledb extension to already be available (CREATE EXTENSION IF NOT EXISTS timescaledb;)

CREATE EXTENSION IF NOT EXISTS timescaledb;
CREATE EXTENSION IF NOT EXISTS pgcrypto; -- for gen_random_uuid()

-- ---------------------------------------------------------------------------
-- Lab members who can be picked as "who's brewing" on the touchscreen portal.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    display_name text NOT NULL UNIQUE,
    avatar_emoji text NOT NULL DEFAULT '☕',
    avatar_color text NOT NULL DEFAULT '#6f4e37',
    active       boolean NOT NULL DEFAULT true,
    created_at   timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- One row per shot pulled on the DE1, as reported by Decaid's REST API
-- (GET /api/v1/shots/{id}). Attribution comes from workflow.context set by
-- the portal via PUT /api/v1/workflow before the shot starts.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS shots (
    id                  text PRIMARY KEY,               -- Decaid shot id
    user_id             uuid REFERENCES users(id) ON DELETE SET NULL, -- resolved via context.extras.espressolab_user_id
    drinker_name        text,                            -- raw workflow.context.drinkerName (kept even if user_id can't be resolved)
    barista_name        text,
    started_at          timestamptz NOT NULL,
    created_at_decaid   timestamptz,
    updated_at_decaid   timestamptz,
    duration_seconds    numeric,
    stop_reason         text,
    profile_title       text,
    target_dose_weight  numeric,
    target_yield        numeric,
    grinder_model       text,
    grinder_setting     text,
    grinder_id          text,
    coffee_name         text,
    coffee_roaster      text,
    bean_batch_id       text,
    actual_dose_weight  numeric,
    actual_yield        numeric,
    drink_tds           numeric,
    drink_ey            numeric,
    enjoyment           numeric,
    espresso_notes      text,
    raw_workflow        jsonb,
    raw_annotations     jsonb,
    ingested_at         timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_shots_user_id ON shots(user_id);
CREATE INDEX IF NOT EXISTS idx_shots_started_at ON shots(started_at DESC);

-- ---------------------------------------------------------------------------
-- Per-snapshot telemetry for each shot (pressure/flow/temperature/weight
-- curves). This is the time-series hypertable.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS shot_samples (
    shot_id                  text NOT NULL REFERENCES shots(id) ON DELETE CASCADE,
    sample_time              timestamptz NOT NULL,
    seq                      integer NOT NULL,           -- ordinal within the shot, breaks timestamp ties
    elapsed_seconds          numeric,                    -- seconds since shot start, convenient for charting
    machine_state            text,
    machine_substate         text,
    flow                     double precision,
    pressure                 double precision,
    target_flow              double precision,
    target_pressure          double precision,
    mix_temperature          double precision,
    group_temperature        double precision,
    target_mix_temperature   double precision,
    target_group_temperature double precision,
    steam_temperature        double precision,
    profile_frame            integer,
    weight                   double precision,
    weight_flow              double precision,
    volume                   double precision,
    PRIMARY KEY (shot_id, sample_time, seq)
);

SELECT create_hypertable(
    'shot_samples', 'sample_time',
    chunk_time_interval => INTERVAL '7 days',
    if_not_exists => TRUE
);

CREATE INDEX IF NOT EXISTS idx_shot_samples_shot_id ON shot_samples(shot_id);
