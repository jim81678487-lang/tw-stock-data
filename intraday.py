name: Intraday TW stock snapshot

on:
  schedule:
    - cron: "55 2 * * 1-5"
  workflow_dispatch:

permissions:
  contents: write

jobs:
  snapshot:
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Fetch intraday quotes
        run: python intraday.py

      - name: Commit data
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
          git add data/
          git diff --cached --quiet || git commit -m "intraday: $(TZ=Asia/Taipei date '+%F %H:%M')"
          git pull --rebase
          git push
