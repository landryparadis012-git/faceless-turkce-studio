"""Telegram listener: /start triggers produce; auto-resumes after daily-limit resets."""
import datetime, json, os, subprocess, urllib.request

API = f"https://api.telegram.org/bot{os.environ['TELEGRAM_BOT_TOKEN']}"
CHAT_ID = str(os.environ["TELEGRAM_CHAT_ID"])
OFFSET_FILE = "memory/tg_offset.txt"
FLAG_FILE = "memory/autoresume.flag"

def tg(method, **params):
    req = urllib.request.Request(f"{API}/{method}",
        data=json.dumps(params).encode(), headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=60).read())

def git(*args):
    subprocess.run(["git", *args], check=False)

def git_push_retry():
    for _ in range(3):
        if subprocess.run(["git", "push"], capture_output=True).returncode == 0:
            return True
        git("pull", "--rebase", "--autostash", "origin", "main")
    return False

def autoresume():
    if not os.path.exists(FLAG_FILE):
        return
    reset = open(FLAG_FILE).read().strip()
    now = datetime.datetime.now(datetime.timezone.utc)
    try:
        reset_dt = datetime.datetime.strptime(reset, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
    except Exception:
        reset_dt = now
    if now < reset_dt:
        print(f"[listen] autoresume waiting until {reset} UTC")
        return
    print("[listen] autoresume: limit window passed — clearing flag first (dedup), then dispatching")
    os.remove(FLAG_FILE)
    git("config", "user.name", "studio-bot")
    git("config", "user.email", "bot@users.noreply.github.com")
    git("add", "memory/autoresume.flag")
    git("commit", "-m", "autoresume consumed")
    if not git_push_retry():
        print("[listen] could not push flag removal — skipping dispatch this hour to avoid duplicates (will retry next hour)")
        return
    subprocess.run(["gh", "workflow", "run", "produce.yml", "--ref", "main"], check=True, env=os.environ)
    print("[listen] autoresume dispatched")

def main():
    autoresume()
    offset = 0
    try:
        offset = int(open(OFFSET_FILE).read().strip())
    except Exception:
        pass
    try:
        res = tg("getUpdates", offset=offset, timeout=0, allowed_updates=["message"])
    except Exception as e:
        print(f"[listen] getUpdates error (ignoring this round): {e}")
        return
    updates = res.get("result", [])
    if not updates:
        print("[listen] nothing new")
        return
    trigger = False
    new_offset = offset
    for u in updates:
        new_offset = max(new_offset, u["update_id"] + 1)
        msg = u.get("message") or {}
        if str(msg.get("chat", {}).get("id")) == CHAT_ID:
            text = (msg.get("text") or "").strip()
            if text.startswith("/start"):
                trigger = True
    os.makedirs("memory", exist_ok=True)
    open(OFFSET_FILE, "w").write(str(new_offset))
    if trigger:
        print("[listen] /start received — dispatching produce workflow")
        subprocess.run(["gh", "workflow", "run", "produce.yml", "--ref", "main"], check=True, env=os.environ)
        print("[listen] dispatched")
    else:
        print("[listen] updates seen, no /start")

main()
