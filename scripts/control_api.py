#!/usr/bin/env python3
"""
AUTOTRADE Control API — 서비스 제어 + 웹 UI.

24시간 가동되어 autotrade 서비스 시작/중지/상태 확인 제공.
Cloudflare Access 인증 후 접근 가능.

실행:
    python scripts/control_api.py
    → http://localhost:8504
"""
import subprocess
import asyncio
from datetime import datetime, time
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

app = FastAPI(title="AUTOTRADE Control")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://hwrim.trade"],
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)

KST = ZoneInfo("Asia/Seoul")
SERVICE_NAME = "autotrade"
AUTO_STOP_TIME = time(18, 0)  # 오후 6시 자동 종료


def run_systemctl(action: str) -> tuple[bool, str]:
    """systemctl 명령 실행."""
    try:
        result = subprocess.run(
            ["sudo", "systemctl", action, SERVICE_NAME],
            capture_output=True,
            text=True,
            timeout=30,
        )
        success = result.returncode == 0
        msg = result.stdout.strip() or result.stderr.strip() or f"{action} 완료"
        return success, msg
    except subprocess.TimeoutExpired:
        return False, "명령 timeout"
    except Exception as e:
        return False, str(e)


def get_service_status() -> dict:
    """서비스 상태 조회."""
    try:
        # is-active
        active_result = subprocess.run(
            ["systemctl", "is-active", SERVICE_NAME],
            capture_output=True,
            text=True,
        )
        is_active = active_result.stdout.strip() == "active"

        # 상세 정보
        status_result = subprocess.run(
            ["systemctl", "show", SERVICE_NAME, "--property=ActiveState,SubState,MainPID,ActiveEnterTimestamp"],
            capture_output=True,
            text=True,
        )
        props = {}
        for line in status_result.stdout.strip().split("\n"):
            if "=" in line:
                key, val = line.split("=", 1)
                props[key] = val

        return {
            "running": is_active,
            "state": props.get("ActiveState", "unknown"),
            "substate": props.get("SubState", "unknown"),
            "pid": props.get("MainPID", ""),
            "started_at": props.get("ActiveEnterTimestamp", ""),
        }
    except Exception as e:
        return {"running": False, "error": str(e)}


# ── API 라우트 ──────────────────────────────────────────────

@app.get("/control/api/status")
async def api_status():
    """서비스 상태 조회."""
    status = get_service_status()
    status["server_time"] = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST")
    return status


@app.post("/control/api/start")
async def api_start():
    """서비스 시작."""
    status = get_service_status()
    if status.get("running"):
        return {"ok": False, "msg": "이미 실행 중입니다"}

    success, msg = run_systemctl("start")
    return {"ok": success, "msg": msg}


@app.post("/control/api/stop")
async def api_stop():
    """서비스 중지."""
    status = get_service_status()
    if not status.get("running"):
        return {"ok": False, "msg": "이미 중지되어 있습니다"}

    success, msg = run_systemctl("stop")
    return {"ok": success, "msg": msg}


@app.post("/control/api/restart")
async def api_restart():
    """서비스 재시작."""
    success, msg = run_systemctl("restart")
    return {"ok": success, "msg": msg}


# ── 웹 UI ───────────────────────────────────────────────────

CONTROL_HTML = """
<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AUTOTRADE Control</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Segoe UI', -apple-system, sans-serif;
            background: #0a0e17;
            color: #e1e5eb;
            min-height: 100vh;
            display: flex;
            justify-content: center;
            align-items: center;
            padding: 20px;
        }
        .container {
            background: #111827;
            border: 1px solid #1e293b;
            border-radius: 16px;
            padding: 32px;
            width: 100%;
            max-width: 400px;
            text-align: center;
        }
        h1 {
            font-size: 24px;
            margin-bottom: 8px;
            color: #f0f4f8;
        }
        .subtitle {
            color: #64748b;
            font-size: 14px;
            margin-bottom: 24px;
        }
        .status-card {
            background: #0d1117;
            border-radius: 12px;
            padding: 20px;
            margin-bottom: 24px;
        }
        .status-indicator {
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 10px;
            font-size: 18px;
            font-weight: 600;
            margin-bottom: 12px;
        }
        .dot {
            width: 12px;
            height: 12px;
            border-radius: 50%;
        }
        .dot-green { background: #22c55e; box-shadow: 0 0 8px #22c55e; }
        .dot-red { background: #ef4444; }
        .dot-yellow { background: #eab308; animation: pulse 1s infinite; }
        @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.5; }
        }
        .status-detail {
            font-size: 13px;
            color: #64748b;
        }
        .btn-group {
            display: flex;
            gap: 12px;
            justify-content: center;
            flex-wrap: wrap;
        }
        .btn {
            padding: 14px 28px;
            border: none;
            border-radius: 10px;
            font-size: 15px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.2s;
            min-width: 120px;
        }
        .btn:hover { transform: translateY(-2px); }
        .btn:active { transform: translateY(0); }
        .btn:disabled { opacity: 0.5; cursor: not-allowed; transform: none; }
        .btn-start { background: #22c55e; color: #0a0e17; }
        .btn-start:hover { background: #16a34a; }
        .btn-stop { background: #ef4444; color: white; }
        .btn-stop:hover { background: #dc2626; }
        .btn-restart { background: #3b82f6; color: white; }
        .btn-restart:hover { background: #2563eb; }
        .server-time {
            margin-top: 20px;
            font-size: 12px;
            color: #475569;
        }
        .dashboard-link {
            margin-top: 16px;
        }
        .dashboard-link a {
            color: #60a5fa;
            text-decoration: none;
            font-size: 14px;
        }
        .dashboard-link a:hover { text-decoration: underline; }
        .toast {
            position: fixed;
            bottom: 20px;
            left: 50%;
            transform: translateX(-50%);
            background: #1e293b;
            color: #e1e5eb;
            padding: 12px 24px;
            border-radius: 8px;
            font-size: 14px;
            opacity: 0;
            transition: opacity 0.3s;
            z-index: 100;
        }
        .toast.show { opacity: 1; }
        .toast.success { border-left: 4px solid #22c55e; }
        .toast.error { border-left: 4px solid #ef4444; }
    </style>
</head>
<body>
    <div class="container">
        <h1>🤖 AUTOTRADE</h1>
        <p class="subtitle">서비스 제어판</p>

        <div class="status-card">
            <div class="status-indicator">
                <span class="dot" id="status-dot"></span>
                <span id="status-text">확인 중...</span>
            </div>
            <div class="status-detail" id="status-detail">-</div>
        </div>

        <div class="btn-group">
            <button class="btn btn-start" id="btn-start" onclick="doStart()" disabled>▶ 시작</button>
            <button class="btn btn-stop" id="btn-stop" onclick="doStop()" disabled>⏹ 중지</button>
        </div>

        <div class="dashboard-link" id="dashboard-link" style="display:none;">
            <a href="/" target="_blank">📊 대시보드 열기 →</a>
        </div>

        <div class="server-time" id="server-time">-</div>
    </div>

    <div class="toast" id="toast"></div>

    <script>
        let isRunning = false;

        async function fetchStatus() {
            try {
                const res = await fetch('/control/api/status');
                const data = await res.json();
                updateUI(data);
            } catch (e) {
                updateUI({ running: false, error: '연결 실패' });
            }
        }

        function updateUI(data) {
            const dot = document.getElementById('status-dot');
            const text = document.getElementById('status-text');
            const detail = document.getElementById('status-detail');
            const btnStart = document.getElementById('btn-start');
            const btnStop = document.getElementById('btn-stop');
            const dashboardLink = document.getElementById('dashboard-link');
            const serverTime = document.getElementById('server-time');

            isRunning = data.running;

            if (data.error) {
                dot.className = 'dot dot-red';
                text.textContent = '오류';
                detail.textContent = data.error;
                btnStart.disabled = true;
                btnStop.disabled = true;
            } else if (data.running) {
                dot.className = 'dot dot-green';
                text.textContent = '실행 중';
                detail.textContent = `PID: ${data.pid} | ${data.started_at}`;
                btnStart.disabled = true;
                btnStop.disabled = false;
                dashboardLink.style.display = 'block';
            } else {
                dot.className = 'dot dot-red';
                text.textContent = '중지됨';
                detail.textContent = data.state || '-';
                btnStart.disabled = false;
                btnStop.disabled = true;
                dashboardLink.style.display = 'none';
            }

            if (data.server_time) {
                serverTime.textContent = data.server_time;
            }
        }

        async function doStart() {
            setLoading(true);
            try {
                const res = await fetch('/control/api/start', { method: 'POST' });
                const data = await res.json();
                showToast(data.msg, data.ok ? 'success' : 'error');
                setTimeout(fetchStatus, 1000);
            } catch (e) {
                showToast('요청 실패', 'error');
            }
            setLoading(false);
        }

        async function doStop() {
            if (!confirm('서비스를 중지하시겠습니까?')) return;
            setLoading(true);
            try {
                const res = await fetch('/control/api/stop', { method: 'POST' });
                const data = await res.json();
                showToast(data.msg, data.ok ? 'success' : 'error');
                setTimeout(fetchStatus, 1000);
            } catch (e) {
                showToast('요청 실패', 'error');
            }
            setLoading(false);
        }

        function setLoading(loading) {
            const dot = document.getElementById('status-dot');
            const text = document.getElementById('status-text');
            if (loading) {
                dot.className = 'dot dot-yellow';
                text.textContent = '처리 중...';
            }
            document.getElementById('btn-start').disabled = loading;
            document.getElementById('btn-stop').disabled = loading;
        }

        function showToast(msg, type) {
            const toast = document.getElementById('toast');
            toast.textContent = msg;
            toast.className = 'toast show ' + type;
            setTimeout(() => { toast.className = 'toast'; }, 3000);
        }

        // 초기 로드 + 5초마다 상태 갱신
        fetchStatus();
        setInterval(fetchStatus, 5000);
    </script>
</body>
</html>
"""


@app.get("/control", response_class=HTMLResponse)
@app.get("/control/", response_class=HTMLResponse)
async def control_page():
    """Control 웹 UI."""
    return HTMLResponse(CONTROL_HTML)


# ── 자동 종료 스케줄러 ──────────────────────────────────────

async def auto_stop_scheduler():
    """오후 6시 자동 종료 스케줄러."""
    while True:
        now = datetime.now(KST)
        
        # 오후 6시 체크 (18:00:00 ~ 18:00:59)
        if now.time().hour == AUTO_STOP_TIME.hour and now.time().minute == AUTO_STOP_TIME.minute:
            status = get_service_status()
            if status.get("running"):
                print(f"[{now}] 오후 6시 자동 종료 실행")
                run_systemctl("stop")
            # 1분 후 재체크 방지
            await asyncio.sleep(60)
        else:
            # 30초마다 체크
            await asyncio.sleep(30)


@app.on_event("startup")
async def startup_event():
    """앱 시작 시 스케줄러 실행."""
    asyncio.create_task(auto_stop_scheduler())
    print(f"[Control API] 자동 종료 스케줄러 시작 (매일 {AUTO_STOP_TIME.strftime('%H:%M')} KST)")


# ── 실행 ────────────────────────────────────────────────────

def main():
    print("=" * 50)
    print("AUTOTRADE Control API")
    print(f"http://0.0.0.0:8504/control")
    print(f"자동 종료: 매일 {AUTO_STOP_TIME.strftime('%H:%M')} KST")
    print("=" * 50)
    uvicorn.run(app, host="0.0.0.0", port=8504, log_level="warning")


if __name__ == "__main__":
    main()
