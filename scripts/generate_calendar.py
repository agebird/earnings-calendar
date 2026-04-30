#!/usr/bin/env python3
"""
Generate an earnings-calendar ICS file using Finnhub API (US stocks) and AKShare (A-shares).

1. Fetch earnings for the coming 90 days (US stocks via Finnhub).
2. Fetch disclosure schedule for A-shares via AKShare.
3. Convert each record to an all-day iCalendar event.
4. Write/overwrite earnings_calendar.ics in repository root.

Prerequisites:
  • FINNHUB_TOKEN must be provided as env var (for US stocks).
  • pip install -r requirements.txt
  • pip install akshare (for A-shares)
"""

import os
import sys
from datetime import date, timedelta, datetime, timezone
from pathlib import Path
import pandas as pd

import requests

# ────────────────────────────────────────────────────────────────────────────────
# Config
API = "https://finnhub.io/api/v1/calendar/earnings"
TOKEN = os.getenv("FINNHUB_TOKEN")
WATCHLIST_FILE = Path(__file__).parent.parent / "watchlist.txt"
WATCHLIST_CN_FILE = Path(__file__).parent.parent / "watchlist_cn.txt"
LOOKBEHIND_DAYS = 15                          # past earnings window
LOOKAHEAD_DAYS  = 90                          # upcoming earnings window (3 months)

TODAY = date.today()
FROM = (TODAY - timedelta(days=LOOKBEHIND_DAYS)).isoformat()
TO   = (TODAY + timedelta(days=LOOKAHEAD_DAYS)).isoformat()

# A股财报 period 配置（根据当前月份动态选择）
def get_cn_periods() -> list[str]:
    """Get relevant disclosure periods based on current date."""
    year = TODAY.year
    month = TODAY.month
    periods = []

    # 根据月份确定需要获取的财报周期
    # 年报（次年1-4月披露）、一季报（4月披露）、半年报（7-8月披露）、三季报（10月披露）
    if month <= 4:
        periods.append(f"{year - 1}年报")  # 前年年报（今年披露）
        periods.append(f"{year}一季")       # 今年一季报
    elif month <= 8:
        periods.append(f"{year}一季")       # 今年一季报
        periods.append(f"{year - 1}年报")   # 前年年报（可能延期披露）
    elif month <= 10:
        periods.append(f"{year}三季")       # 今年三季报
    else:
        periods.append(f"{year}三季")       # 今年三季报
        periods.append(f"{year}年报")       # 今年年报（次年披露）

    return periods

# ────────────────────────────────────────────────────────────────────────────────
# Helpers
def load_watchlist() -> set[str]:
    """Load symbols from watchlist.txt, ignoring comments and empty lines."""
    if not WATCHLIST_FILE.exists():
        print(f"⚠️  Watchlist file not found: {WATCHLIST_FILE}")
        return set()

    symbols = set()
    with open(WATCHLIST_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                symbols.add(line.upper())
    return symbols


def load_watchlist_cn() -> set[str]:
    """Load A-share symbols from watchlist_cn.txt, ignoring comments and empty lines."""
    if not WATCHLIST_CN_FILE.exists():
        print(f"⚠️  A-share watchlist file not found: {WATCHLIST_CN_FILE}")
        return set()

    symbols = set()
    with open(WATCHLIST_CN_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                # 去掉可能的前缀（如 sh/sz/bj）
                code = line.replace("sh", "").replace("sz", "").replace("bj", "")
                symbols.add(code)
    return symbols


def fmt_number(num):
    """
    Abbreviate big numbers with B/M.
    e.g. 1_234_567_890 -> '1.23 B', 456_000_000 -> '456 M'
    Returns '-' if value is None/invalid/zero.
    """
    if num in (None, 0, "0"):
        return "-"
    try:
        n = float(num)
    except (ValueError, TypeError):
        return "-"
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.2f} B"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.0f} M"
    return f"{n:.0f}"


def fetch_earnings() -> list[dict]:
    """Call Finnhub with chunked requests to avoid API limit (1500 records max)."""
    if not TOKEN:
        raise RuntimeError("FINNHUB_TOKEN env-var is missing.")

    # Split requests into 15-day chunks to avoid 1500 record limit
    chunk_size = 15
    start_date = date.fromisoformat(FROM)
    end_date = date.fromisoformat(TO)

    all_records = []
    current = start_date

    while current < end_date:
        chunk_end = min(current + timedelta(days=chunk_size), end_date)
        params = {
            "from": current.isoformat(),
            "to": chunk_end.isoformat(),
            "token": TOKEN,
        }
        resp = requests.get(API, params=params, timeout=30)
        resp.raise_for_status()
        records = resp.json().get("earningsCalendar", [])
        all_records.extend(records)
        print(f"  📥  {current.isoformat()} ~ {chunk_end.isoformat()}: {len(records)} records")
        current = chunk_end

    # Deduplicate by symbol + date
    seen = set()
    unique_records = []
    for r in all_records:
        key = (r.get("symbol"), r.get("date"))
        if key not in seen:
            seen.add(key)
            unique_records.append(r)

    return unique_records


def fetch_cn_earnings(watchlist_cn: set[str]) -> list[dict]:
    """Fetch A-share disclosure schedule via AKShare."""
    if not watchlist_cn:
        print("  🇨🇳  No A-share watchlist configured")
        return []

    try:
        import akshare as ak
    except ImportError:
        print("  ⚠️  AKShare not installed, skipping A-share data")
        return []

    periods = get_cn_periods()
    all_records = []

    for period in periods:
        try:
            print(f"  🇨🇳  获取 {period} 财报披露时间...")
            df = ak.stock_report_disclosure(market="沪深京", period=period)

            # Filter by watchlist
            df_filtered = df[df["股票代码"].isin(watchlist_cn)]

            for _, row in df_filtered.iterrows():
                # 使用实际披露日期，如果还没有披露则用首次预约日期
                disclosure_date = row.get("实际披露")
                if pd.isna(disclosure_date):
                    disclosure_date = row.get("首次预约")

                if pd.isna(disclosure_date):
                    continue

                # 转换日期格式
                if isinstance(disclosure_date, date):
                    event_date = disclosure_date
                else:
                    try:
                        event_date = pd.to_datetime(disclosure_date).date()
                    except:
                        continue

                # 只保留时间窗口内的记录
                from_date = TODAY - timedelta(days=LOOKBEHIND_DAYS)
                to_date = TODAY + timedelta(days=LOOKAHEAD_DAYS)
                if event_date < from_date or event_date > to_date:
                    continue

                # 提取报告类型
                report_type = period.replace("年", "年").replace("季", "季报")
                if "报" not in report_type:
                    report_type += "报"

                record = {
                    "symbol": row["股票代码"],
                    "name": row["股票简称"],
                    "date": event_date.isoformat(),
                    "period": period,
                    "report_type": report_type,
                    "source": "cn",
                }
                all_records.append(record)

            print(f"      {period}: {len(df_filtered)} 条匹配")

        except Exception as e:
            print(f"      {period}: 错误 - {e}")

    return all_records


def escape_ics_text(value: str) -> str:
    """Escape text according to RFC5545."""
    return (
        value.replace("\\", "\\\\")
        .replace(";", r"\;")
        .replace(",", r"\,")
        .replace("\n", r"\n")
    )


def fold_ics_line(line: str, width: int = 75) -> list[str]:
    """Fold long iCalendar lines (continuation starts with one space)."""
    if len(line) <= width:
        return [line]
    folded = [line[:width]]
    rest = line[width:]
    while rest:
        folded.append(f" {rest[: width - 1]}")
        rest = rest[width - 1 :]
    return folded


def to_event_lines(item: dict, dtstamp: str) -> list[str]:
    """Convert one Finnhub record into RFC5545 VEVENT lines."""
    symbol = item.get("symbol", "UNKNOWN")
    event_date = datetime.fromisoformat(item["date"]).date()
    end_date = event_date + timedelta(days=1)  # all-day events use exclusive end
    uid = f"{symbol}-{event_date.isoformat()}@earning-calendar-ics"

    # Parse hour field: bmo = 盘前, amc = 盘后
    hour = item.get("hour", "")
    hour_map = {"bmo": "盘前", "amc": "盘后", "": ""}
    timing = hour_map.get(hour, "")

    # Build summary with timing
    summary = f"{symbol} Earnings"
    if timing:
        summary = f"{symbol} Earnings ({timing})"

    description = "\n".join(
        [
            f"Ticker: {symbol}",
            f"Fiscal Qtr: {item.get('quarter', '-')}",
            f"Timing: {timing if timing else '未指定'}",
            f"Estimate EPS: {item.get('epsEstimate', '-')}",
            f"Est. Revenue: {fmt_number(item.get('revenueEstimate'))}",
            "Source: Finnhub (non-GAAP)",
        ]
    )

    return [
        "BEGIN:VEVENT",
        f"UID:{escape_ics_text(uid)}",
        f"DTSTAMP:{dtstamp}",
        f"DTSTART;VALUE=DATE:{event_date.strftime('%Y%m%d')}",
        f"DTEND;VALUE=DATE:{end_date.strftime('%Y%m%d')}",
        f"SUMMARY:{escape_ics_text(summary)}",
        f"DESCRIPTION:{escape_ics_text(description)}",
        "END:VEVENT",
    ]


def to_cn_event_lines(item: dict, dtstamp: str) -> list[str]:
    """Convert one A-share record into RFC5545 VEVENT lines."""
    symbol = item.get("symbol", "UNKNOWN")
    name = item.get("name", "")
    event_date = datetime.fromisoformat(item["date"]).date()
    end_date = event_date + timedelta(days=1)
    uid = f"CN-{symbol}-{event_date.isoformat()}@earning-calendar-ics"

    # Build summary with stock name
    report_type = item.get("report_type", "财报")
    summary = f"[A股] {symbol} {name} {report_type}"

    description = "\n".join(
        [
            f"股票代码: {symbol}",
            f"股票简称: {name}",
            f"报告类型: {report_type}",
            f"披露日期: {event_date.isoformat()}",
            "Source: AKShare (东方财富)",
        ]
    )

    return [
        "BEGIN:VEVENT",
        f"UID:{escape_ics_text(uid)}",
        f"DTSTAMP:{dtstamp}",
        f"DTSTART;VALUE=DATE:{event_date.strftime('%Y%m%d')}",
        f"DTEND;VALUE=DATE:{end_date.strftime('%Y%m%d')}",
        f"SUMMARY:{escape_ics_text(summary)}",
        f"DESCRIPTION:{escape_ics_text(description)}",
        "END:VEVENT",
    ]


def build_calendar(records: list[dict]) -> str:
    """Build a full iCalendar payload with all records."""
    dtstamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//earning-calendar-ics//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:Earnings Calendar",
    ]

    for rec in sorted(records, key=lambda r: (r.get("date", ""), r.get("symbol", ""))):
        if not rec.get("date"):
            continue
        # Use different conversion function based on source
        if rec.get("source") == "cn":
            lines.extend(to_cn_event_lines(rec, dtstamp))
        else:
            lines.extend(to_event_lines(rec, dtstamp))

    lines.append("END:VCALENDAR")

    folded_lines: list[str] = []
    for line in lines:
        folded_lines.extend(fold_ics_line(line))

    return "\r\n".join(folded_lines) + "\r\n"


# ────────────────────────────────────────────────────────────────────────────────
def main() -> None:
    all_records = []

    # === 美股 ===
    print("🇺🇸  获取美股财报...")
    watchlist = load_watchlist()
    us_records = fetch_earnings()

    if watchlist:
        filtered = [r for r in us_records if r.get("symbol", "").upper() in watchlist]
        print(f"📋  美股 Watchlist: {len(watchlist)} symbols, matched {len(filtered)} events")
        all_records.extend(filtered)
    else:
        print(f"📋  No US watchlist configured, using all {len(us_records)} events")
        all_records.extend(us_records)

    # === A股 ===
    print()
    print("🇨🇳  获取A股财报...")
    watchlist_cn = load_watchlist_cn()
    cn_records = fetch_cn_earnings(watchlist_cn)
    print(f"📋  A股 Watchlist: {len(watchlist_cn)} symbols, matched {len(cn_records)} events")
    all_records.extend(cn_records)

    # === 输出 ===
    out_path = "earnings_calendar.ics"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(build_calendar(all_records))
    print()
    print(f"✅  Calendar refreshed ({len(all_records)} events: {len([r for r in all_records if r.get('source') != 'cn'])} US + {len([r for r in all_records if r.get('source') == 'cn'])} CN) → {out_path}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("💥  Script failed:", exc)
        sys.exit(1)
