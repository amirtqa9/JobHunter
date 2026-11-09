# JobHunter

JobHunter scrapes job listings, scores matches against local search settings, creates tailored PDFs, and asks before submitting each application.

## Local setup

Install Python dependencies with `python3 -m pip install -r requirements.txt` and install Playwright's Chromium browser with `python3 -m playwright install chromium`.

Copy `config.example.json` to `config.json`. Set `anthropic_api_key`, the path to your CV template, search preferences, and applicant details in the local file. `config.json`, generated PDFs, and the output directory are ignored by Git. Never commit a populated config.

Run `python3 agent.py` from this directory. The script presents each generated application for a yes/no decision before submitting it.
