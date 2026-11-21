#!/usr/bin/env python3
"""Check film playlist URLs and write a conservative status report."""
import concurrent.futures
import datetime as dt
import json
import urllib.error
import urllib.request
from pathlib import Path

PLAYLIST = Path("films.m3u8")
OUTPUT = Path("link-status.json")
REVIEW_DIR = Path("needs-human-intervention")
TIMEOUT = 12
WORKERS = 12

def parse_playlist(text):
    items, title, group = [], "Без названия", ""
    for raw in text.lstrip("\ufeff").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#EXTINF"):
            title = line.rsplit(",", 1)[-1].strip() if "," in line else "Без названия"
        elif line.startswith("#EXTGRP:"):
            group = line.split(":", 1)[1].strip()
        elif not line.startswith("#") and line.lower().startswith(("http://", "https://")):
            items.append({"title": title, "group": group, "url": line})
            title, group = "Без названия", ""
    return items

def request_url(url, method):
    req = urllib.request.Request(url, method=method, headers={
        "User-Agent": "MoyaMediateka-LinkHealth/1.0",
        "Range": "bytes=0-0",
        "Accept": "*/*",
    })
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as response:
            return response.status, response.geturl()
    except urllib.error.HTTPError as exc:
        return exc.code, url
    except Exception:
        return None, url

def check(item):
    code, final_url = request_url(item["url"], "HEAD")
    if code in (405, 501) or code is None:
        code, final_url = request_url(item["url"], "GET")
    if code is not None and 200 <= code < 400:
        state = "available"
    elif code in (404, 410):
        state = "unavailable"
    else:
        state = "unknown"
    return {**item, "status": state, "http_status": code, "final_url": final_url}

def main():
    if not PLAYLIST.exists():
        raise SystemExit("films.m3u8 not found")
    items = parse_playlist(PLAYLIST.read_text(encoding="utf-8-sig", errors="replace"))
    with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as pool:
        checked = list(pool.map(check, items))
    counts = {key: sum(x["status"] == key for x in checked)
              for key in ("available", "unavailable", "unknown")}
    report = {
        "checked_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "total": len(checked),
        "counts": counts,
        "items": checked,
        "note": "HTTP reachability only; playback must be confirmed in the app."
    }
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    # Keep confirmed dead links in a separate human-review folder; never delete
    # them from the source playlist or silently replace their addresses.
    failed = [item for item in checked if item["status"] == "unavailable"]
    REVIEW_DIR.mkdir(parents=True, exist_ok=True)
    playlist_lines = ["#EXTM3U"]
    for item in failed:
        title = item["title"].replace("\\r", " ").replace("\\n", " ").replace(",", " ")
        group = item.get("group", "Требует вмешательства человека").replace("\\r", " ").replace("\\n", " ")
        playlist_lines.extend([f"#EXTINF:-1 group-title=\\"{group}\\",{title}", item["url"]])
    (REVIEW_DIR / "films.m3u8").write_text("\\n".join(playlist_lines) + "\\n", encoding="utf-8")
    readme = [
        "# Требует вмешательства человека",
        "",
        f"Последняя проверка: {report['checked_at']}",
        f"Фильмов в папке: {len(failed)}",
        "",
        "Сюда попадают только адреса, вернувшие HTTP 404 или 410. Проверьте источник, найдите правильный адрес и вручную подтвердите замену.",
        "Исходный каталог не изменяется автоматически. Ошибки доступа, тайм-ауты и прочие неопределённые ответы не считаются подтверждённо нерабочими.",
        "",
        "Отдельный плейлист: films.m3u8",
        ""
    ]
    (REVIEW_DIR / "README.md").write_text("\\n".join(readme), encoding="utf-8")
    print(f"Checked {len(checked)} URLs: {counts}; human review: {len(failed)}")

if __name__ == "__main__":
    main()
