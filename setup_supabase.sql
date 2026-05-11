-- Run this in: Supabase dashboard → SQL Editor → paste → Run
-- Safe to run even if the table already partially exists.

-- Add all missing columns (IF NOT EXISTS means no error if column already exists)
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
  ADD COLUMN IF NOT EXISTS confirmed_at TIMESTAMPTZ;

-- Add the status CHECK constraint if it doesn't exist
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

-- Verify the result
SELECT column_name, data_type FROM information_schema.columns
WHERE table_name = 'subscribers' ORDER BY ordinal_position;
