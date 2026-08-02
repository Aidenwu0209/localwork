-- P3.15: idempotent local transactional outbox for Honcho projection.
-- Run against the dejaview database.  No timeline content is copied here; the
-- application constructs the closed, allowlisted JSON projection.

CREATE TABLE IF NOT EXISTS honcho_outbox (
  event_id          bigint PRIMARY KEY REFERENCES timeline_events(id) ON DELETE CASCADE,
  payload           jsonb NOT NULL,
  session_id        text NOT NULL,
  state             text NOT NULL DEFAULT 'pending'
                    CHECK (state IN ('pending', 'sending', 'sent', 'failed')),
  attempt_count     integer NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
  next_attempt_at   timestamptz NOT NULL DEFAULT now(),
  lease_expires_at  timestamptz,
  last_error        text,
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now(),
  sent_at           timestamptz
);

CREATE INDEX IF NOT EXISTS honcho_outbox_state_due_idx
  ON honcho_outbox (state, next_attempt_at);

CREATE TABLE IF NOT EXISTS honcho_projection_control (
  singleton   boolean PRIMARY KEY DEFAULT true CHECK (singleton),
  enabled     boolean NOT NULL DEFAULT true,
  updated_at  timestamptz NOT NULL DEFAULT now()
);

INSERT INTO honcho_projection_control (singleton, enabled) VALUES (true, true)
ON CONFLICT (singleton) DO NOTHING;
