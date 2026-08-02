-- P3.13: record the closed sentinel verdict reason for every audit row.
-- This migration is idempotent and is intentionally not run by tests.

ALTER TABLE sentinel_audit ADD COLUMN IF NOT EXISTS reason text;

UPDATE sentinel_audit
SET reason = CASE
  WHEN decision = 'allow' THEN 'classified_normal'
  ELSE 'sensitive_category'
END
WHERE reason IS NULL;

ALTER TABLE sentinel_audit ALTER COLUMN reason SET NOT NULL;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conname = 'p313_sentinel_audit_reason_check'
      AND conrelid = 'sentinel_audit'::regclass
      AND contype = 'c'
  ) THEN
    ALTER TABLE sentinel_audit
      ADD CONSTRAINT p313_sentinel_audit_reason_check
      CHECK (reason IN (
        'classified_normal', 'sensitive_category', 'malformed_output',
        'unknown_category', 'low_confidence', 'sentinel_unavailable', 'test_stub'
      ));
  END IF;
END $$;
