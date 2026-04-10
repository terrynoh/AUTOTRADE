# W-16 — vault 재구성 (옵션 C 출범)

> **미션**: vault 를 옵션 C (활성/동기화/정적 3 영역) 로 재구성. 4/13 첫 거래일 검증 전 stale 정보 격리 + 신규 활성 구조 출범.
>
> **R-N 정렬**: R-08 종결 직후, R-09 진입 전
> **실행자**: 수석님 직접 (Code 위임 X)
> **소요 시간**: 약 15~30 분
> **작성일**: 2026-04-10

---

## 제약

- **화이트리스트**: vault 만 (`C:\Users\terryn\Documents\Obsidian\AUTOTRADE`)
- **블랙리스트**:
  - AUTOTRADE 작업폴더 (`C:\Users\terryn\AUTOTRADE`) 일체
  - 운영 서버 (`134.185.115.229`) 일체
  - git (vault 는 별도 관리, AUTOTRADE git 과 무관)
- **1 회용**: 실행 후 본 명령서를 `_관리/W-16_vault_재구성_명령서.md` 로 archive

---

## 원칙

- §15-16 vault 원본 문서 보존: stale 파일은 *삭제 X*, `_archive/2026-04-pre-R08/` 로 *이동*
- §5.6 원칙 1 사실 base 우선: 신규 stub/문서는 CLAUDE.md v3.1 정합
- 멈춤 조건 명시 (각 작업 단계에서 예상 외 결과 시 즉시 멈춤 + 보고)

---

## 사전 준비

키트 zip 다운로드 후 적절한 위치에 임시 압축 해제:
```bash
# Git Bash
cd /c/Users/terryn/Downloads
unzip vault_R08_초기화_kit.zip -d vault_kit_temp
ls vault_kit_temp
```

키트 안에 다음이 있어야 함:
- `_관리/vault_운영_매뉴얼.md`
- `00_대시보드.md`
- `02_Phase_진행상황.md`
- `04_환경설정.md`
- `이슈_트래커.md`
- `백테스트_결과.md`
- `R/R-07_종결.md`
- `R/R-08_종결.md`
- `개발일지/_템플릿.md`
- `매매일지/_템플릿.md`

---

## 작업 0 — 사전 백업 (필수)

```bash
# Obsidian 닫기 (파일 핸들 해제)
# Git Bash
cd /c/Users/terryn/Documents/Obsidian
tar -czvf AUTOTRADE_pre-W16_$(date +%Y-%m-%d).tar.gz AUTOTRADE
ls -lh AUTOTRADE_pre-W16_*.tar.gz
```

**검증**: 백업 파일 크기 > 0 byte. 실패 시 **즉시 멈춤**.

---

## 작업 1 — 디렉토리 신설

```bash
cd /c/Users/terryn/Documents/Obsidian/AUTOTRADE
mkdir -p _archive/2026-04-pre-R08
mkdir -p R
mkdir -p 결정
mkdir -p _관리
```

(`개발일지/`, `매매일지/` 는 기존 유지)

**검증**: 4 개 디렉토리 신규 존재.

---

## 작업 2 — stale 파일 archive 이동

```bash
cd /c/Users/terryn/Documents/Obsidian/AUTOTRADE
mv 00_대시보드.md _archive/2026-04-pre-R08/00_대시보드_stale.md
mv 01_전략_마스터스펙.md _archive/2026-04-pre-R08/01_전략_마스터스펙_stale.md
mv 02_Phase_진행상황.md _archive/2026-04-pre-R08/02_Phase_진행상황_stale.md
mv 04_환경설정.md _archive/2026-04-pre-R08/04_환경설정_stale.md
mv 05_이슈_트래커.md _archive/2026-04-pre-R08/05_이슈_트래커_stale.md
mv 06_백테스트_결과.md _archive/2026-04-pre-R08/06_백테스트_결과_stale.md
```

**유지 (정적 참조 영역)**: `03_KIS_API_레퍼런스.md`, `07_개발지침.md` → 작업 6 에서 헤더만 갱신.

**검증**:
```bash
ls _archive/2026-04-pre-R08/
# 6 개 파일 확인
ls *.md
# 03, 07 만 남았는지 확인 (00, 01, 02, 04, 05, 06 없음)
```

실패 시 (예: 파일 못 찾음, 충돌) **즉시 멈춤**.

---

## 작업 3 — 신규 활성/동기화 파일 배포

키트에서 vault 루트로 복사:

```bash
cd /c/Users/terryn/Downloads/vault_kit_temp
VAULT=/c/Users/terryn/Documents/Obsidian/AUTOTRADE

cp 00_대시보드.md "$VAULT/"
cp 02_Phase_진행상황.md "$VAULT/"
cp 04_환경설정.md "$VAULT/"
cp 이슈_트래커.md "$VAULT/"
cp 백테스트_결과.md "$VAULT/"
```

**검증**: vault 루트에 5 개 신규 파일 + 기존 03/07 = 7 개 `.md` 존재.

---

## 작업 4 — R/ 디렉토리 채우기

```bash
cd /c/Users/terryn/Downloads/vault_kit_temp
VAULT=/c/Users/terryn/Documents/Obsidian/AUTOTRADE

cp R/R-07_종결.md "$VAULT/R/"
cp R/R-08_종결.md "$VAULT/R/"
```

**R-04 원본 보존** (별도):
```bash
cp "$VAULT/_archive/2026-04-pre-R08/01_전략_마스터스펙_stale.md" "$VAULT/R/R-04_원본.md"
```

R-04 는 archive 에 *원본* 도 남기고 R/ 에 *동결본 사본* 도 남김. 향후 신규 채팅에서 "R 별 변천" 을 빠르게 추적하기 위함.

R-04 사본 헤더에 다음 한 줄 추가:
```markdown
> 🟢 **R-04 동결본** — 2026-04-06 시점 마스터스펙 원본 보존. 현재 매매 명세는 `CLAUDE.md` §2.
```

(Obsidian 에서 직접 첫 줄에 추가하거나, vim/메모장에서 편집)

**검증**: `R/` 안에 `R-04_원본.md`, `R-07_종결.md`, `R-08_종결.md` 3 개.

---

## 작업 5 — 매매/개발일지 템플릿 R-08 화

```bash
cd /c/Users/terryn/Downloads/vault_kit_temp
VAULT=/c/Users/terryn/Documents/Obsidian/AUTOTRADE

# 기존 템플릿 백업 (archive)
mv "$VAULT/개발일지/_템플릿.md" "$VAULT/_archive/2026-04-pre-R08/개발일지_템플릿_stale.md"
mv "$VAULT/매매일지/_템플릿.md" "$VAULT/_archive/2026-04-pre-R08/매매일지_템플릿_stale.md"

# 신규 R-08 화 템플릿 배포
cp 개발일지/_템플릿.md "$VAULT/개발일지/"
cp 매매일지/_템플릿.md "$VAULT/매매일지/"
```

**검증**: 신규 템플릿 본문에 "T1/T2/T3", "ReservationSnapshot", "last-line defense" 키워드 grep 통과.

---

## 작업 6 — vault 운영 매뉴얼 배포 (`_관리/`)

```bash
cd /c/Users/terryn/Downloads/vault_kit_temp
VAULT=/c/Users/terryn/Documents/Obsidian/AUTOTRADE

cp _관리/vault_운영_매뉴얼.md "$VAULT/_관리/"
```

본 W-16 명령서 자체도 archive:
```bash
cp /c/Users/terryn/Downloads/W-16_vault_재구성_명령서.md "$VAULT/_관리/"
```

**검증**: `_관리/` 안에 `vault_운영_매뉴얼.md`, `W-16_vault_재구성_명령서.md` 2 개.

---

## 작업 7 — 정적 참조 영역 헤더 라벨

`03_KIS_API_레퍼런스.md` 와 `07_개발지침.md` 의 첫 줄 (제목) 다음에 다음 한 줄 추가:

`03_KIS_API_레퍼런스.md`:
```markdown
> 🔵 **정적 참조 영역** — KIS API 외부 사양. 변경 시에만 갱신. 마지막 정합: 2026-04-10 (W-16).
```

`07_개발지침.md`:
```markdown
> 🔵 **정적 참조 영역** — 9 코딩 규칙. CLAUDE.md §5.6 (8 협업 원칙) 과 별개. 마지막 정합: 2026-04-10 (W-16).
```

Obsidian 에서 직접 편집.

---

## 작업 8 — 검증

### 8.1 파일 카운트
```bash
cd /c/Users/terryn/Documents/Obsidian/AUTOTRADE
find . -name "*.md" -not -path "./.obsidian/*" | sort
```

기대 결과 (총 14 개):
```
./00_대시보드.md
./02_Phase_진행상황.md
./03_KIS_API_레퍼런스.md
./04_환경설정.md
./07_개발지침.md
./_관리/W-16_vault_재구성_명령서.md
./_관리/vault_운영_매뉴얼.md
./_archive/2026-04-pre-R08/00_대시보드_stale.md
./_archive/2026-04-pre-R08/01_전략_마스터스펙_stale.md
./_archive/2026-04-pre-R08/02_Phase_진행상황_stale.md
./_archive/2026-04-pre-R08/04_환경설정_stale.md
./_archive/2026-04-pre-R08/05_이슈_트래커_stale.md
./_archive/2026-04-pre-R08/06_백테스트_결과_stale.md
./_archive/2026-04-pre-R08/개발일지_템플릿_stale.md
./_archive/2026-04-pre-R08/매매일지_템플릿_stale.md
./R/R-04_원본.md
./R/R-07_종결.md
./R/R-08_종결.md
./개발일지/2026-04-05.md
./개발일지/2026-04-06.md
./개발일지/2026-04-07.md
./개발일지/_템플릿.md
./매매일지/_템플릿.md
./백테스트_결과.md
./이슈_트래커.md
```

(총 25 개 — 활성 7 + 정적 2 + 관리 2 + archive 9 + R 3 + 일지 4 + 템플릿 1)

### 8.2 Obsidian 그래프 점검
- Obsidian 다시 열기
- 그래프 뷰 (Graph View) 확인
- `_archive/` 노드는 *고립* 되어야 정상 (의도적 격리)
- 활성 영역은 `00_대시보드` ↔ `02_Phase` ↔ `R/` ↔ `이슈_트래커` 로 연결

### 8.3 깨진 링크 검사
Obsidian 명령 팔레트 (Ctrl+P) → "Search for broken links" 또는 Linter 플러그인 사용. 결과 = 0 이어야 함.

깨진 링크 발견 시:
- stub 영역의 `[[02_Phase_진행상황]]` 같은 wiki link 가 정상 작동하는지
- `[[R/R-08_종결]]` 같은 디렉토리 prefix 가 작동하는지

---

## 멈춤 조건

| 단계 | 조건 | 대응 |
|---|---|---|
| 작업 0 | 백업 실패 | 즉시 멈춤, 백업 성공 후 재개 |
| 작업 2 | mv 시 파일 충돌 / 못 찾음 | 즉시 멈춤, 수동 확인 |
| 작업 3 | cp 시 파일 부재 (키트 손상) | 즉시 멈춤, 키트 재다운로드 |
| 작업 8.1 | 파일 카운트 불일치 | 빠진 파일 식별 + 보충 |
| 작업 8.2 | 그래프에 예상 외 고립 노드 | stub 의 wiki link 점검 |
| 작업 8.3 | 깨진 링크 > 0 | 해당 링크 위치 확인 + 정정 |

---

## 보고

완료 후 채팅에 다음 형식으로 보고:

```
W-16 vault 재구성 완료.
- 백업: AUTOTRADE_pre-W16_2026-04-10.tar.gz (XX MB)
- 신규 활성: 5 (00, 02, 04, 이슈, 백테스트)
- 신규 R/: 3 (R-04 동결본, R-07 종결, R-08 종결)
- 신규 _관리/: 2 (운영 매뉴얼, W-16 명령서)
- archive: 8 (stale 6 + 템플릿 2)
- 정적 참조 헤더 갱신: 03, 07
- Obsidian 그래프: 정상 / 깨진 링크: N 건
- 다음: CLAUDE.md §14 패치 적용 + 4/13 첫 거래일 매매일지 작성
```

---

## 후속 작업

1. **CLAUDE.md §14 패치 적용** — 채팅에서 받은 패치 텍스트를 작업폴더 `CLAUDE.md` 의 §14 영역에 직접 반영. 적용 후 v3.2 로 버전 올림.
2. **신규 채팅 인계 테스트** — CLAUDE.md v3.2 + `_관리/vault_운영_매뉴얼.md` 두 파일만 새 채팅에 업로드 → 옵션 C 패턴 자동 인식 확인.
3. **4/13 (월) 첫 거래일** — 매매일지 신규 작성. 일일 마무리 루틴 첫 적용.

---

## 표준 금지 조항

1. 운영 서버 접근 0건
2. 운영 서버 배포 0건
3. git commit 자동 실행 금지
4. 로컬 AutoTrader 실제 실행 금지
5. systemd / 데몬 재시작 금지

본 명령서는 vault 로컬 작업이라 위 5 건 모두 *영역 외 자동 충족*. 그래도 명시해 §5.6 정합 유지.
