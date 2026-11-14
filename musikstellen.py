name: Musikstellen

on:
  schedule:
    - cron: "0 5 * * *"   # täglich ca. 06:00/07:00 Schweizer Zeit
  workflow_dispatch:        # erlaubt manuellen Start per Knopfdruck

permissions:
  contents: write

concurrency:
  group: musikstellen
  cancel-in-progress: false

jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: Stellen prüfen
        run: python musikstellen.py
        env:
          SMTP_HOST: ${{ secrets.SMTP_HOST }}
          SMTP_PORT: ${{ secrets.SMTP_PORT }}
          SMTP_USER: ${{ secrets.SMTP_USER }}
          SMTP_PASS: ${{ secrets.SMTP_PASS }}
          MAIL_TO: ${{ secrets.MAIL_TO }}
      - name: Stand speichern
        run: |
          git config user.name "stellen-bot"
          git config user.email "bot@users.noreply.github.com"
          git add seen.json stellen.html
          git diff --cached --quiet || (git commit -m "Update" && git pull --rebase && git push)
