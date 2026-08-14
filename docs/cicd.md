# docs/cicd.md

> CI/CD reference — GitHub Actions (default), with notes for GitLab CI and Azure DevOps
> Covers: pipeline stages, quality gates, deployment gates

---

## 1. Pipeline Overview

```
┌──────────┐   ┌──────────┐   ┌──────────┐
│   Lint   │ → │   Test   │ → │  Deploy  │
└──────────┘   └──────────┘   └──────────┘
     │              │              │
     ▼              ▼              ▼
  ruff/eslint   pytest/vitest  SSH → git pull →
  mypy/tsc      coverage 80%   docker compose up
  secrets scan                 -d --build
```

> Docker image **build จาก source บน server** ตอน deploy — ไม่มี image registry

**Trigger rules:**
- All branches/PRs → Lint + Test
- `main` branch → Lint + Test + Deploy staging
- Tag `v*.*.*` → Lint + Test + Deploy production (with manual approval)

---

## 2. GitHub Actions (Default)

### 2.1 Folder

```
.github/
└── workflows/
    ├── ci.yml              # lint + test (all PRs)
    ├── deploy-staging.yml  # auto deploy to staging (main)
    ├── deploy-prod.yml     # manual deploy to prod (tags)
    └── security.yml        # security scans (weekly + PR)
```

### 2.2 `ci.yml`

```yaml
name: CI

on:
  pull_request:
  push:
    branches: [main]

env:
  PYTHON_VERSION: '3.12'
  NODE_VERSION: '20'

concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true

jobs:
  backend-lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: ${{ env.PYTHON_VERSION }}
          cache: pip
      - name: Install dependencies
        working-directory: backend
        run: |
          pip install --upgrade pip
          pip install -e ".[dev]"
      - name: Run ruff
        working-directory: backend
        run: ruff check .
      - name: Run black
        working-directory: backend
        run: black --check .
      - name: Run mypy
        working-directory: backend
        run: mypy app

  backend-test:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:16-alpine
        env:
          POSTGRES_USER: test
          POSTGRES_PASSWORD: test
          POSTGRES_DB: test_db
        ports:
          - 5432:5432
        options: >-
          --health-cmd pg_isready
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: ${{ env.PYTHON_VERSION }}
          cache: pip
      - name: Install dependencies
        working-directory: backend
        run: pip install -e ".[dev]"
      - name: Enable Postgres extensions
        run: |
          PGPASSWORD=test psql -h localhost -U test -d test_db -c \
            'CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
             CREATE EXTENSION IF NOT EXISTS "pgcrypto";
             CREATE EXTENSION IF NOT EXISTS "pg_trgm";'
      - name: Run tests
        working-directory: backend
        env:
          TEST_DB_NAME: test_db
        run: pytest --cov-fail-under=80
      - name: Upload coverage
        uses: codecov/codecov-action@v4
        if: always()
        with:
          files: backend/coverage.xml
          flags: backend

  frontend-lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: ${{ env.NODE_VERSION }}
          cache: npm
          cache-dependency-path: frontend/package-lock.json
      - name: Install
        working-directory: frontend
        run: npm ci
      - name: Run ESLint
        working-directory: frontend
        run: npm run lint
      - name: TypeScript check
        working-directory: frontend
        run: npm run typecheck
      - name: Prettier check
        working-directory: frontend
        run: npm run format:check

  frontend-test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: ${{ env.NODE_VERSION }}
          cache: npm
          cache-dependency-path: frontend/package-lock.json
      - name: Install
        working-directory: frontend
        run: npm ci
      - name: Run tests
        working-directory: frontend
        run: npm run test:coverage

  secret-scan:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - name: Gitleaks
        uses: gitleaks/gitleaks-action@v2
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
```

### 2.3 Build

ไม่มี build stage แยกใน CI — production **build Docker image จาก source บน server**
ตอน deploy (`docker compose up -d --build` ผ่าน `deploy.sh`) ไม่มี image registry

CI ทำหน้าที่แค่ quality gate (lint + test) — ดู `ci.yml` (§2.2)

### 2.4 `deploy-staging.yml`

```yaml
name: Deploy Staging

on:
  push:
    branches: [main]

jobs:
  deploy:
    runs-on: ubuntu-latest
    environment:
      name: staging
      url: ${{ vars.STAGING_URL }}
    steps:
      - name: Deploy via SSH
        uses: appleboy/ssh-action@v1
        with:
          host: ${{ secrets.STAGING_HOST }}
          username: ${{ secrets.STAGING_USER }}
          key: ${{ secrets.STAGING_SSH_KEY }}
          script: |
            cd /opt/${PROJECT_SLUG:-app}
            ./deploy.sh
```

`deploy.sh` (อยู่ที่ root ของ repo) ทำ 3 ขั้น:
`git pull` → `docker compose up -d --build` → `docker compose exec backend alembic upgrade head`

### 2.5 `deploy-prod.yml`

```yaml
name: Deploy Production

on:
  push:
    tags: ['v*']

jobs:
  deploy:
    runs-on: ubuntu-latest
    environment:
      name: production          # required reviewers = manual approval gate
      url: ${{ vars.PRODUCTION_URL }}
    steps:
      - name: Deploy via SSH
        uses: appleboy/ssh-action@v1
        with:
          host: ${{ secrets.PROD_HOST }}
          username: ${{ secrets.PROD_USER }}
          key: ${{ secrets.PROD_SSH_KEY }}
          script: |
            cd /opt/${PROJECT_SLUG:-app}
            git fetch --tags
            git checkout ${{ github.ref_name }}
            docker compose up -d --build
            docker compose exec -T backend alembic upgrade head
            curl -fsS https://${{ vars.PRODUCTION_URL }}/health || exit 1
```

⚠️ Production deploy ใช้ **GitHub Environment with required reviewers** เพื่อ enforce manual approval

> DB เป็น centralized server (shared) — การ backup DB จัดการที่ระดับ DB server โดย IT
> ไม่ใช่ที่ app stack (ดู `docs/human/runbook.md`)

## 3. Quality Gates

Pipeline ต้อง **fail** ถ้า:

| Stage | Failure Conditions |
|---|---|
| Lint | ruff/eslint errors, mypy/tsc errors, prettier format mismatch |
| Test | any test failure, coverage < 80% backend / 70% frontend |
| Security scan | any secret detected, any critical/high CVE |
| Build | Dockerfile errors, image build failure |
| Deploy | health check fail post-deploy |

### 3.1 Logging Coverage Check

AGENTS.md Section 12 กำหนดว่าทุก endpoint/job/integration ต้องมี logging — enforce ผ่าน **code review checklist** (ไม่ใช่ automated CI check เพราะ static analysis ทำได้ยากสำหรับ async patterns)

**Code review checklist** (เพิ่มใน PR template):
```markdown
## Logging Checklist
- [ ] New mutation endpoints wire `UserActivityLogger.log()`
- [ ] New export/PII endpoints wire `UserActivityLogger.log_sensitive_read()` or `log_export()`
- [ ] New background jobs wire `SystemLogger.log_job()` start + end
- [ ] New integration calls wire `SystemLogger.log_integration()`
- [ ] New Claude API calls go through `call_claude_messages()` wrapper (not direct SDK)
```

เพิ่มไฟล์ `.github/pull_request_template.md` ใน repo:

```markdown
## Changes
<!-- Describe what changed -->

## Logging Checklist
- [ ] Mutation endpoints → `UserActivityLogger.log()`
- [ ] Export/PII endpoints → `UserActivityLogger.log_export()` / `log_sensitive_read()`
- [ ] Background jobs → `SystemLogger.log_job()` start + end
- [ ] Integration calls → `SystemLogger.log_integration()`
- [ ] Claude API calls → through `call_claude_messages()` wrapper
- [ ] N/A — no new endpoints/jobs/integrations in this PR

## Tests
- [ ] Unit tests added/updated
- [ ] Integration tests added/updated
```

**Override:** ห้าม override quality gates โดยไม่มี approval — ถ้า genuinely blocked โดย flaky test, ใช้ `pytest.mark.flaky` + ticket

---

## 4. Branch Protection (main)

ตั้งใน GitHub Settings → Branches → Branch protection rules:

- [x] Require pull request before merging
  - [x] Require approvals: 1
  - [x] Dismiss stale approvals when new commits pushed
- [x] Require status checks before merging
  - Required: `backend-lint`, `backend-test`, `frontend-lint`, `frontend-test`, `secret-scan`
- [x] Require branches to be up to date
- [x] Require conversation resolution
- [x] Require linear history
- [x] Do not allow bypassing the above settings (even for admins)

---

## 5. Environments + Secrets

ใน GitHub → Settings → Environments:

### staging

**Secrets:**
- `STAGING_HOST`, `STAGING_USER`, `STAGING_SSH_KEY`

**Variables:**
- `STAGING_URL`, `VITE_API_BASE_URL`, Azure AD config

### production

**Secrets:**
- `PROD_HOST`, `PROD_USER`, `PROD_SSH_KEY`

**Variables:**
- `PRODUCTION_URL`, `VITE_API_BASE_URL`, Azure AD config

**Required reviewers:** at least 1 senior engineer

**Deployment branches:** only tags matching `v*`

---

## 6. GitLab CI Variant

> ⚠️ ตัวอย่าง GitLab/Azure ด้านล่างยังเขียนแบบ build+push image — ถ้าใช้ platform นี้
> ให้ปรับเป็น build-on-host: ตัด stage `docker build`/`docker push` ออก, deploy = SSH +
> `./deploy.sh` (เหมือน §2.4-§2.5)

**`.gitlab-ci.yml`:**

```yaml
stages:
  - lint
  - test
  - build
  - deploy

variables:
  PYTHON_VERSION: "3.12"
  NODE_VERSION: "20"

# --- Lint ---
backend:lint:
  stage: lint
  image: python:${PYTHON_VERSION}-slim
  script:
    - cd backend
    - pip install -e ".[dev]"
    - ruff check .
    - black --check .
    - mypy app

# --- Test ---
backend:test:
  stage: test
  image: python:${PYTHON_VERSION}-slim
  services:
    - postgres:16-alpine
  variables:
    POSTGRES_USER: test
    POSTGRES_PASSWORD: test
    POSTGRES_DB: test_db
    TEST_DB_NAME: test_db
  script:
    - cd backend
    - pip install -e ".[dev]"
    - pytest --cov-fail-under=80

# --- Build ---
build:backend:
  stage: build
  image: docker:24
  services:
    - docker:24-dind
  only:
    - main
    - tags
  script:
    - docker login -u $CI_REGISTRY_USER -p $CI_REGISTRY_PASSWORD $CI_REGISTRY
    - export PROJECT_SLUG=$(grep '^PROJECT_SLUG=' project.config | cut -d= -f2 || echo "$CI_PROJECT_NAME")
    - docker build -t $CI_REGISTRY_IMAGE/${PROJECT_SLUG}-backend:$CI_COMMIT_SHORT_SHA backend
    - docker push $CI_REGISTRY_IMAGE/${PROJECT_SLUG}-backend:$CI_COMMIT_SHORT_SHA

# --- Deploy ---
deploy:staging:
  stage: deploy
  only:
    - main
  environment:
    name: staging
  script:
    - # SSH deploy
deploy:production:
  stage: deploy
  only:
    - tags
  when: manual
  environment:
    name: production
  script:
    - # SSH deploy
```

---

## 7. Azure DevOps Variant

**`azure-pipelines.yml`:**

```yaml
trigger:
  branches:
    include: [main]
  tags:
    include: ['v*']

variables:
  pythonVersion: '3.12'
  nodeVersion: '20'

stages:
  - stage: Lint_Test
    jobs:
      - job: Backend
        steps:
          - task: UsePythonVersion@0
            inputs:
              versionSpec: $(pythonVersion)
          - script: |
              cd backend
              pip install -e ".[dev]"
              ruff check .
              mypy app
              pytest --cov-fail-under=80

      - job: Frontend
        steps:
          - task: NodeTool@0
            inputs:
              versionSpec: $(nodeVersion)
          - script: |
              cd frontend
              npm ci
              npm run lint
              npm run typecheck
              npm run test:coverage

  - stage: Build
    dependsOn: Lint_Test
    condition: succeeded()
    jobs:
      - job: BuildImages
        steps:
          - task: Docker@2
            inputs:
              command: buildAndPush
              repository: $(projectSlug)-backend
              tags: |
                $(Build.SourceVersion)
                latest

  - stage: DeployStaging
    condition: and(succeeded(), eq(variables['Build.SourceBranchName'], 'main'))
    jobs:
      - deployment: Staging
        environment: staging
        strategy:
          runOnce:
            deploy:
              steps: [...]

  - stage: DeployProduction
    condition: and(succeeded(), startsWith(variables['Build.SourceBranch'], 'refs/tags/v'))
    jobs:
      - deployment: Production
        environment: production  # configure approval here
        strategy:
          runOnce:
            deploy:
              steps: [...]
```

---

## 8. Deployment Notification (Slack/Teams)

Optional but recommended. Example (Slack):

```yaml
- name: Notify deployment
  if: always()
  uses: slackapi/slack-github-action@v1
  with:
    payload: |
      {
        "text": "Deploy ${{ job.status }} — ${{ github.ref_name }} → ${{ vars.PRODUCTION_URL }}"
      }
  env:
    SLACK_WEBHOOK_URL: ${{ secrets.SLACK_WEBHOOK_URL }}
```

---

## 9. Rollback

### 9.1 Quick Rollback (กลับไป tag ก่อนหน้า)

```bash
ssh prod-host
cd /opt/${PROJECT_SLUG}
git fetch --tags
git checkout v1.2.2             # tag ก่อนหน้า
docker compose up -d --build    # rebuild จาก code เก่า
curl -fsS https://<production-url>/health
```

### 9.2 With DB Migration Rollback

ถ้า deploy ที่ fail มีการ apply migration:

```bash
# 1. downgrade migration ก่อน (ระหว่างที่ code ใหม่ยังอ่าน schema ได้)
docker compose exec backend alembic downgrade -1
# 2. แล้วค่อย checkout + rebuild code เก่า (§9.1)
```

---

## 10. Versioning

ใช้ **Semantic Versioning** (`v<major>.<minor>.<patch>`):

- `v1.0.0` — initial release
- `v1.0.1` — bug fix
- `v1.1.0` — new feature, backward-compatible
- `v2.0.0` — breaking change

Create tag:

```bash
git tag -a v1.2.3 -m "Release 1.2.3"
git push origin v1.2.3
```

---

## 11. Hard Rules

1. **ห้าม disable quality gates** ใน main branch
2. **ห้าม deploy ก่อนผ่าน CI**
3. **ห้าม commit secrets** — pipeline ต้อง fail ถ้าเจอ
4. **ห้าม use `latest` tag** ใน production deploy
5. **ห้าม manual deploy** โดยไม่ผ่าน pipeline ยกเว้น emergency (document ใน runbook)
6. **Production deploy ต้องผ่าน manual approval**
7. **DB migration ใน production ต้อง backup ก่อนเสมอ**

---

## 12. Quick Reference

| Task | Action |
|---|---|
| Open PR | Auto-triggers `ci.yml` |
| Merge to main | Triggers `deploy-staging.yml` → SSH → `./deploy.sh` |
| Release to prod | `git tag v1.2.3 && git push --tags` → manual approval |
| Roll back staging | Re-run previous build's deploy job |
| Roll back prod | Checkout previous tag, redeploy, downgrade migration if needed |
| Skip CI temporarily (admin only) | `[skip ci]` in commit msg — discouraged |
