# AUTOTRADE α-1a Part C-1.5: Swap 추가 보고서

작성일: 2026-04-08 (KST)
서버: ubuntu@134.185.115.229

---

## 검증 출력 (원문)

```
=== swapfile ===
-rw------- 1 root root 4294967296 Apr  8 00:22 /swapfile

=== Memory ===
               total        used        free      shared  buff/cache   available
Mem:           956Mi       228Mi       166Mi       1.0Mi       561Mi       559Mi
Swap:          4.0Gi          0B       4.0Gi

=== Swappiness ===
vm.swappiness = 10

=== fstab ===
/swapfile none swap sw 0 0
```

---

## 항목별 결과

| 항목 | 결과 |
|------|------|
| swap file 크기 | 4.0 GiB (4,294,967,296 bytes) |
| swap file 권한 | `-rw------- root root` (600) |
| swap 활성화 | Swap: 4.0Gi / 0B used |
| swappiness | vm.swappiness = 10 |
| /etc/fstab 등록 | `/swapfile none swap sw 0 0` |
| 영구 설정 | `/etc/sysctl.d/99-swap.conf` 작성 완료 |

모든 검증 통과.
