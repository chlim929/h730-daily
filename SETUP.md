# 일력 자동 게시 — 설정 안내

| 시간(KST) | 워크플로 | 하는 일 |
|---|---|---|
| 07:45 | `prepare` | 오늘 H:730 레터에서 문장·출처 추출 → 카드 PNG → `posts/` 커밋 → 검토 Issue 생성 |
| 09:30 | (나) | Issue의 검토 링크로 수정 → `posts/날짜.png` 덮어쓰기, 캡션은 `posts/날짜.json` 편집 |
| 16:00 | `publish` | PNG → JPEG(1080×1350) → 인스타 게시 → `posts/done/` 이동 → Issue 닫기 |
| 월 09:00 | `refresh-token` | 인스타 장기 토큰 갱신 → Secret `IG_ACCESS_TOKEN` 교체 |

GitHub 예약 실행은 몇 분~수십 분 늦게 시작될 수 있어요.

## GitHub 웹에서 할 일 (순서대로)

### 1. GitHub Pages 켜기
**Settings → Pages → Build and deployment → Source: Deploy from a branch → Branch: `main` / `(root)` → Save**
몇 분 뒤 https://chlim929.github.io/h730-daily/maker.html 이 열리면 완료.

### 2. 인스타 Secret 등록
**Settings → Secrets and variables → Actions → New repository secret**
- `IG_USER_ID`
- `IG_ACCESS_TOKEN` (장기 토큰, `IGAA…`)
- `IG_APP_SECRET`

### 3. 토큰 갱신용 `GH_PAT` 만들기
1. 프로필 사진 → **Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token**
2. Token name: `h730-daily secrets`, Expiration: 원하는 기간(최대 1년 — 만료일을 달력에 적어두세요)
3. Repository access: **Only select repositories → `chlim929/h730-daily`**
4. Permissions → Repository permissions → **Secrets: Read and write** (나머지는 그대로)
5. Generate token → 나온 값(`github_pat_…`)을 복사
6. 저장소 **Settings → Secrets and variables → Actions → New repository secret** → 이름 `GH_PAT`, 값 붙여넣기

### 4. 처음 테스트
1. **Actions → prepare → Run workflow** → date에 `2026-10-29` 입력 → Run
   → 끝나면 **Issues**에 `[검토] 2026-10-29 일력` 이 생기고 `posts/`에 PNG·JSON이 커밋됨
2. **Actions → publish → Run workflow** → date `2026-10-29`, **dry_run 체크** → Run
   → 로그에 캡션과 JPEG 변환 결과만 나오고 게시는 안 함
3. 실제로 올려 보려면 dry_run 체크 없이 다시 실행 (**인스타에 진짜 게시됨**)
4. **Actions → refresh-token → Run workflow** → "갱신 완료" 확인

여기까지 되면 다음 날부터 자동으로 돌아가요.

## 매일 검토(09:30)
- Issue의 **제작기에서 열기** 링크 → 크기·자간 조정 → **PNG로 저장**
- `일력_2026-10-29.png` → 이름을 `2026-10-29.png` 로 바꿔서 **posts 폴더 → Add file → Upload files** (덮어쓰기)
- 캡션 수정: Issue의 **json 편집** 링크 → `"caption"` 값 수정 → Commit
- 그날 올리지 않으려면 `posts/날짜.png` 삭제

## 참고
- 아카이브 주소가 바뀌면: **Settings → Secrets and variables → Actions → Variables → New repository variable** `H730_ARCHIVE_URL`
- prepare 수동 실행 옵션: `letter`(레터 URL 직접 지정), `force`(이미 만든 날짜 다시 만들기)
- 스타일(물결밑줄형/상자형)은 `state.json` 의 `lastStyle` 기준으로 카드를 만든 날만 번갈아 바뀌어요.
- 로컬 실행: `pip install -r requirements.txt && python -m playwright install chromium` 후
  `python scripts/prepare.py --date 2026-10-29 --no-push`, `python scripts/publish.py --date 2026-10-29 --dry-run`
