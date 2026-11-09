import json
import os
import re
import time
from pathlib import Path

import anthropic
import pdfplumber
from fpdf import FPDF
from playwright.sync_api import sync_playwright

from secretTLV import scrape_jobs_page, scrape_job_details


# ── helpers ────────────────────────────────────────────────────────────────────

def load_config(path=None):
    config_path = Path(path) if path is not None else Path(__file__).with_name("config.json")
    if not config_path.is_file():
        raise FileNotFoundError(
            f"Missing {config_path}. Copy config.example.json to config.json and fill it locally."
        )
    with config_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def read_pdf_text(path):
    text = ""
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            text += page.extract_text() or ""
    return text.strip()


def save_pdf(text, path):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.set_font("Helvetica", size=11)
    for line in text.split("\n"):
        pdf.multi_cell(0, 7, line)
    pdf.output(path)


def slugify(text):
    return re.sub(r'[^a-z0-9]+', '-', text.lower()).strip('-')[:60]


# ── scoring ────────────────────────────────────────────────────────────────────

def score_job(job, search_cfg):
    score = 0
    text = f"{job.get('title','')} {job.get('category','')} {job.get('description','')}".lower()

    for kw in search_cfg.get("keywords", []):
        if kw.lower() in text:
            score += 1

    if job.get("location"):
        for loc in search_cfg.get("locations", []):
            if loc.lower() in job["location"].lower():
                score += 1

    if job.get("job_type"):
        for jt in search_cfg.get("job_types", []):
            if jt.lower() in job["job_type"].lower():
                score += 1

    if job.get("hebrew_level"):
        if job["hebrew_level"] in search_cfg.get("hebrew_levels", []):
            score += 1

    exp = job.get("experience", "") or ""
    max_exp = search_cfg.get("max_experience_years", 99)
    nums = re.findall(r'\d+', exp)
    if nums and int(nums[-1]) <= max_exp:
        score += 1

    return score


# ── claude calls ───────────────────────────────────────────────────────────────

def generate_cv(client, cv_template, job):
    prompt = f"""You are an expert CV writer. 
Given the candidate's template CV and a job description, rewrite the CV to be tailored for this specific role.
Keep it professional, truthful, and ATS-friendly. Output plain text only, no markdown.

=== TEMPLATE CV ===
{cv_template}

=== JOB TITLE ===
{job['title']}

=== JOB DESCRIPTION ===
{job.get('description', 'N/A')}

=== JOB DETAILS ===
Category: {job.get('category')}
Location: {job.get('location')}
Experience required: {job.get('experience')}
"""
    response = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}]
    )
    return response.content[0].text


def generate_cover_letter(client, cv_template, job, applicant):
    prompt = f"""You are an expert cover letter writer.
Write a compelling, concise cover letter (3-4 paragraphs) for the following job.
Be specific to the role. Output plain text only, no markdown.

=== CANDIDATE INFO ===
{cv_template}

=== JOB TITLE ===
{job['title']}

=== JOB DESCRIPTION ===
{job.get('description', 'N/A')}

=== APPLICANT ===
Name: {applicant['first_name']} {applicant['last_name']}
Email: {applicant['email']}
LinkedIn: {applicant.get('linkedin', '')}
"""
    response = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=1000,
        messages=[{"role": "user", "content": prompt}]
    )
    return response.content[0].text


# ── apply ──────────────────────────────────────────────────────────────────────

def apply_to_job(page, job, applicant, cv_pdf_path):
    print(f"  → Applying to: {job['url']}")
    page.goto(job['url'], timeout=60000, wait_until='domcontentloaded')
    page.wait_for_timeout(2000)

    try:
        # click Apply button to open the form slider
        apply_btn = page.locator('a.wpjb-button, a:has-text("Apply")')
        apply_btn.first.click()
        page.wait_for_timeout(1500)

        page.fill('input[name="first_name"]',  applicant['first_name'])
        page.fill('input[name="last_name"]',   applicant['last_name'])
        page.fill('input[name="email"]',        applicant['email'])
        page.fill('input[name="phone"]',        applicant.get('phone', ''))

        # upload CV
        with page.expect_file_chooser() as fc_info:
            page.click('input[type="file"]')
        fc_info.value.set_files(cv_pdf_path)
        page.wait_for_timeout(1000)

        # submit
        page.click('input[type="submit"], button[type="submit"]')
        page.wait_for_timeout(2000)
        print(f"  ✓ Applied successfully")
        return True

    except Exception as e:
        print(f"  ✗ Apply failed: {e}")
        return False


# ── main pipeline ──────────────────────────────────────────────────────────────

def main():
    cfg = load_config()
    if not cfg.get("anthropic_api_key") or cfg["anthropic_api_key"] == "replace-with-your-key":
        raise ValueError("Set anthropic_api_key in your local config.json before running.")
    search_cfg = cfg["search"]
    applicant = cfg["applicant"]
    output_dir = Path(cfg["output_dir"])
    output_dir.mkdir(exist_ok=True)

    client = anthropic.Anthropic(api_key=cfg["anthropic_api_key"])
    cv_template = read_pdf_text(cfg["cv_template_path"])

    all_jobs = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
            viewport={'width': 1280, 'height': 800},
        )
        page = context.new_page()

        # ── step 1: scrape all job listings ──
        print("=== Scraping job listings ===")
        page_num = 1
        while True:
            url = 'https://jobs.secrettelaviv.com/' if page_num == 1 else f'https://jobs.secrettelaviv.com/page/{page_num}/'
            print(f"  Listing page {page_num}...")
            jobs = scrape_jobs_page(page, url)
            if not jobs:
                break
            all_jobs.extend(jobs)
            page_num += 1

        # deduplicate
        seen = set()
        unique_jobs = []
        for job in all_jobs:
            if job['url'] not in seen:
                seen.add(job['url'])
                unique_jobs.append(job)
        print(f"  Found {len(unique_jobs)} unique jobs")

        # ── step 2: scrape details ──
        print("\n=== Scraping job details ===")
        for i, job in enumerate(unique_jobs):
            print(f"  [{i+1}/{len(unique_jobs)}] {job['title']}")
            details = scrape_job_details(page, job['url'])
            job.update(details)

        # ── step 3: score and filter ──
        print("\n=== Scoring and filtering jobs ===")
        min_score = search_cfg.get("min_match_score", 2)
        matched_jobs = []
        for job in unique_jobs:
            score = score_job(job, search_cfg)
            job['match_score'] = score
            if score >= min_score:
                matched_jobs.append(job)

        matched_jobs.sort(key=lambda j: j['match_score'], reverse=True)
        print(f"  {len(matched_jobs)} jobs matched (min score: {min_score})")

        # ── step 4: generate docs + apply ──
        # ── step 4: generate docs + apply ──
        print("\n=== Generating CVs, cover letters, and applying ===")
        results = []

        for job in matched_jobs:
            slug = slugify(job['title'])
            job_dir = output_dir / slug
            job_dir.mkdir(exist_ok=True)

            print(f"\n[Score {job['match_score']}] {job['title']}")
            print(f"  URL:        {job['url']}")
            print(f"  Category:   {job.get('category')}")
            print(f"  Location:   {job.get('location')}")
            print(f"  Job Type:   {job.get('job_type')}")
            print(f"  Experience: {job.get('experience')}")
            print(f"  Hebrew:     {job.get('hebrew_level')}")

            # generate tailored CV
            print("  Generating CV...")
            cv_text = generate_cv(client, cv_template, job)
            cv_path = str(job_dir / "cv.pdf")
            save_pdf(cv_text, cv_path)

            # generate cover letter
            print("  Generating cover letter...")
            cl_text = generate_cover_letter(client, cv_template, job, applicant)
            cl_path = str(job_dir / "cover_letter.pdf")
            save_pdf(cl_text, cl_path)

            # ── human confirmation ──
            print(f"\n  CV saved to:           {cv_path}")
            print(f"  Cover letter saved to: {cl_path}")
            print(f"\n  Apply to this job? [y]es / [n]o / [q]uit: ", end="")
            choice = input().strip().lower()

            if choice == 'q':
                print("  Stopping.")
                break
            elif choice != 'y':
                print("  Skipped.")
                results.append({
                    "title": job['title'],
                    "url": job['url'],
                    "match_score": job['match_score'],
                    "cv_path": cv_path,
                    "cover_letter_path": cl_path,
                    "applied": False,
                    "skipped": True,
                })
                continue

            # apply
            applied = apply_to_job(page, job, applicant, cv_path)

            results.append({
                "title": job['title'],
                "url": job['url'],
                "match_score": job['match_score'],
                "cv_path": cv_path,
                "cover_letter_path": cl_path,
                "applied": applied,
                "skipped": False,
            })

            time.sleep(2)





        browser.close()

    # ── save results log ──
    log_path = output_dir / "results.json"
    with open(log_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print(f"\n=== Done ===")
    print(f"Applied to {sum(r['applied'] for r in results)}/{len(results)} jobs")
    print(f"Results saved to {log_path}")


if __name__ == "__main__":
    main()
