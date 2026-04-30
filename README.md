# 📅 Earnings Calendar (.ics Generator)

This project automatically generates a `.ics` calendar file containing upcoming earnings release events for U.S.-listed companies, using data from the [Finnhub API](https://finnhub.io/).

The file is updated **twice daily** via GitHub Actions and is compatible with iOS/macOS/Google calendars. Useful for investors who want earnings events directly in their calendar apps.新增了A股科技股————葛小鹏

---

## ✅ Features

- Updates **twice per day** (10:00 and 22:00 Beijing Time)
- Data includes **EPS** and **Revenue estimates**
- Revenue numbers are formatted as `12.3 B`, `560 M`
- All-day events, timezone-aware (ET)
- Works in any calendar app that supports `.ics`
- Automatically pushed to this repo for public access

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
