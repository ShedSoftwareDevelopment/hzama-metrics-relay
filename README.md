# hzama-metrics-relay

Daily GitHub Actions job that fetches yesterday's HzAma App Store Connect sales
data (downloads, subscription counts, revenue) and writes it to
`appstore-metrics.json` in this repo.

This exists because the Claude Code cloud sandbox that sends HzAma's daily
report email can only reach a small allowlist of domains (`googleapis.com`,
`api.github.com`) and cannot call `api.appstoreconnect.apple.com` directly.
GitHub Actions runners have normal internet access, so this repo does the
Apple API call on a schedule, and the reporting routine reads the result back
via the GitHub Contents API.

`appstore-metrics.json` always contains only the most recent day's aggregate
numbers — no customer data.
