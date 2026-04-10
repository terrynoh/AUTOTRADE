"""
매일 06:40 KST: 대시보드 Tunnel URL + 관리자 토큰을 텔레그램으로 발송.
cron: 40 6 * * * /home/ubuntu/AUTOTRADE/venv/bin/python /home/ubuntu/AUTOTRADE/scripts/send_dashboard_url.py
"""
import re
import sys
from pathlib import Path

AUTOTRADE_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = AUTOTRADE_DIR / ".env"
LOG_FILE = AUTOTRADE_DIR / "logs" / "autotrade.err"


def load_env(path: Path) -> dict:
    env = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip()
    return env


def get_latest_tunnel_url(log_path: Path) -> str:
    try:
        text = log_path.read_text(errors="ignore")
        matches = re.findall(r"https://[a-z0-9-]+\.trycloudflare\.com", text)
        return matches[-1] if matches else ""
    except Exception:
        return ""


def send_message(bot_token: str, chat_id: str, text: str) -> bool:
    import urllib.request
    import urllib.parse
    import json

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    data = json.dumps({"chat_id": chat_id, "text": text}).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
            return result.get("ok", False)
    except Exception as e:
        print(f"[ERROR] chat_id={chat_id}: {e}", file=sys.stderr)
        return False


def main():
    env = load_env(ENV_FILE)
    bot_token = env.get("TELEGRAM_BOT_TOKEN", "")
    chat_ids = [c.strip() for c in env.get("TELEGRAM_CHAT_ID", "").split(",") if c.strip()]
    admin_token = env.get("DASHBOARD_ADMIN_TOKEN", "")

    if not bot_token or not chat_ids:
        print("[ERROR] TELEGRAM_BOT_TOKEN 또는 TELEGRAM_CHAT_ID 미설정", file=sys.stderr)
        sys.exit(1)

    tunnel_url = get_latest_tunnel_url(LOG_FILE)

    if not tunnel_url:
        message = "⚠️ AUTOTRADE: Tunnel URL을 찾을 수 없습니다. 서비스 상태를 확인하세요.\n\nssh ubuntu@134.185.115.229 'sudo systemctl is-active autotrade'"
    else:
        display_url = f"{tunnel_url}?token={admin_token}" if admin_token else tunnel_url
        message = f"🕖 AUTOTRADE 대시보드 (06:40 KST)\n\n{display_url}"

    for chat_id in chat_ids:
        ok = send_message(bot_token, chat_id, message)
        status = "OK" if ok else "FAIL"
        print(f"[{status}] chat_id={chat_id}")


if __name__ == "__main__":
    main()
