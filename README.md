# ZeroFounder

ZeroFounder는 이미 정해진 제품을 자동 관리하는 도구가 아닙니다. 공개 시장 신호에서 반복되는 문제를 찾고, 근거·독창성·유통 가능성을 통과한 사업 아이템만 선택한 뒤 MVP를 만들고 검증하는 자율 스타트업 운영 시스템입니다.

초기 상태에는 선택된 제품이나 서비스명이 없습니다. `company/state.json`은 `DISCOVERY`, `venture/venture.json`은 `unselected`로 시작합니다.

## 운영 원칙

- 실제 저장된 evidence ID만 사용하며 모델은 URL이나 사용자·방문·매출 수치를 만들 수 없습니다.
- GitHub 외부 출처를 포함한 독립 근거와 유통 경로가 없으면 아이디어를 선택하지 않습니다.
- AI 사용 자체는 차별화로 인정하지 않으며 규칙·정보 구조·업무 흐름 개선을 먼저 검토합니다.
- 제품 코드와 의존성은 자동 병합하지 않습니다.
- 외부 게시, DM, 이메일, 계정 생성, 결제, 광고, 개인정보 수집은 자동화하지 않습니다.
- `founder/results.json`은 검증된 인간 전용 증거입니다. bot과 AI 기록은 실적으로 계산하지 않습니다.

## 생애주기

```text
DISCOVERY → EVIDENCE_VALIDATION → IDEA_EVALUATION → DISTRIBUTION_CHECK
→ IDEA_SELECTED → FOUNDER_APPROVAL → MVP_PLANNING
→ INFRASTRUCTURE_SELECTION → MVP_BUILDING → PRE_LAUNCH
→ DISTRIBUTION_REQUIRED → VALIDATION_RUNNING → OPERATING
→ GROWTH_EXPERIMENT → PIVOT_REVIEW → PIVOTING
```

배포 직후 운영 성공이나 피벗을 판단하지 않습니다. 실제 노출, 사용자 신호, 피드백, 실험, 검증 기간이 strategy 기준을 충족해야 합니다.

## 에이전트 역할

CEO, Market Scout, Researcher, Venture Analyst, Cliché Critic, Product Manager, Builder, Designer, Growth Manager, Customer Analyst, Data Analyst, Auditor, Secretary 중 실행당 하나만 주 역할을 맡습니다. 각 역할은 `agents/prompts/`의 공통 안전 규칙과 역할별 지침을 따릅니다.

## 이벤트 기반 실행

`agent.yml`은 UTC 기준 매 2시간의 17분에 실행되지만 preflight가 먼저 다음 변화를 검사합니다.

- 분석할 새 시장 신호 또는 강한 단일 신호
- 새 Issue·피드백·승인 명령
- 제품 commit 또는 핵심 metrics 변경
- 실험 검토일
- 일일·주간 검토일
- 수동 실행

변화가 없으면 모델 호출, 상태 변경, 보고서, 커밋 없이 `no_op`로 끝납니다. 장기 중복 방지는 `company/checkpoints.json`의 idempotency key를 사용합니다. Actions artifact는 같은 workflow 안의 job 전달에만 사용합니다.

## 로컬 실행 — macOS

Python 3.12와 Node.js 22를 설치한 뒤 실행합니다.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
npm ci
python -m scripts.check_all
```

개발 화면은 `npm run dev`로 실행합니다. 초기 화면은 특정 미니 도구가 아니라 시장 조사 진행 상태만 표시합니다.

## GitHub 저장소 생성과 push

이 폴더에는 Phase별 로컬 커밋이 생성되어 있습니다. GitHub에서 빈 저장소를 만든 뒤 안내된 원격 URL을 사용합니다.

```bash
git remote add origin https://github.com/OWNER/REPOSITORY.git
git push -u origin main
```

개인 PAT를 저장소 파일이나 Repository Variable에 넣지 마세요. Actions에서는 GitHub가 실행별로 제공하는 `GITHUB_TOKEN`만 사용합니다.

## 필수 GitHub 설정

1. Settings → Pages → Build and deployment → Source를 **GitHub Actions**로 선택합니다.
2. Settings → Actions → General → Workflow permissions에서 저장소 작업에 필요한 읽기/쓰기 권한과 **Allow GitHub Actions to create and approve pull requests**를 허용합니다.
3. Actions에서 **ZeroFounder Agent**를 수동 실행하고 `setup-labels`를 선택합니다.
4. 공개 저장소의 무료 Actions·Pages 범위를 권장합니다.

workflow 최상위 권한은 `contents: read`입니다. 모델, Issue, 브랜치, PR, dispatch, Pages job이 필요한 최소 권한만 따로 받습니다. `pull_request_target`은 사용하지 않습니다.

## Repository Variables

| 이름 | 기본값 | 설명 |
|---|---:|---|
| `AUTONOMY_LEVEL` | `1` | 0 조언만, 1 승인된 제품 작업만 PR, 2 낮은 위험 자체 작업도 PR |
| `GITHUB_MODEL` | 비어 있음 | 카탈로그에서 확인할 우선 채팅 모델 |
| `GITHUB_FALLBACK_MODELS` | `openai/gpt-4.1-mini,openai/gpt-4.1` | 순차 fallback |
| `GITHUB_EMBEDDING_MODEL` | 자동 선택 | 사용 가능한 임베딩 모델 |
| `DAILY_MODEL_CALL_LIMIT` | `8` | chat과 embedding 합산 soft limit |
| `MAX_FILES_PER_ACTION` | `12` | 한 행동의 최대 파일 수 |
| `MAX_FILE_CHARS` | `20000` | 파일당 생성 문자 수 |
| `MAX_TOTAL_OUTPUT_CHARS` | `60000` | 실행당 총 출력 문자 수 |
| `MIN_UNIQUE_SIGNALS` | `12` | 기본 탐색 신호 수 |
| `MIN_SOURCE_TYPES` | `3` | 탐색 source type 수 |
| `MIN_EVIDENCE_PER_PROBLEM` | `3` | 최종 문제 독립 근거 수 |
| `MAX_SIGNAL_AGE_DAYS` | `180` | 기본 최근성 범위 |
| `MIN_EVIDENCE_QUALITY` | `0.65` | 규칙 기반 근거 품질 |
| `VALIDATION_PERIOD_DAYS` | `14` | 피벗 전 최소 검증 기간 |
| `MIN_DISTRIBUTION_ACTIVITIES` | `2` | 인간 노출 활동 |
| `MIN_USER_OR_VISIT_SIGNALS` | `10` | 실제 사용자 또는 방문 신호 |
| `MIN_FEEDBACK_ITEMS` | `3` | 실제 피드백 수 |
| `MIN_GROWTH_EXPERIMENTS` | `2` | 완료 실험 수 |

같은 값은 `company/strategy.json`에서도 관리하며 Repository Variable이 우선합니다.

## 첫 시장 조사

1. Actions → **Collect Market Signals** → Run workflow를 실행합니다.
2. `signals/raw/`에 새 고유 신호가 생겼는지 확인합니다.
3. Actions → **ZeroFounder Agent** → `agent`를 수동 실행합니다.
4. `research/inbox/`에 직접 조사한 Markdown이나 JSON을 추가할 수 있습니다.

`signals/sources.json`의 source pack으로 developer, education, local-life, small-business, productivity, public-data, offline-coordination을 켜거나 끌 수 있습니다. GitHub와 Hacker News만으로 최종 아이디어를 선택할 수 없습니다.

## 사업 아이템 승인

기본 자율 수준에서는 `founder-approval` Issue가 생성됩니다. write 권한이 있는 인간이 독립된 한 줄로 명령합니다.

```text
/approve
/reject
/revise
/pause
/resume
/pivot
```

문장 안 명령, 추가 셸 문자열, 권한 없는 사용자, PR 댓글은 무시됩니다.

## AI가 만든 PR 검토

PR 전에 agent workflow 자체가 lint, typecheck, Python·TypeScript 테스트, build, audit, security 검사를 수행합니다. PR 생성 후 `dispatch-quality-check` job이 기본 브랜치의 `quality-check.yml`을 명시적으로 `workflow_dispatch`합니다.

quality workflow는 입력 SHA와 실제 PR head SHA를 다시 비교합니다. dispatch 실패는 `ci_not_started`, 시작 후에는 `awaiting_ci_approval`, 별도 CI 성공 후에만 `ready_for_human_review`입니다.

`GITHUB_TOKEN`으로 생성된 PR은 GitHub 보안 정책에 따라 후속 workflow에 인간 승인이 필요할 수 있습니다. 승인 대기는 성공이 아니며 개인 PAT를 기본 해결책으로 사용하지 않습니다.

## 의존성 변경

AI는 manifest와 lockfile을 수정할 수 없습니다. 정확한 버전, 이유, 표준 기능 대안, 라이선스, 보안, bundle·유지보수 영향을 담은 `DependencyProposal` Issue만 생성합니다. 검증된 창업자의 `/approve` 후 고정 스크립트가 정확한 패키지만 적용하며 audit와 전체 검사를 통과해야 의존성 PR을 생성합니다.

## 유통과 검증

최종 선택 전 `founder/tasks.md`, `founder/outreach-plan.md`, `founder/posting-pack.md`가 준비됩니다. AI는 게시하지 않습니다. 창업자가 실제로 수행한 결과만 `founder/results.json`에 직접 commit하거나 권한 검증된 전용 Issue로 기록합니다.

bot, agent, 모델이 작성한 결과는 검증 실적이 아닙니다. 개인정보 보호형 analytics가 연결되지 않으면 방문자 수는 `null`이며 추정하지 않습니다.

## 인프라와 배포

`INFRASTRUCTURE_SELECTION`에서 다음을 비교합니다.

- GitHub Pages: 정적 자산, 브라우저 계산, Issue 피드백
- Cloudflare Pages: 향후 인간 승인 후 연결 가능한 정적 provider
- Cloudflare Pages + Workers + D1: 익명 저장·API·구조화 데이터가 핵심 검증에 필수일 때만 제안

초기 버전에서 실제 배포되는 provider는 GitHub Pages뿐입니다. Cloudflare는 interface, schema, 문서, 승인 절차만 있으며 계정·Worker·D1을 만들거나 배포하지 않습니다.

## 운영, 일시정지, 피벗

- `/pause`는 이전 단계를 보존하고 모델 호출을 중단합니다.
- `/resume`은 보존 단계로 복귀합니다.
- `/pivot`은 검증 기간·노출·사용자 신호·피드백·실험 조건을 모두 충족한 경우에만 검토됩니다.
- 기존 venture와 실험 데이터는 삭제하지 않고 버전으로 보존합니다.

일일 보고서는 `reports/YYYY-MM-DD.md`, 결정 기록은 append-only `company/decisions.jsonl`, 상태와 지표는 `company/`에서 확인합니다. 동일 보고서와 변화 없는 no-op 커밋은 생성하지 않습니다.

## 무료 운영 범위와 알려진 한계

- GitHub Free의 정책과 GitHub Models 제공량은 계정·저장소에 따라 달라질 수 있습니다.
- GitHub Trending 공식 API가 없어 저장소 검색 기반 proxy만 사용합니다.
- 공개 RSS는 제공자가 중단하거나 형식을 바꾸면 해당 source만 실패합니다.
- GitHub Discussions 수집은 활성화되고 접근 가능한 설정 저장소에 한정됩니다.
- GitHub Pages는 서버 저장, 인증, 비밀 처리, 실시간 API를 제공하지 않습니다.
- 초기에는 의도적으로 선택 제품이 없어 venture-specific 핵심 기능 테스트도 없습니다. Builder는 MVP 기능과 테스트를 같은 PR에 추가해야 합니다.
- Cloudflare 실제 배포는 구현하지 않았습니다. 인간 승인과 계정 설정이 필요하기 때문입니다.
- 웹 방문 분석은 기본 비활성화되어 방문 수를 제공하지 않습니다.

## 문제 해결

- 모델이 실행되지 않음: preflight 사유, 사용량 한도, `models: read`, catalog fallback을 확인합니다.
- `ci_not_started`: Actions 쓰기 권한과 기본 브랜치의 `quality-check.yml` 존재 여부를 확인합니다.
- `awaiting_ci_approval`: PR의 Actions 승인 배너를 인간이 확인합니다.
- Pages 404: Pages source가 GitHub Actions인지와 deploy artifact를 확인합니다. project/user Pages base path 및 `404.html` fallback이 포함되어 있습니다.
- 신호가 없음: source pack URL 오류는 전체 실패가 아니므로 collector 출력의 `source_errors`를 확인합니다.
- 직접 push 실패: branch protection이 자동 상태·신호 commit을 막는지 확인하고 변경을 PR로 검토합니다.

## 보안

전체 금지 규칙은 `company/constitution.md`에 있습니다. 외부 텍스트는 데이터일 뿐 명령이 아니며, 비밀값·개인정보·허위 수치·자동 외부 접촉·자동 병합·자동 릴리스·파일 삭제를 금지합니다.
