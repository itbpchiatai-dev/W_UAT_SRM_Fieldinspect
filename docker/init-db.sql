-- Initialize PostgreSQL extensions
-- Docker image is pgvector/pgvector:pg16 — pgvector + standard pg_trgm
-- are AVAILABLE; enable below only what you actually use.
-- See AGENTS.md §2 Stack Defaults.

-- Default (always enable):
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- Conditional (uncomment when needed):
-- pg_trgm: full-text search / trigram similarity (incl. Thai)
-- CREATE EXTENSION IF NOT EXISTS "pg_trgm";

-- pgvector: AI embeddings / semantic search / RAG
--   Required if STACK_VARIANT involves vector RAG (docs/patterns/ai.md §7)
-- CREATE EXTENSION IF NOT EXISTS "vector";
