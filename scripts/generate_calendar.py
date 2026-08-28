#!/usr/bin/env python3
"""
Generate an earnings-calendar ICS file using Finnhub API (US stocks), AKShare (A-shares),
and HKEXNews (Hong Kong stocks).

1. Fetch earnings for the coming 90 days (US stocks via Finnhub).
2. Fetch disclosure schedule for A-shares via AKShare.
3. Fetch HK earnings announcements & board-meeting notices via HKEXNews (HK stocks).
4. Convert each record to an all-day iCalendar event.
5. Write/overwrite earnings_calendar.ics in repository root.

Prerequisites:
  • FINNHUB_TOKEN must be provided as env var (for US stocks).
  • pip install -r requirements.txt
  • pip install akshare (for A-shares)
  • pip install pymupdf (for HK board-meeting PDF parsing)
"""

import json
import os
import re
import sys
import time as _time
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
WATCHLIST_HK_FILE = Path(__file__).parent.parent / "watchlist_hk.txt"
LOOKBEHIND_DAYS = 15                          # past earnings window
LOOKAHEAD_DAYS  = 90                          # upcoming earnings window (3 months)

# 港交所披露易（HKEXNews）公告标题搜索
HKEX_SEARCH_URL = "https://www1.hkexnews.hk/search/titleSearchServlet.do"
HKEX_FILE_BASE  = "https://www1.hkexnews.hk"
HKEX_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
HKEX_SLEEP = 1.5                               # 请求间隔，避免触发限流
HKEX_WINDOW_DAYS = 7                           # 搜索窗口（rowRange=1000 上限）
# 业绩公布公告标题关键词（搜过去窗口）
HKEX_PERF_TITLES = ["業績公布", "業績公佈", "業績公告", "INTERIM RESULTS", "ANNUAL RESULTS", "FINAL RESULTS"]
# 董事会会议通告标题关键词（搜未来窗口，预告业绩公布日）
HKEX_BOARD_TITLES = ["董事會會議通告", "董事會會議召開日期"]

TODAY = date.today()
FROM = (TODAY - timedelta(days=LOOKBEHIND_DAYS)).isoformat()
TO   = (TODAY + timedelta(days=LOOKAHEAD_DAYS)).isoformat()

# A股财报 period 配置（根据当前月份动态选择）
# 披露节奏：年报（次年1-4月）、一季报（4月）、半年报（7-8月）、三季报（10月）
# 预约披露表通常在披露季前公布，所以查询月份应覆盖披露季 ± 提前期，
# 具体记录再由 LOOKBEHIND/LOOKAHEAD 窗口过滤。
def get_cn_periods() -> list[str]:
    """Get relevant disclosure periods based on current date."""
    year = TODAY.year
    month = TODAY.month

    if month <= 4:                          # 1-4月: 上年年报 + 一季报
        return [f"{year - 1}年报", f"{year}一季"]
    if month <= 6:                          # 5-6月: 半年报预约表陆续公布
        return [f"{year}半年报"]
    if month <= 9:                          # 7-9月: 半年报披露季 + 三季报预约
        return [f"{year}半年报", f"{year}三季"]
    if month <= 10:                         # 10月: 三季报
        return [f"{year}三季"]
    return [f"{year}三季", f"{year}年报"]   # 11-12月: 三季报 + 次年年报预约

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


def load_watchlist_hk() -> set[str]:
    """Load HK symbols from watchlist_hk.txt, ignoring comments and empty lines.

    Accepts 00700 / 0700 / 700.HK / 00700.HK formats; normalizes to 5-digit.
    """
    if not WATCHLIST_HK_FILE.exists():
        print(f"⚠️  HK watchlist file not found: {WATCHLIST_HK_FILE}")
        return set()

    symbols = set()
    with open(WATCHLIST_HK_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                code = re.sub(r"\.HK$|\.hk$", "", line)
                code = re.sub(r"\D", "", code)
                if code:
                    symbols.add(code.zfill(5))
    return symbols


def normalize_hk_code(raw: str) -> str:
    """Normalize a HKEX stock code to 5-digit form ('700' -> '00700')."""
    return re.sub(r"\D", "", raw or "").zfill(5)


def hkex_search(title: str, from_date: str, to_date: str, rows: int = 1000,
                lang: str = "ZH") -> list[dict]:
    """Search HKEXNews announcement titles within [from_date, to_date].

    from_date/to_date are 'YYYYMMDD' strings. Uses title keyword (substring match).
    rowRange=1000 returns everything for a ≤7-day window (no pagination needed).
    """
    params = {
        "sortDir": "0",
        "sortByOptions": "DateTime",
        "category": "0",
        "market": "SEHK",
        "stockId": "-1",
        "documentType": "-1",
        "fromDate": from_date,
        "toDate": to_date,
        "title": title,
        "searchType": "1",
        "t1code": "-2",
        "t2Gcode": "-2",
        "t2code": "-2",
        "rowRange": str(rows),
        "lang": lang,
    }
    for attempt in range(3):
        try:
            resp = requests.get(HKEX_SEARCH_URL, params=params, timeout=30,
                                headers={"User-Agent": HKEX_UA})
            resp.raise_for_status()
            data = resp.json()
            result = data.get("result")
            if result:
                return json.loads(result)
            # 空结果：可能是限流（HKEXNews 对频繁请求返回空 result），重试
            if attempt < 2:
                print(f"      [retry {attempt + 1}] {title} {from_date}~{to_date}: 空结果（可能限流）")
        except Exception as e:
            print(f"      [retry {attempt + 1}] {title} {from_date}~{to_date}: {e}")
        _time.sleep(HKEX_SLEEP * (attempt + 3))
    return []


def hkex_search_windows(keywords: list[str], start: date, end: date,
                        lang: str = "ZH") -> list[dict]:
    """Search HKEXNews over date range in ≤7-day chunks, dedup by (code, title, time)."""
    all_rows: list[dict] = []
    seen = set()
    for kw in keywords:
        current = start
        while current < end:
            win_end = min(current + timedelta(days=HKEX_WINDOW_DAYS), end)
            rows = hkex_search(kw, current.strftime("%Y%m%d"), win_end.strftime("%Y%m%d"), lang=lang)
            for row in rows:
                key = (row.get("TITLE"), row.get("DATE_TIME"), row.get("STOCK_CODE"))
                if key not in seen:
                    seen.add(key)
                    all_rows.append(row)
            current = win_end
            _time.sleep(HKEX_SLEEP)
    return all_rows


def report_type_from_title(title: str) -> str:
    """Guess report type (中期/全年/季度) from an announcement title."""
    if "中期" in title:
        return "中期业绩"
    if re.search(r"半年", title):
        return "中期业绩"
    if re.search(r"全年|末期|年度", title):
        return "全年业绩"
    if "季度" in title or "季報" in title:
        return "季度业绩"
    return "业绩公布"


# 中文数字解析（港交所公告日期）
_CN_DIGIT = {"零": 0, "〇": 0, "○": 0, "一": 1, "二": 2, "两": 2,
             "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_EN_MONTH = {"january": 1, "february": 2, "march": 3, "april": 4, "may": 5,
             "june": 6, "july": 7, "august": 8, "september": 9, "october": 10,
             "november": 11, "december": 12}


def _cn_to_int(s: str) -> int | None:
    if not s:
        return None
    if s == "十":
        return 10
    if s.startswith("十"):
        return 10 + _CN_DIGIT.get(s[1], 0)
    if "十" in s:
        t, u = s.split("十", 1)
        return _CN_DIGIT.get(t, 0) * 10 + _CN_DIGIT.get(u, 0)
    if len(s) >= 2 and all(c in _CN_DIGIT for c in s):   # 年份：二零二六 → 2026
        val = 0
        for c in s:
            val = val * 10 + _CN_DIGIT[c]
        return val
    return _CN_DIGIT.get(s)


_HK_DATE_PATTERNS = [
    re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日"),
    re.compile(r"([零〇○一二三四五六七八九两]{4})\s*年\s*([零〇○一二三四五六七八九十]{1,3})\s*月\s*([零〇○一二三四五六七八九十]{1,3})\s*日"),
    re.compile(r"(\d{1,2})(?:st|nd|rd|th)?\s+(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{4})", re.I),
    re.compile(r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})", re.I),
]


def _parse_date_match(pat: re.Pattern, m: re.Match) -> str | None:
    if pat is _HK_DATE_PATTERNS[0]:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    if pat is _HK_DATE_PATTERNS[1]:
        y, mo, d = _cn_to_int(m.group(1)), _cn_to_int(m.group(2)), _cn_to_int(m.group(3))
        if not (y and mo and d):
            return None
        return f"{y:04d}-{mo:02d}-{d:02d}"
    if pat is _HK_DATE_PATTERNS[2]:
        return f"{int(m.group(3)):04d}-{_EN_MONTH[m.group(2).lower()]:02d}-{int(m.group(1)):02d}"
    return f"{int(m.group(3)):04d}-{_EN_MONTH[m.group(1).lower()]:02d}-{int(m.group(2)):02d}"


def extract_board_meeting_date(pdf_text: str) -> str | None:
    """Extract the earnings-related board meeting date from an announcement PDF text.

    Strategy: collapse whitespace (keeps single spaces for English), find date
    patterns, and accept a date only when its ±150-char context mentions a board
    meeting + earnings. Returns 'YYYY-MM-DD' or None.
    """
    flat = re.sub(r"\s+", " ", pdf_text)
    for pat in _HK_DATE_PATTERNS:
        for m in pat.finditer(flat):
            d = _parse_date_match(pat, m)
            if not d or not (2000 <= int(d[:4]) <= 2030):
                continue
            ctx = flat[max(0, m.start() - 150):m.end() + 150]
            has_meeting = (
                "董事會會議" in ctx
                or bool(re.search(r"舉行.{0,6}會議|召開.{0,6}會議", ctx))
                or bool(re.search(r"meeting of the board|board meeting|will be held", ctx, re.I))
            )
            has_perf = bool(re.search(r"業績|財務報表|中期報告|年度報告|季度|財務業績|result", ctx, re.I))
            if has_meeting and has_perf:
                return d
    return None


def fetch_hk_earnings(watchlist_hk: set[str]) -> list[dict]:
    """Fetch HK earnings events from HKEXNews for the watchlist.

    1. Past window (LOOKBEHIND_DAYS): earnings announcements already published.
    2. Future window (LOOKAHEAD_DAYS): board-meeting notices whose PDF reveals
       the upcoming results date (HK has no A-share-style pre-schedule).

    Returns records with source 'hk' (announcement) or 'hk-board' (meeting notice).
    """
    if not watchlist_hk:
        print("  🇭🇰  No HK watchlist configured")
        return []

    from_date = TODAY - timedelta(days=LOOKBEHIND_DAYS)
    to_date = TODAY + timedelta(days=LOOKAHEAD_DAYS)
    records: list[dict] = []

    # ── 1) 已发布的业绩公布公告 ─────────────────────────────────────────────
    print("  🇭🇰  搜索已发布业绩公布公告（过去15天）…")
    rows = hkex_search_windows(HKEX_PERF_TITLES, from_date, TODAY)
    matched = 0
    for row in rows:
        codes = [normalize_hk_code(c) for c in row.get("STOCK_CODE", "").split("<br/>")]
        hit = next((c for c in codes if c in watchlist_hk), None)
        if not hit:
            continue
        try:
            event_date = datetime.strptime(row["DATE_TIME"], "%d/%m/%Y %H:%M").date()
        except (KeyError, ValueError):
            continue
        name = row.get("STOCK_NAME", "").split("<br/>")[0].strip()
        title = row.get("TITLE", "").replace("<br/>", " ")
        records.append({
            "symbol": hit,
            "name": name,
            "date": event_date.isoformat(),
            "report_type": report_type_from_title(title),
            "title": title,
            "source": "hk",
        })
        matched += 1
    print(f"      {matched} 条匹配 watchlist")

    # ── 2) 董事会会议通告（未来业绩日预告）────────────────────────────────
    print("  🇭🇰  搜索董事会会议通告（未来90天）…")
    board_rows = hkex_search_windows(HKEX_BOARD_TITLES, TODAY, to_date)
    board_candidates = []
    for row in board_rows:
        codes = [normalize_hk_code(c) for c in row.get("STOCK_CODE", "").split("<br/>")]
        hit = next((c for c in codes if c in watchlist_hk), None)
        if hit:
            board_candidates.append((hit, row))

    print(f"      {len(board_candidates)} 条 watchlist 通告，解析 PDF 会议日期…")
    pdf_ok = 0
    for hit, row in board_candidates:
        link = row.get("FILE_LINK", "")
        if not link:
            continue
        try:
            resp = requests.get(HKEX_FILE_BASE + link, timeout=30, headers={"User-Agent": HKEX_UA})
            if resp.content[:5] != b"%PDF-":
                continue
            import pymupdf
            doc = pymupdf.open(stream=resp.content, filetype="pdf")
            text = "\n".join(page.get_text() for page in doc)
            meeting_date = extract_board_meeting_date(text)
            if not meeting_date:
                print(f"      [!] {hit} 通告未提取到会议日期（可能为扫描件）")
                _time.sleep(HKEX_SLEEP)
                continue
            # 会议日期必须在未来窗口内
            d = date.fromisoformat(meeting_date)
            if not (from_date <= d <= to_date):
                _time.sleep(HKEX_SLEEP)
                continue
            # 报告期：从通告文本推断（截至X月X日止六個月 → 中期）
            if re.search(r"六個月|六个月|中期", text):
                rtype = "中期业绩"
            elif re.search(r"全年|年度|末期", text):
                rtype = "全年业绩"
            else:
                rtype = "业绩公布"
            name = row.get("STOCK_NAME", "").split("<br/>")[0].strip()
            title = row.get("TITLE", "").replace("<br/>", " ")
            records.append({
                "symbol": hit,
                "name": name,
                "date": meeting_date,
                "report_type": rtype,
                "title": title,
                "source": "hk-board",
            })
            pdf_ok += 1
        except Exception as e:
            print(f"      [!] {hit} PDF 解析失败: {e}")
        _time.sleep(HKEX_SLEEP)
    print(f"      {pdf_ok} 条解析出会议日期")

    # 去重：同代码同日期的董事会通告事件，被业绩公告事件覆盖
    seen_dates = {(r["symbol"], r["date"]) for r in records if r["source"] == "hk"}
    deduped = [r for r in records if r["source"] != "hk-board" or (r["symbol"], r["date"]) not in seen_dates]
    return deduped


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


def fetch_yfinance_earnings(
    watchlist: set[str], existing_symbols: set[str]
) -> list[dict]:
    """Fallback: use yfinance for tickers Finnhub didn't cover.

    yfinance 1.2.0+ provides ``Ticker.calendar`` as a dict with keys
    like ``Earnings Date`` (list of date), ``Earnings Average``, etc.

    Note: exclusion is by symbol only (not symbol+date). If Finnhub
    returned any event for a ticker, yfinance is skipped for that
    ticker entirely.  This avoids redundant queries at the cost of
    potentially missing a *different* earnings date for the same
    ticker within the window.

    Args:
        watchlist: All US tickers of interest.
        existing_symbols: Tickers already found by Finnhub (skip these).

    Returns:
        List of records in the same format as Finnhub.
    """
    import time as _time
    import yfinance as yf

    missing = watchlist - existing_symbols
    if not missing:
        return []

    from_date = TODAY - timedelta(days=LOOKBEHIND_DAYS)
    to_date = TODAY + timedelta(days=LOOKAHEAD_DAYS)
    records: list[dict] = []

    print(f"\n  🐍  yfinance fallback for {len(missing)} tickers…")
    for i, symbol in enumerate(sorted(missing), 1):
        try:
            ticker = yf.Ticker(symbol)
            cal = ticker.calendar
        except Exception as e:
            print(f"    [!] {symbol}: {e}")
            _time.sleep(0.5)
            continue

        if not cal:
            print(f"    [!] {symbol}: empty calendar data")
            _time.sleep(0.5)
            continue

        # ``Earnings Date`` is a list of date objects
        raw_dates = cal.get("Earnings Date")
        if not raw_dates:
            _time.sleep(0.5)
            continue

        event_date = raw_dates[0]
        if isinstance(event_date, datetime):
            event_date = event_date.date()
        if not (from_date <= event_date <= to_date):
            _time.sleep(0.5)
            continue

        records.append({
            "symbol": symbol,
            "date": event_date.isoformat(),
            "hour": "",
            "quarter": "",
            "epsEstimate": cal.get("Earnings Average"),
            "revenueEstimate": cal.get("Revenue Average"),
            "source": "yf",
        })
        print(f"    [{i}/{len(missing)}] {symbol}: {event_date}")
        _time.sleep(0.5)

    print(f"  🐍  yfinance found {len(records)} tickers")
    return records


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

                # A股财报实际是在披露日期前一天晚上公布
                # 例如：披露日期显示20260430，实际20260429晚上发布
                event_date = event_date - timedelta(days=1)

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
            # akshare 对空预约数据的已知报错（巨潮未发布该 period 预约表时），视为无数据
            if "Length mismatch" in str(e):
                print(f"      {period}: 暂无预约披露数据")
            else:
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

    source_label = "yfinance" if item.get("source") == "yf" else "Finnhub (non-GAAP)"

    description = "\n".join(
        [
            f"Ticker: {symbol}",
            f"Fiscal Qtr: {item.get('quarter', '-')}",
            f"Timing: {timing if timing else '未指定'}",
            f"Estimate EPS: {item.get('epsEstimate') if item.get('epsEstimate') is not None else '-'}",
            f"Est. Revenue: {fmt_number(item.get('revenueEstimate'))}",
            f"Source: {source_label}",
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

    # Build summary with stock name only
    report_type = item.get("report_type", "财报")
    summary = f"{name} {report_type}"

    description = "\n".join(
        [
            f"股票代码: {symbol}",
            f"股票简称: {name}",
            f"报告类型: {report_type}",
            f"披露日期: {event_date.isoformat()}",
            "Source: AKShare (巨潮资讯)",
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


def to_hk_event_lines(item: dict, dtstamp: str) -> list[str]:
    """Convert one HK record into RFC5545 VEVENT lines."""
    symbol = item.get("symbol", "UNKNOWN")
    name = item.get("name", "")
    event_date = datetime.fromisoformat(item["date"]).date()
    end_date = event_date + timedelta(days=1)
    uid = f"HK-{symbol}-{event_date.isoformat()}@earning-calendar-ics"

    report_type = item.get("report_type", "业绩公布")
    is_board = item.get("source") == "hk-board"
    summary = f"{name} {report_type}"
    if is_board:
        summary = f"{name} {report_type}（待公布）"

    description = "\n".join(
        [
            f"股票代码: {symbol}",
            f"股票简称: {name}",
            f"报告类型: {report_type}",
            f"事件日期: {event_date.isoformat()}",
            f"公告标题: {item.get('title', '-')}",
            f"Source: HKEXNews (港交所披露易){' · 董事会会议通告' if is_board else ''}",
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
        elif rec.get("source") in ("hk", "hk-board"):
            lines.extend(to_hk_event_lines(rec, dtstamp))
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
        found_symbols = {r.get("symbol", "").upper() for r in filtered}
        print(f"📋  美股 Watchlist: {len(watchlist)} symbols, matched {len(filtered)} events")
        all_records.extend(filtered)

        # yfinance fallback for tickers Finnhub didn't cover
        yf_records = fetch_yfinance_earnings(watchlist, found_symbols)
        # Deduplicate against finnhub results by (symbol, date)
        yf_seen = set()
        for r in yf_records:
            key = (r.get("symbol"), r.get("date"))
            if key not in yf_seen:
                yf_seen.add(key)
                all_records.append(r)
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

    # === 港股 ===
    print()
    print("🇭🇰  获取港股业绩...")
    watchlist_hk = load_watchlist_hk()
    hk_records = fetch_hk_earnings(watchlist_hk)
    print(f"📋  港股 Watchlist: {len(watchlist_hk)} symbols, matched {len(hk_records)} events")
    all_records.extend(hk_records)

    # === 输出 ===
    out_path = "earnings_calendar.ics"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(build_calendar(all_records))
    n_us = len([r for r in all_records if r.get("source") in (None, "", "yf")])
    n_cn = len([r for r in all_records if r.get("source") == "cn"])
    n_hk = len([r for r in all_records if r.get("source") in ("hk", "hk-board")])
    print()
    print(f"✅  Calendar refreshed ({len(all_records)} events: {n_us} US + {n_cn} CN + {n_hk} HK) → {out_path}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("💥  Script failed:", exc)
        sys.exit(1)
