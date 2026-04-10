# AUTOTRADE α-1a Part C-3-2 + C-3-3: systemd unit + 정식 가동 보고서

작성일: 2026-04-08 01:05 KST
서버: ubuntu@134.185.115.229

---

## 결과 요약

| 단계 | 결과 |
|------|------|
| C-3-2-a systemd unit 작성 | ✅ |
| C-3-2-b 임시 가동 + 30초 검증 | ✅ |
| C-3-2-c 검증 게이트 11개 | ✅ 전부 통과 |
| C-3-2-d 수석님 텔레그램 확인 | ✅ yes-received |
| C-3-3-a systemd enable | ✅ |
| C-3-3 정식 가동 확인 | ✅ active + enabled |

---

## systemd unit 파일 전문

```ini
[Unit]
Description=AUTOTRADE - KOSPI/KOSDAQ automated trading (alpha-1a dry-run)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=ubuntu
Group=ubuntu
WorkingDirectory=/home/ubuntu/AUTOTRADE
Environment="PATH=/home/ubuntu/AUTOTRADE/venv/bin:/usr/local/bin:/usr/bin:/bin"
Environment="PYTHONUNBUFFERED=1"
ExecStart=/home/ubuntu/AUTOTRADE/venv/bin/python -m src.main
KillSignal=SIGTERM
TimeoutStopSec=15
Restart=on-failure
RestartSec=10
StandardOutput=append:/home/ubuntu/AUTOTRADE/logs/autotrade.log
StandardError=append:/home/ubuntu/AUTOTRADE/logs/autotrade.err

# 보안 강화
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=/home/ubuntu/AUTOTRADE/data /home/ubuntu/AUTOTRADE/logs

[Install]
WantedBy=multi-user.target
```

---

## 가동 후 30초 시점 로그

```
텔레그램 알림 활성화 (2명)
DB 초기화 완료: /home/ubuntu/AUTOTRADE/data/trades.db
AUTOTRADE 시작 (모드: dry_run)
텔레그램 명령 수신 시작 (polling)
KIS 토큰 캐시 로드 (만료: 2026-04-08 23:45)
KIS API 연결 완료 (모의투자)
선물 실시간 구독: 101S3000 (TR: H0IFCNT0)
[DRY_RUN] 가상 예수금 사용: 50,000,000원
예수금: 50,000,000원 → 매매가용: 50,000,000원
대시보드 서버 시작 (port=8503)
종목 마스터 로드: 2773건
Cloudflare Tunnel 시작: https://organizational-pieces-booth-thu.trycloudflare.com
```

---

## Quick Tunnel URL

```
https://organizational-pieces-booth-thu.trycloudflare.com
```

Cloudflare PoP: icn05 (인천), protocol: QUIC

**주의**: Quick Tunnel URL은 재시작마다 변경됨. 매 시작 시 텔레그램으로 새 URL 수신.

---

## 메모리 사용량 — 3개 시점

| 시점 | Mem used | Swap used |
|------|----------|-----------|
| 가동 전 | 210Mi | 1Mi |
| 가동 후 30초 | 339Mi | 1Mi |
| 정식 가동 (enable 후) | 345Mi | 1Mi |

- 증분: +135Mi (python 프로세스 + cloudflared)
- 여유: ~611Mi (available 439Mi + buff/cache 일부)
- Swap: 1Mi (사실상 0, 정상)

---

## 프로세스 구성 (systemd CGroup)

```
/system.slice/autotrade.service
├─ 19029 /home/ubuntu/AUTOTRADE/venv/bin/python -m src.main
└─ 19041 cloudflared tunnel --url http://localhost:8503
```

- `src.main`이 `cloudflared`를 자식 프로세스로 spawn
- SIGTERM → `src.main` → cloudflared도 함께 종료 (정상)

---

## 디스크 사용량

| 경로 | 사용량 |
|------|--------|
| data/ | 32K (trades.db 초기 상태) |
| logs/ | 8.0K |

---

## 수석님 텔레그램 확인

- 결과: **yes-received** (텔레그램 메시지 수신 + 대시보드 URL 확인 완료)

---

## systemd enable 결과

```
Created symlink /etc/systemd/system/multi-user.target.wants/autotrade.service
  → /etc/systemd/system/autotrade.service
is-active:  active
is-enabled: enabled
```

---

## 발견 사항

1. **autotrade.log 항상 비어있음**: loguru 기본 핸들러가 `sys.stderr`로 출력 → 모든 로그가 `autotrade.err`에 기록. `autotrade.log`는 사실상 미사용. 운영에 영향 없으나 향후 `logger.add("autotrade.log")` 추가 시 분리 가능. (별도 이슈 등록 필요 없음, 현재 구조로 충분)

2. **Matplotlib MPLCONFIGDIR 경고**: `PrivateTmp=true`로 인해 `~/.config/matplotlib` 접근 불가 → `/tmp/matplotlib-xxx` 임시 디렉토리 사용. 매매 로직에 영향 없음. 억제하려면 unit에 `Environment="MPLCONFIGDIR=/home/ubuntu/AUTOTRADE/data/.matplotlib"` + `ReadWritePaths` 추가 가능. 지금은 무시.

3. **KIS 토큰 캐시 재사용**: 서버에서 C-2-5에서 발급한 토큰을 캐시(`token_paper.json`)에서 로드. 만료(23:45) 전 자동 갱신 로직 작동 예정.

4. **텔레그램 성공 로그 없음**: `notifier._send()`는 성공 시 로그 없음, 실패 시만 ERROR 로그. 운영 중 텔레그램 미수신 시 `autotrade.err`에서 "텔레그램 전송 실패" 검색.

---

## 정식 가동 시작

```
2026-04-08 01:02:06 KST — systemd start
2026-04-08 01:04:45 KST — systemctl enable 완료
```

---

## K 수석 09:00 체크리스트

아침에 일어나서 09:00 직전 확인할 항목 5개:

**1. 텔레그램 봇 메시지**
- 가장 최근 Tunnel URL 확인 (재시작이 있었다면 새 URL로 변경됨)
- 메시지 형식: `대시보드 접속 URL (관리자)\n\nhttps://xxx.trycloudflare.com?token=...`

**2. Dashboard URL 접속**
```
https://[텔레그램_수신_URL]?token=[DASHBOARD_ADMIN_TOKEN]
```
- 브라우저에서 열어서 대시보드 표시 확인
- admin 모드: 종목 입력 영역 활성화 상태여야 함

**3. 메모리 / swap 확인**
```bash
ssh ubuntu@134.185.115.229 'free -h'
```
- 기준: Mem used < 800Mi, Swap used < 1500Mi

**4. systemd 상태 확인**
```bash
ssh ubuntu@134.185.115.229 'sudo systemctl is-active autotrade'
```
- 기대값: `active`

**5. 로그 tail**
```bash
ssh ubuntu@134.185.115.229 'tail -30 ~/AUTOTRADE/logs/autotrade.err'
```
- ERROR 또는 traceback 없으면 정상
