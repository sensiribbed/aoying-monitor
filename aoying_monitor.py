"""Run with TG_BOT_TOKEN and TG_CHAT_ID set, or use --preview."""
import argparse
import hashlib
import json
import os
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

URL = "https://www.binance.com/bapi/futures/v1/friendly/future/copy-trade/lead-portfolio/order-history"
TZ = timezone(timedelta(hours=8))


class BinanceBusy(ValueError):
    pass


def post(url, payload):
    req = Request(url, json.dumps(payload).encode(), {"Content-Type": "application/json"})
    with urlopen(req, timeout=15) as response:
        return json.load(response)


def orders(start, end, latest_only=False):
    # Work backwards when a burst exceeds one page. Overlap the boundary millisecond.
    found = {}
    while end >= start:
        result = post(URL, dict(portfolioId="5075281354358777856", startTime=start,
                                endTime=end, pageSize=10))
        code = str(result.get("code"))
        if code != "000000":
            detail = f"币安 {code}：{result.get('message')}"
            if code == "11012005":
                raise BinanceBusy(detail)
            raise ValueError(detail)
        rows = result["data"]["list"]
        for row in rows:
            key = hashlib.sha256(json.dumps(row, sort_keys=True).encode()).hexdigest()
            found[key] = row
        if latest_only or len(rows) < 10 or len(rows) >= int(result["data"]["total"]):
            break
        oldest = min(row["orderUpdateTime"] for row in rows)
        if oldest >= end:
            raise ValueError("同一毫秒内记录超过分页能力，请检查成交列表")
        end = oldest
    return sorted(found.items(), key=lambda pair: pair[1]["orderUpdateTime"])


def message(row):
    actions = {("SELL", "SHORT"): "开空", ("BUY", "SHORT"): "平空",
               ("BUY", "LONG"): "开多", ("SELL", "LONG"): "平多"}
    action = actions.get((row["side"], row["positionSide"]), row["side"])
    stamp = datetime.fromtimestamp(row["orderUpdateTime"] / 1000, TZ)
    return (f"熬鹰资本 · {action} {row['symbol']}\n"
            f"成交量 {row['executedQty']} {row['baseAsset']}｜均价 {row['avgPrice']} {row['quoteAsset']}\n"
            f"订单更新 {stamp:%Y-%m-%d %H:%M:%S} UTC+8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--preview", action="store_true")
    args = parser.parse_args()
    now = int(time.time() * 1000)
    if args.preview:
        for attempt in range(4):
            try:
                rows = orders(now - 6 * 86400000, now, latest_only=True)
                break
            except BinanceBusy as exc:
                if attempt == 3:
                    parser.exit(1, f"{exc}\n")
                delay = 3 * 2**attempt
                print(f"{exc}，{delay} 秒后重试", flush=True)
                time.sleep(delay)
        print(message(rows[-1][1]) if rows else "近六天暂无记录")
        return
    token, chat = os.environ["TG_BOT_TOKEN"], os.environ["TG_CHAT_ID"]
    db = sqlite3.connect(os.environ.get("AOYING_DB_PATH", Path(__file__).with_suffix(".sqlite3")))
    db.execute("CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, body TEXT, sent INTEGER)")
    db.execute("CREATE TABLE IF NOT EXISTS cursor (t INTEGER)")
    delay = 3
    while True:
        try:
            end = int(time.time() * 1000)
            cursor = db.execute("SELECT t FROM cursor").fetchone()
            start = (cursor[0] if cursor else end) - 3600000
            with db:
                for key, row in orders(start, end):
                    db.execute("INSERT OR IGNORE INTO events VALUES (?, ?, ?)",
                               (key, message(row), int(cursor is None)))
                db.execute("DELETE FROM cursor")
                db.execute("INSERT INTO cursor VALUES (?)", (end,))
            for key, body in db.execute("SELECT id, body FROM events WHERE sent=0 ORDER BY rowid"):
                reply = post(f"https://api.telegram.org/bot{token}/sendMessage",
                             dict(chat_id=chat, text=body))
                if not reply.get("ok"):
                    raise ValueError("Telegram 未确认发送")
                with db:
                    db.execute("UPDATE events SET sent=1 WHERE id=?", (key,))
            delay = 3
        except (OSError, ValueError, KeyError, URLError) as exc:
            delay = min(120, delay * 2)
            if isinstance(exc, HTTPError):
                print(f"HTTP {exc.code}", flush=True)
                if exc.code == 429:
                    delay = max(60, delay, int(exc.headers.get("Retry-After", "0")))
            elif isinstance(exc, ValueError):
                print(f"{exc}，{delay} 秒后重试", flush=True)
            else:
                print(type(exc).__name__, flush=True)
        time.sleep(delay)


if __name__ == "__main__":
    main()

