# AUTOTRADE α-1a Part C-1: 서버 환경 셋업 보고서

작성일: 2026-04-08 (KST)
서버: ubuntu@134.185.115.229 (Oracle Cloud, ap-chuncheon-1)

---

## 작업 항목별 결과

| 항목 | 상태 | 비고 |
|------|------|------|
| C-1-1 시스템 업데이트 | ✅ | apt upgrade 완료 |
| C-1-2 Python 3.11 설치 | ✅ | Python 3.11.15 |
| C-1-3 시스템 의존성 | ✅ | build-essential, git, sqlite3 등 35패키지 |
| C-1-4 디렉토리 구조 | ✅ | data/ logs/ chmod 700 |
| C-1-5 가상환경 | ✅ | Python 3.11.15 / pip 24.0 |
| C-1-6 타임존 | ✅ | Asia/Seoul (KST, +0900) |
| C-1-7 iptables | ✅ (보고) | 변경 없음 |
| C-1-8 디스크/메모리 | ✅ (보고) | 이하 실측값 |

---

## Python 3.11 버전

```
Python 3.11.15
pip 24.0
```

deadsnakes PPA 정상 작동 (x86_64 아키텍처 — 사전 우려했던 ARM 이슈 없음).

---

## 디스크 / 메모리 실측값

```
Filesystem      Size  Used Avail Use%
/dev/sda1        45G  3.0G   42G   7%

Mem:   total 956Mi  used 237Mi  free 167Mi  buff/cache 551Mi  available 550Mi
Swap:  0B
```

- 디스크: 45GB 중 3GB 사용 (7%), 여유 42GB
- 메모리: 956MB (약 1GB). 현재 237MB 사용, 가용 550MB
- Swap: 없음

---

## Timezone 확인

```
Local time: Wed 2026-04-08 00:15:41 KST
Time zone: Asia/Seoul (KST, +0900)
System clock synchronized: yes
NTP service: active
```

---

## iptables INPUT 체인 현재 상태

```
Chain INPUT (policy ACCEPT 0 packets, 0 bytes)
 pkts bytes target  prot  in  out  source       destination
46121  200M ACCEPT  all   *   *    0.0.0.0/0    0.0.0.0/0    state RELATED,ESTABLISHED
    0     0 ACCEPT  icmp  *   *    0.0.0.0/0    0.0.0.0/0
  154 14005 ACCEPT  all   lo  *    0.0.0.0/0    0.0.0.0/0
  119  5504 ACCEPT  tcp   *   *    0.0.0.0/0    0.0.0.0/0    state NEW tcp dpt:22
    3  1044 REJECT  all   *   *    0.0.0.0/0    0.0.0.0/0    reject-with icmp-host-prohibited
```

- SSH(22) 허용, 기타 신규 연결 전부 REJECT
- 포트 8503 (대시보드) 미개방 — C-3 또는 별도 작업 필요

---

## 발견 사항

1. **아키텍처 x86_64 (AMD), ARM 아님**: VM.Standard.E2.1.Micro는 AMD Micro 인스턴스. 사전 우려한 deadsnakes ARM 호환성 이슈 없음.

2. **메모리 956MB (1GB), Swap 없음**: AUTOTRADE 예상 사용량 ~180MB 대비 여유 있으나, pandas 초기 import + pykrx 첫 호출 시 순간 피크 발생 가능. Swap 파티션 없음 — C-2 또는 C-3에서 swap file 추가 검토 필요.

3. **iptables 포트 8503 미개방**: 대시보드 직접 접속 불가 상태. Cloudflare Tunnel 경유 시 이 제약 우회 가능 (tunnel이 로컬 포트에 연결하므로 inbound 불필요). C-3에서 systemd 설정 시 확인.
