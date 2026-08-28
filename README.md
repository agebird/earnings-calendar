# 📅 Earnings Calendar (.ics Generator)

This project automatically generates a `.ics` calendar file containing earnings release events for three markets:

- 🇺🇸 **US stocks** — [Finnhub API](https://finnhub.io/) (+ yfinance fallback)
- 🇨🇳 **A-shares** — AKShare (巨潮资讯披露时间)
- 🇭🇰 **Hong Kong stocks** — 港交所披露易 HKEXNews（业绩公布公告 + 董事会会议通告）

The file is updated **twice daily** via GitHub Actions and is compatible with iOS/macOS/Google calendars. Useful for investors who want earnings events directly in their calendar apps.

---

## ✅ Features

- Updates **twice per day** (10:00 and 22:00 Beijing Time)
- **US**: data includes **EPS** and **Revenue estimates**, with 盘前/盘后 (bmo/amc) timing
- **CN**: A股预约披露时间（实际披露日前一天晚间发布）
- **HK**: 已发布的业绩公布公告 + 董事会会议通告（预告未来业绩公布日，从公告 PDF 解析会议日期）
- Revenue numbers are formatted as `12.3 B`, `560 M`
- All-day events, timezone-aware (ET)
- Works in any calendar app that supports `.ics`
- Automatically pushed to this repo for public access

---

## 📋 Watchlists

| Market | File | Format |
|---|---|---|
| US | `watchlist.txt` | 代码，如 `AAPL` |
| A股 | `watchlist_cn.txt` | 6 位数字，如 `688256` |
| 港股 | `watchlist_hk.txt` | 5 位代码，如 `00700` / `0700` / `700.HK` |

> ⚠️ 港股业绩时间说明：港股没有 A股式的"预约披露"，业绩公布日期通常在公布前约一周通过**董事会会议通告**预告。本项目会解析通告 PDF 提取会议日期作为业绩公布日（标注"待公布"）；已发布的业绩公布公告会直接作为事件。少数扫描件 PDF 无法解析，可能遗漏。

---

## 🔧 Setup Instructions

### 1. Fork or Clone This Repository

```bash
git clone https://github.com/<your-username>/earning-calendar-ics.git
cd earning-calendar-ics
```

### 2. Get a Free Finnhub API Key

- Go to: https://finnhub.io/register
- Create an account
- Copy your free API key (e.g., sandbox_abc123...)

### 3. Set API Key in GitHub Actions

- Go to your GitHub repository
- Navigate to: Settings → Secrets and variables → Actions
- Click New repository secret
- Name: `FINNHUB_TOKEN`
- Value: your API key

### 4. How It Works

- GitHub Actions runs every day at 10:00 and 22:00 Beijing Time
- Pulls 30 days of upcoming earnings
- Converts them into a .ics calendar file
- Commits it back to the repository if updated

---

## 📅 Subscribe to the Calendar

After the first successful run, you’ll see a file like:

```
earnings_calendar.ics
```

You can subscribe to it via this URL:

```
https://raw.githubusercontent.com/<your-username>/earning-calendar-ics/main/earnings_calendar.ics
```

### Calendar Subscription Instructions

- **macOS / iOS**: Calendar → File → New Calendar Subscription…
- **Google Calendar**:
  - Open calendar.google.com
  - Left menu: “Other calendars” → “From URL” → paste the link above
- **Outlook / Others**: Add internet calendar via URL
