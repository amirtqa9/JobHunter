from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup


def scrape_jobs_page(page, url):
    page.goto(url, timeout=60000, wait_until='domcontentloaded')
    page.wait_for_timeout(3000)
    soup = BeautifulSoup(page.content(), 'html.parser')

    jobs = []
    for a in soup.find_all('a', href=True):
        href = a['href']
        title = a.get_text(strip=True)
        if 'jobs.secrettelaviv.com/job/' in href and title:
            jobs.append({'title': title, 'url': href})
    return jobs


def scrape_job_details(page, url):
    page.goto(url, timeout=60000, wait_until='domcontentloaded')
    page.wait_for_timeout(3000)
    soup = BeautifulSoup(page.content(), 'html.parser')

    details = {
        'category': None,
        'job_type': None,
        'education': None,
        'language': None,
        'hebrew_level': None,
        'location': None,
        'experience': None,
        'description': None,
    }

    field_map = {
        'category':         'category',
        'job type':         'job_type',
        'education':        'education',
        'spoken language':  'language',
        'hebrew':           'hebrew_level',
        'location':         'location',
        'years experience': 'experience',
    }

    for row in soup.find_all('div', class_='wpjb-grid-row'):
        label_div = row.find('div', class_='wpjb-col-35')
        value_div = row.find('div', class_='wpjb-col-60')
        if not label_div or not value_div:
            continue

        label = label_div.get_text(strip=True).lower()
        raw_value = value_div.get_text(separator=', ', strip=True)
        value = ', '.join(p.strip().strip(',') for p in raw_value.split(',') if p.strip().strip(','))

        for keyword, field in field_map.items():
            if keyword in label:
                details[field] = value
                break

    desc_block = soup.find('div', class_='wpjb-text')
    if desc_block:
        details['description'] = desc_block.get_text(separator='\n', strip=True)

    return details


def main():
    all_jobs = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
            viewport={'width': 1280, 'height': 800},
        )
        page = context.new_page()

        # step 1: collect all job links across all pages
        page_num = 1
        while True:
            url = 'https://jobs.secrettelaviv.com/' if page_num == 1 else f'https://jobs.secrettelaviv.com/page/{page_num}/'
            print(f"Scraping listing page {page_num}...")
            jobs = scrape_jobs_page(page, url)
            if not jobs:
                print("No more jobs found.")
                break
            all_jobs.extend(jobs)
            page_num += 1

        # deduplicate by URL
        seen = set()
        unique_jobs = []
        for job in all_jobs:
            if job['url'] not in seen:
                seen.add(job['url'])
                unique_jobs.append(job)

        print(f"\nFound {len(unique_jobs)} unique jobs. Scraping details...\n")

        # step 2: scrape details for each job
        for i, job in enumerate(unique_jobs):
            print(f"[{i+1}/{len(unique_jobs)}] {job['title']}")
            details = scrape_job_details(page, job['url'])
            job.update(details)

        browser.close()

    # print results
    print("\n=== RESULTS ===\n")
    for job in unique_jobs:
        print(f"Title:       {job['title']}")
        print(f"URL:         {job['url']}")
        print(f"Category:    {job['category']}")
        print(f"Job Type:    {job['job_type']}")
        print(f"Education:   {job['education']}")
        print(f"Language:    {job['language']}")
        print(f"Hebrew:      {job['hebrew_level']}")
        print(f"Location:    {job['location']}")
        print(f"Experience:  {job['experience']}")
        print(f"Description: {job['description'][:100] if job['description'] else None}")
        print("-" * 60)
        
    import json

    with open('jobs.json', 'w', encoding='utf-8') as f:
        json.dump(unique_jobs, f, ensure_ascii=False, indent=2)

    print(f"\nSaved {len(unique_jobs)} jobs to jobs.json")

    return unique_jobs


if __name__ == '__main__':
    jobs = main()