-- Run once against the control-db (vpic_meta database).
-- Holds the "which vpic_* database is currently live" pointer and a full
-- audit trail of every update attempt.

CREATE TABLE IF NOT EXISTS current_deployment (
    id              SMALLINT PRIMARY KEY DEFAULT 1 CHECK (id = 1),  -- singleton row
    db_name         TEXT,
    version         TEXT,
    released_on     DATE,
    promoted_at     TIMESTAMPTZ
);

-- Seed the singleton row if it doesn't exist yet.
INSERT INTO current_deployment (id, db_name, version, released_on, promoted_at)
VALUES (1, NULL, NULL, NULL, NULL)
ON CONFLICT (id) DO NOTHING;

CREATE TABLE IF NOT EXISTS update_history (
    id              BIGSERIAL PRIMARY KEY,
    version         TEXT NOT NULL,
    released_on     DATE,
    db_name         TEXT,
    status          TEXT NOT NULL CHECK (status IN ('success', 'failure')),
    error           TEXT,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at     TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_update_history_version ON update_history (version);
CREATE INDEX IF NOT EXISTS idx_update_history_status_finished
    ON update_history (status, finished_at DESC);

-- Optional addition to update_history for richer audit trail:
ALTER TABLE update_history ADD COLUMN IF NOT EXISTS validation_detail JSONB;