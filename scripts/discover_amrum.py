"""Walk the public Amrum host directory, cache detail pages, and deduplicate TOMAS IDs.

python scripts/discover_amrum.py --cache-dir /tmp/amrum-cache
Then run collect_amrum.py --offline-cache. No search-engine snippets become prices.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import time
import threading
from urllib.parse import urljoin
from collect_amrum import Document, ROOT, fetch


def discover(cache, max_pages=90):
    config_path = ROOT / 'config/amrum_sources.json'
    config = json.loads(config_path.read_text())
    directory = 'https://www.amrum.de/gastgeberverzeichnis'
    page_url = directory
    listings, pages, errors, houses = {}, [], [], set(config['official_urls'])
    directory_entries = set()
    rate_lock = threading.Lock()
    next_request = [0.0]

    def read(url):
        path = cache / (hashlib.sha256(url.encode()).hexdigest() + '.html')
        if not path.exists():
            # At most two request starts per second, with four in flight.
            with rate_lock:
                time.sleep(max(0, next_request[0] - time.monotonic()))
                next_request[0] = time.monotonic() + .5
        return fetch(url, cache, offline=path.exists())

    for _ in range(max_pages):
        doc = Document(read(page_url)).root
        pages.append(page_url)
        directory_entries.update(n.attrs['data-ident'] for n in doc.all('h2') if n.attrs.get('data-ident', '').startswith('address_'))
        for a in doc.all('a'):
            href = a.attrs.get('href', '')
            if '/hotel/' in href and 'form=tomasDetail' in href:
                listings[href] = a.text()
        next_links = [a.attrs.get('href') for a in doc.all('a') if a.text().lower() == 'nächste seite']
        print(f'Directory page {len(pages)}: {len(listings)} distinct entries', flush=True)
        if not next_links:
            break
        next_url = urljoin(directory, next_links[0])
        if next_url in pages:
            raise ValueError('Directory pagination loop')
        page_url = next_url
    else:
        raise ValueError('Directory exceeded safety page limit; incomplete discovery')
    def detail(url):
        try:
            raw = read(url)
            doc = Document(raw).root
            ids = [n.attrs.get('value') for n in doc.all('input') if n.attrs.get('name') == 'serviceProviderID']
            if not ids or not re.fullmatch(r'[A-Z]+\d+', ids[0]):
                raise ValueError('No public booking-provider ID')
            canonical = 'https://www.amrum.de/amrum-touristik/ukv/house/' + ids[0]
            (cache / (hashlib.sha256(canonical.encode()).hexdigest() + '.html')).write_text(raw)
            return canonical, None
        except (OSError, ValueError) as error:
            return None, {'url':url, 'error':type(error).__name__}
    with ThreadPoolExecutor(max_workers=4) as pool:
        for i, future in enumerate(as_completed([pool.submit(detail, url) for url in listings])):
            canonical, error = future.result()
            if canonical:
                houses.add(canonical)
            if error:
                errors.append(error)
            if (i+1) % 20 == 0:
                print(f'Details {i+1}/{len(listings)}: {len(houses)} provider records, {len(errors)} unavailable', flush=True)
    config['official_urls'] = sorted(houses)
    config['directory'] = dict(url=directory, retrieved_at=datetime.now(timezone.utc).isoformat(timespec='seconds'),
                               pages=len(pages), entries=len(listings), directory_entries=len(directory_entries), provider_records=len(houses), errors=errors)
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(config['directory'], ensure_ascii=False), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache-dir', type=Path, default=Path('/tmp/amrum-cache'))
    args = parser.parse_args()
    args.cache_dir.mkdir(parents=True, exist_ok=True)
    discover(args.cache_dir)
