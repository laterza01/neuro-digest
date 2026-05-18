-- Run this in: Supabase dashboard → SQL Editor → paste → Run
-- Safe to run even if the table already partially exists.

-- ── subscribers table ─────────────────────────────────────────────────────────

ALTER TABLE subscribers
  ADD COLUMN IF NOT EXISTS status       TEXT        DEFAULT 'pending',
  ADD COLUMN IF NOT EXISTS topics       TEXT[]      DEFAULT ARRAY[
    'Multiple Sclerosis',
    'Stroke',
    'Parkinson''s Disease',
    'Epilepsy',
    'Dementia',
    'Headache',
    'Neuromuscular',
    'Neuro-oncology',
    'Neuroinflammation',
    'Movement Disorders',
    'Neurocritical Care',
    'Neurogenetics'
  ],
  ADD COLUMN IF NOT EXISTS created_at   TIMESTAMPTZ DEFAULT NOW(),
  ADD COLUMN IF NOT EXISTS confirmed_at TIMESTAMPTZ,
  -- Timezone-aware delivery: IANA timezone string auto-detected at signup
  -- (e.g. 'Europe/Rome', 'America/New_York', 'Asia/Tokyo')
  ADD COLUMN IF NOT EXISTS timezone     TEXT        DEFAULT 'Europe/Rome';

-- Status CHECK constraint
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.table_constraints
    WHERE table_name = 'subscribers' AND constraint_name = 'subscribers_status_check'
  ) THEN
    ALTER TABLE subscribers
      ADD CONSTRAINT subscribers_status_check
      CHECK (status IN ('pending', 'confirmed', 'unsubscribed'));
  END IF;
END $$;

-- Make sure RLS is on (service role key bypasses it anyway)
ALTER TABLE subscribers ENABLE ROW LEVEL SECURITY;

-- ── digests table — add columns if the table already exists ──────────────────
-- (If the table doesn't exist yet it will be created on first run by digest.py)
DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.tables WHERE table_name = 'digests'
  ) THEN
    ALTER TABLE digests ADD COLUMN IF NOT EXISTS edition_num  INT;
    -- Structured JSON stored alongside HTML so every hourly run can apply
    -- per-subscriber topic filtering without re-running Claude synthesis
    ALTER TABLE digests ADD COLUMN IF NOT EXISTS digest_json TEXT;
  END IF;
END $$;

-- ── sends_log — one row per (subscriber, digest) to prevent double-sending ────
CREATE TABLE IF NOT EXISTS sends_log (
  id         BIGSERIAL    PRIMARY KEY,
  email      TEXT         NOT NULL,
  digest_id  BIGINT       NOT NULL,   -- references digests.id
  sent_at    TIMESTAMPTZ  DEFAULT NOW(),
  UNIQUE (email, digest_id)           -- deduplication key
);

CREATE INDEX IF NOT EXISTS sends_log_digest_id_idx ON sends_log (digest_id);
CREATE INDEX IF NOT EXISTS sends_log_email_idx     ON sends_log (email);

ALTER TABLE sends_log ENABLE ROW LEVEL SECURITY;

-- ── guidelines_log — already in use, keep as-is ───────────────────────────────
CREATE TABLE IF NOT EXISTS guidelines_log (
  id             BIGSERIAL    PRIMARY KEY,
  macro_topic    TEXT         NOT NULL,
  specific_topic TEXT         NOT NULL,
  sent_at        TIMESTAMPTZ  DEFAULT NOW()
);

-- ── Verify ────────────────────────────────────────────────────────────────────
SELECT column_name, data_type FROM information_schema.columns
WHERE table_name = 'subscribers' ORDER BY ordinal_position;
