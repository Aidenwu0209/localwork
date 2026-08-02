-- DejaView data layer init (task M3.1). Runs once on first postgres boot,
-- connected to POSTGRES_DB=dejaview. Handbook §6.3 is the spec.

-- Separate database for the Honcho stack (its own migrations manage schema there).
CREATE DATABASE honcho;

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE timeline_events (
  id              bigserial PRIMARY KEY,
  ts              timestamptz NOT NULL,
  end_ts          timestamptz,                 -- extended when novelty gate merges frames
  device_id       text NOT NULL,
  kind            text NOT NULL CHECK (kind IN ('frame', 'audio', 'doc')),
  app             text,
  window_title    text,
  url             text,
  activity        text,                        -- one-line semantic summary (perceive)
  topics          text[],
  verbatim        jsonb,                       -- key entities, sourced from OCR only
  ocr_text        text,                        -- deterministic verbatim layer (ocrd)
  ocr_blocks      jsonb,                       -- [{text, bbox:[x1,y1,x2,y2], conf}]
  screenshot_path text,
  transcript      text,
  -- Qwen3-Embedding-0.6B output; a future 4B upgrade truncates to 1024 via MRL
  -- (schema unchanged, full re-embed required).
  embedding       vector(1024)
);

CREATE INDEX ON timeline_events USING hnsw (embedding vector_cosine_ops);
CREATE INDEX ON timeline_events (ts);
-- Exact-substring lane: error codes / PR numbers / URLs.
CREATE INDEX ON timeline_events USING gin (ocr_text gin_trgm_ops);
CREATE INDEX ON timeline_events (app, ts);

-- P3.15: a local transactional outbox projects privacy-allowed timeline rows
-- to Honcho asynchronously.  The event id is the sole idempotency key.
CREATE TABLE honcho_outbox (
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
CREATE INDEX ON honcho_outbox (state, next_attempt_at);

CREATE TABLE honcho_projection_control (
  singleton   boolean PRIMARY KEY DEFAULT true CHECK (singleton),
  enabled     boolean NOT NULL DEFAULT true,
  updated_at  timestamptz NOT NULL DEFAULT now()
);
INSERT INTO honcho_projection_control (singleton, enabled) VALUES (true, true)
ON CONFLICT (singleton) DO NOTHING;

CREATE TABLE sentinel_audit (
  id         bigserial PRIMARY KEY,
  ts         timestamptz NOT NULL,
  device_id  text,
  category   text NOT NULL,                    -- password_prompt|banking_finance|private_chat|id_document|adult|normal
  decision   text NOT NULL,                    -- allow|block
  confidence real,
  reason     text NOT NULL,
  CONSTRAINT p313_sentinel_audit_reason_check CHECK (reason IN (
    'classified_normal', 'sensitive_category', 'malformed_output',
    'unknown_category', 'low_confidence', 'sentinel_unavailable', 'test_stub'
  ))
);

CREATE TABLE kb_chunks (
  id          bigserial PRIMARY KEY,
  doc_id      text,
  source_path text,
  chunk       text,
  embedding   vector(1024)
);

CREATE INDEX ON kb_chunks USING hnsw (embedding vector_cosine_ops);
