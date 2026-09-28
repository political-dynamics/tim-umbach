"""Extract read-only Wayback captures; preserve capture dates separately from tariff years.

Download CDX indexes into the cache first as archive-2025.json/archive-2026.json.
Then run with --fetch or reuse cached archive HTML without network.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import time
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from collect_amrum import ROOT, parse_official


def collect(cache, refresh=False, max_downloads=200):
    records, sources, errors = [], [], []
    downloads, failures, paused = 0, 0, False
    index_counts = {}
    now = datetime.now(timezone.utc).isoformat(timespec='seconds')
    for year in (2026,2025):
        index = json.loads((cache / f'archive-{year}.json').read_text())
        index_counts[str(year)] = sum(bool(re.fullmatch(r'https?://(?:www\.)?amrum.de/amrum-touristik/ukv/house/GER\d+', r[2])) for r in index[1:])
        for capture in index[1:]:
            _, stamp, original, *_ = capture
            if not re.fullmatch(r'https?://(?:www\.)?amrum.de/amrum-touristik/ukv/house/GER\d+',original):
                continue
            url = f'https://web.archive.org/web/{stamp}id_/{original}'
            file = cache / (hashlib.sha256(url.encode()).hexdigest()+'.html')
            try:
                if refresh and not file.exists():
                    if paused or downloads >= max_downloads:
                        raise ValueError('Download deferred after connection failures or request limit')
                    downloads += 1
                    time.sleep(2.)
                    request = Request(url,headers={'User-Agent':'AmrumMarketResearch/2.0 (historical public asking-price research)'})
                    with urlopen(request,timeout=25) as response:
                        raw = response.read().decode('utf-8')
                        # Wayback can redirect a missing capture; do not misdate it.
                        final_url = response.geturl()
                        match = re.search(r'/web/(\d{14})',final_url)
                        if not match or match[1] != stamp:
                            raise ValueError('Archive redirected to a different capture')
                    file.write_text(raw)
                    failures = 0
                raw = file.read_text()
                observed = datetime.strptime(stamp,'%Y%m%d%H%M%S').replace(tzinfo=timezone.utc).isoformat()
                parsed = parse_official(raw,original,now)
                for row in parsed:
                    row.update(captured_at=observed, capture_year=year, archive_url=url,
                               tariff_year=None, stay_season=None,
                               price_type='Archived undated portal from-price; capture date is not a stay date')
                records.extend(parsed)
                sources.append(dict(url=original,archive_url=url,captured_at=observed,retrieved_at=now,
                                    sha256=hashlib.sha256(raw.encode()).hexdigest()))
            except (OSError,ValueError) as error:
                errors.append(dict(url=original,archive_url=url,error=str(error)))
                if isinstance(error, OSError):
                    failures += 1
                    if failures >= 3 or isinstance(error, HTTPError) and error.code == 429:
                        paused = True
            print(f'{year}: {len(sources)} pages, {len(records)} unit records, {len(errors)} errors',flush=True)
    return dict(meta=dict(retrieved_at=now,source='Internet Archive captures of amrum.de',
                          note='Capture year/month identify observation time only. Tariff year and stay season are unknown.',
                          index_property_counts=index_counts, errors=errors),observations=records,sources=sources)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache-dir',type=Path,default=Path('/tmp/amrum-verified'))
    parser.add_argument('--fetch',action='store_true')
    parser.add_argument('--max-downloads',type=int,default=200)
    args=parser.parse_args()
    data=collect(args.cache_dir,args.fetch,args.max_downloads)
    if not data['sources']:
        raise ValueError('No archived pages extracted; preserve existing snapshot')
    output=ROOT/'data/amrum_archive.json'
    tmp=output.with_suffix('.tmp')
    tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
    tmp.replace(output)


if __name__=='__main__':
    main()
