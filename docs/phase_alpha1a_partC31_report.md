# AUTOTRADE α-1a Part C-3-1: cloudflared 설치 + 코드 호출 경로 검증 보고서

작성일: 2026-04-08 (KST)
서버: ubuntu@134.185.115.229

---

## 단계별 결과

| 단계 | 결과 |
|------|------|
| C-3-1-a 코드 호출 경로 확인 | ✅ PATH 탐색 (shutil.which) |
| C-3-1-b cloudflared 설치 | ✅ v2026.3.0 / /usr/local/bin |
| C-3-1-c Quick Tunnel 단독 실행 | ✅ trycloudflare.com URL 추출 성공 |

---

## C-3-1-a. CloudflareTunnel 코드 호출 경로

**파일**: `src/utils/tunnel.py`

| 라인 | 역할 |
|------|------|
| 21~36 | `_build_cloudflared_candidates()` — OS별 후보 경로 목록 생성 |
| 39~44 | `_find_cloudflared()` — `shutil.which(candidate)` 로 PATH 탐색 |
| 60~68 | `CloudflareTunnel.start()` — `asyncio.create_subprocess_exec(exe, ...)` 실행 |

**호출 방식: PATH 탐색 (shutil.which)**

```python
candidates = ["cloudflared", r"C:\Program Files\cloudflared\cloudflared.exe", ...]

def _find_cloudflared() -> Optional[str]:
    for candidate in _CLOUDFLARED_CANDIDATES:
        if shutil.which(candidate) or (candidate != "cloudflared" and Path(candidate).exists()):
            return candidate
    return None
```

- 첫 번째 후보 `"cloudflared"` 로 `shutil.which` 탐색
- `/usr/local/bin/cloudflared` 가 PATH에 있으면 자동 인식
- 코드 변경 불필요

---

## C-3-1-b. cloudflared 바이너리 설치 결과

```
패키지: cloudflared-linux-amd64.deb
버전:   cloudflared version 2026.3.0 (built 2026-03-09-14:08 UTC)
경로:   /usr/local/bin/cloudflared
설치처: dpkg (Cloudflare GitHub Releases)
```

---

## C-3-1-c. Quick Tunnel 단독 실행 검증

```
Requesting new quick Tunnel on trycloudflare.com...
Your quick Tunnel has been created! Visit it at:
https://improvement-bracket-dodge-innovative.trycloudflare.com

Registered tunnel connection connIndex=0 ... location=icn05 protocol=quic
```

**추출된 URL**: `https://improvement-bracket-dodge-innovative.trycloudflare.com`

- `icn05` — Cloudflare 인천(ICN) PoP 연결 (서울 근접, 레이턴시 최소)
- protocol: QUIC (HTTP/3 기반, 최적)

---

## 발견 사항

1. **UDP 버퍼 경고**: `failed to sufficiently increase receive buffer size (was: 208 kiB, wanted: 7168 kiB, got: 416 kiB)` — QUIC 성능 최적화 관련 비치명적 경고. 매매 시스템에서 Tunnel은 dashboard 조회 전용이므로 영향 없음. 무시.

2. **config.yml 없음 경고**: `Cannot determine default configuration path` — Quick Tunnel은 config 파일 불필요. 정상.

3. **자동 업데이트 비활성**: `cloudflared will not automatically update if installed by a package manager` — dpkg 설치 시 정상 동작. 보안 업데이트는 수동으로 진행.
