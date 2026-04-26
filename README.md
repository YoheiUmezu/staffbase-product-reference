# staffbase-product-reference

Staffbase Help Center (日本語) の記事タイトルと URL を取得し、`staffbase_urls.md` に出力するスクリプトです。

## Requirements

- Python 3
- `requests`

## Setup

```bash
python3 -m pip install requests
```

## Run

```bash
python3 fetch_staffbase.py
```

## Scheduled Sync (cron)

毎朝 9:00 に実行し、ログを `cron.log` に追記する例です。

```cron
0 9 * * * /usr/bin/python3 /Users/umedzuyouhei/Desktop/kb/fetch_staffbase.py >> /Users/umedzuyouhei/Desktop/kb/cron.log 2>&1
```
