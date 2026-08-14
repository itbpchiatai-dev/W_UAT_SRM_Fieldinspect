# docs/human/STACK_DEVIATIONS.md

> Log of intentional deviations from the default stack defined in `AGENTS.md`
>
> Every entry must include: date, what changed, why, by whom, and roll-back plan.

---

## Default Stack Reference (snapshot)

For full default stack, see [`AGENTS.md`](../../AGENTS.md) Section 2.

Quick reference:

| Layer | Default |
|---|---|
| Backend | FastAPI + Python 3.12+ |
| Frontend | React 18+ + TypeScript 5+ + Vite |
| Database | PostgreSQL 16+ + SQLAlchemy 2.0 async |
| Auth | Azure AD SSO + Local (bcrypt + JWT) |
| AI | Anthropic Claude API |
| Deployment | Docker (multi-stage) |
| Scheduler | APScheduler |

---

## How to Add a Deviation Entry

1. Copy the template below
2. Fill in all fields honestly
3. Get explicit user confirmation before merging the change
4. Update relevant `docs/*.md` files to reflect the new tech
5. Commit with message: `chore(stack): add deviation — <one-line summary>`

---

## Entry Template

```markdown
### YYYY-MM-DD — <Short Title>

**Layer:** <e.g., Backend / Frontend / DB / Auth / Deployment>
**Default:** <what the default stack says>
**Deviation:** <what was actually chosen>
**Status:** Active | Reverted | Superseded by <entry>

**Context:**
<Why deviating? What problem does the default fail to solve?>

**Alternatives Considered:**
- <Option A> — pros/cons
- <Option B> — pros/cons

**Decision:**
<What was chosen and why>

**Trade-offs / Risks:**
- <Risk 1 and mitigation>
- <Risk 2 and mitigation>

**Rollback Plan:**
<How to revert if this turns out wrong>

**Affected Files:**
- <list of files/modules touched>

**Approved by:** <name(s)>
```

---

## Entries

<!-- Add new entries at the top (reverse chronological) -->

### (No deviations yet)

This project follows the default stack as defined in `AGENTS.md`. If/when deviations are introduced, they will be logged above this line.

---

## Common Acceptable Deviations (Pre-Approved Reasoning)

These deviations have pre-validated rationale; you still must add an entry above when used:

| Deviation | When Acceptable |
|---|---|
| **TimescaleDB** instead of/in addition to Postgres | Time-series data dominates the workload (e.g., sensor readings, market price ticks at high frequency) |
| **Redis** added as cache | Need cross-process cache sharing or distributed locks; Postgres caching insufficient |
| **Celery / Arq / RQ** instead of APScheduler | Background tasks need distributed workers, retries with persistent state, or > 1 process |
| **Streamlit** for internal dashboards | V1 prototype only; small audience (< 10 users); plan to migrate to React if scope grows |
| **Next.js** instead of Vite SPA | Public-facing site requires SSR for SEO; or product team wants RSC |
| **Postgres extensions beyond default** (e.g., PostGIS, TimescaleDB) | Domain-specific need clearly documented |

---

## Deviations NOT Acceptable (Discuss First)

These would require executive-level discussion before merging:

- Replacing Python backend with another language (Go/Node/Java/etc.)
- Replacing PostgreSQL with a different database (MongoDB, MySQL, etc.)
- Replacing Azure AD with another SSO provider for internal users
- Bypassing dual-auth model (e.g., using social login for external users)
- Removing the AGENTS.md / CLAUDE.md / project.config pattern
- Self-hosted AI instead of Claude API (cost, support, capability trade-offs)

If genuinely needed, write a separate proposal document and engage stakeholders before committing to a path.

---

## Periodic Review

Review this file:
- Whenever onboarding a new team member to this project
- Every quarter: are deviations still justified? Should any be reverted now that defaults have evolved?
- When the default standard (`AGENTS.md`) is updated, re-evaluate whether existing deviations are still required

---

## AI Maintenance Notes

**Trigger for AI to add an entry:**
- User requests using a tech that's not in the default stack
- AI suggests a deviation and user approves

**AI must NOT:**
- Silently introduce a deviation
- Add a deviation entry without explicit user approval
- Use the "Common Acceptable Deviations" list to justify auto-adding without confirmation

When in doubt, ask the user.

## pgvector (AI Embeddings)

**Status:** Default — ทุก project ใช้ `pgvector/pgvector:pg16`

**เหตุผล:** Chiatai ใช้ Claude API และมีโอกาสสูงที่ project จะต้องการ AI semantic search
ติดตั้งครั้งเดียวบน production DB server แล้วทุก project ใช้ได้เลย

**IT ต้องทำ (ครั้งเดียว):**
ติดตั้ง pgvector บน production DB server แล้วรัน:
```sql
CREATE EXTENSION IF NOT EXISTS "vector";
```

**ใช้ใน project:**
```python
# pip install pgvector
from pgvector.sqlalchemy import Vector
embedding = Column(Vector(1536))  # OpenAI/Claude embedding dimension
```

