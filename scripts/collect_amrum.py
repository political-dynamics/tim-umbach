"""Public Amrum asking-price benchmarks. Standard library only; no booking actions.

Refresh: python scripts/collect_amrum.py --refresh --cache-dir /tmp/amrum-cache
Recalculate checked-in observations without network: python scripts/collect_amrum.py
Raw HTML stays in a local cache, outside the published site.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone, date
import hashlib
from html.parser import HTMLParser
import json
import math
from pathlib import Path
import re
import statistics
import time
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'data/amrum_market.json'
TARGET_HOUSE = 'GER00020060915743264'
SEASON_URL = 'https://www.amrum.de/kurabgabe-saisonzeiten'
DU_URL = 'https://www.ferien-amrum.de/ferienhaus-due-min-tues/preise/'
KOLL_URL = 'https://www.amrumferien-unter-reet.de/buchen/'


class Node:
    def __init__(self, tag='', attrs=()):
        self.tag, self.attrs, self.children = tag, dict(attrs), []
        self.parent = None

    def all(self, tag=None, cls=None):
        found = []
        for child in self.children:
            if isinstance(child, Node):
                if (not tag or child.tag == tag) and (not cls or cls in child.attrs.get('class', '').split()):
                    found.append(child)
                found.extend(child.all(tag, cls))
        return found

    def text(self):
        if self.tag in {'script', 'style', 'noscript'}:
            return ''
        return re.sub(r'\s+', ' ', ' '.join(c.text() if isinstance(c, Node) else c for c in self.children)).strip()


class Document(HTMLParser):
    def __init__(self, raw):
        super().__init__(convert_charrefs=True)
        self.root = Node()
        self.stack = [self.root]
        self.feed(raw)

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs)
        node.parent = self.stack[-1]
        self.stack[-1].children.append(node)
        if tag not in {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                self.stack = self.stack[:i]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def number(value):
    return float(value.replace('.', '').replace(',', '.'))


def match_number(pattern, text):
    match = re.search(pattern, text, re.I)
    return number(match[1]) if match else None


def parse_official(raw, url, retrieved_at):
    doc = Document(raw).root
    title = doc.all('title')[0].text().split(' | ')[0]
    house = url.rsplit('/', 1)[-1]
    geo = {n.attrs.get('itemprop'): n.attrs.get('content') for n in doc.all('meta')}
    lat, lon = geo.get('latitude'), geo.get('longitude')
    coordinates = [float(lat), float(lon)] if lat and lon else None
    if coordinates and not (54.60 < coordinates[0] < 54.72 and 8.28 < coordinates[1] < 8.43):
        coordinates = None
    locality = next((n.text() for n in doc.all() if n.attrs.get('itemprop') == 'addressLocality'), '')
    street = next((n.text() for n in doc.all() if n.attrs.get('itemprop') == 'streetAddress'), '')
    building = re.sub(r'[^a-z0-9äöüß]', '', (locality.split('/')[0] + street).lower()) if street else house
    records = []
    for node in doc.all('div', 'tp-media'):
        if not node.attrs.get('id', '').startswith('hp-'):
            continue
        headings, prices = node.all('h3'), node.all(cls='tp-price-amount')
        if not headings:
            continue
        text = node.text()
        amount = match_number(r'([\d.,]+)\s*€', prices[0].text()) if prices else None
        postfix = node.all(cls='tp-price-postfix')
        price_unit = postfix[0].text() if postfix else ''
        area = match_number(r'Größe\s*\(Quadratmeter\):\s*([\d.,]+)', text)
        capacity = match_number(r'Belegung:\s*\d+\s*-\s*(\d+)\s*Personen', text)
        beds = match_number(r'(\d+)\s+Schlafr[aä]um', text)
        cleaning = match_number(r'Endreinigung\s*([\d.,]+)\s*(?:,-|€|Euro)', text)
        if cleaning is None:
            cleaning = match_number(r'([\d.,]+)\s*(?:,-)?\s*(?:€|Euro)\s*Endreinigung', text)
        name = headings[0].text().split(' DTV')[0]
        ancestor, category = node.parent, ''
        while ancestor and not category:
            for child in ancestor.children:
                if isinstance(child, Node) and 'tp-panel-headline' in child.attrs.get('class', '').split():
                    category = child.text()
                    break
            ancestor = ancestor.parent
        reason = None
        if house == TARGET_HOUSE or 'alteschule' in re.sub(r'\W', '', title.lower()):
            reason = 'Target property excluded from market model'
        elif amount is None:
            reason = 'No public price'
        elif not re.search(r'pro\s+Einheit\s*/\s*Nacht', price_unit, re.I):
            reason = 'Nightly whole-unit price basis unverified'
        elif area is None or capacity is None:
            reason = 'Missing area or capacity'
        elif not 20 <= area <= 100:
            reason = 'Outside 20–100 m² comparison scope'
        elif 'ferienhaus' in name.lower() or 'ferienhäuschen' in name.lower():
            reason = 'Whole house, not an apartment'
        elif category and not category.lower().startswith('ferienwohnungen'):
            reason = 'Not an apartment listing'
        records.append(dict(id=node.attrs['id'], house=house, building=building, name=name, property=title,
                            area=area, guests=capacity, bedrooms=beds, nightly=amount, coordinates=coordinates, locality=locality,
                            cleaning=cleaning, minimum_nights=match_number(r'Mindestaufenthalt:\s*(\d+)', text),
                            url=url + '#' + node.attrs['id'], retrieved_at=retrieved_at,
                            price_type='Undated portal “heute ab” asking price', price_unit=price_unit, category=category, excluded=reason))
    if not records:
        raise ValueError(f'No accommodation records parsed: {url}')
    return records


def parse_target(raw, index, official):
    doc = Document(raw).root
    tables = []
    for table in doc.all('table', 'preis-table'):
        bodies = table.all('tbody')
        cells = bodies[0].all('td') if bodies else []
        if len(cells) == 6:
            tables.append([match_number(r'(\d+)', cell.text()) for cell in cells])
    if len(tables) != 2 or any(value is None for row in tables for value in row):
        raise ValueError('Target tariff structure changed')
    year_labels = [n.text() for n in doc.all('h4') if re.fullmatch(r'20\d{2}', n.text())]
    # Do not guess a corrected year for the duplicated Wohnung I heading.
    verified_2027 = '2027' in year_labels
    return dict(id=f'wohnung-{index}', name='Wohnung ' + ['I', 'II', 'III', 'IV'][index-1],
                area=official['area'], guests=official['guests'], bedrooms=[2, 2, 1, 0][index-1], coordinates=official['coordinates'],
                url=f'https://amrum.sh/wohnung{index}.php',
                tariffs={'2026': tables[0], '2027': tables[1] if verified_2027 else None},
                ambiguous_tariff=tables[1] if not verified_2027 else None,
                warning=None if verified_2027 else 'The second tariff table repeats 2026 although its dates match 2027. The 2027 advertised price is unverified.')


def seasonal_evidence(du_raw, koll_raw):
    du = Document(du_raw).root.text()
    rows = re.findall(r'A\s*-\s*Hauptsaison\s*-\s*(\d+)\s*€\s*B\s*-\s*Zwischensaison\s*-\s*(\d+)\s*€\s*C\s*-\s*Nebensaison\s*-\s*(\d+)\s*€', du)
    if len(rows) != 4:
        raise ValueError('Dü Min Tüs tariff structure changed')
    koll = Document(koll_raw).root.text()
    rates = re.findall(r'Saison\s*([ABC])\s*:\s*([\d.,]+)\s*€', koll)
    first = re.findall(r'erste\s+Tag wird mit\s*([\d.,]+)\s*€', koll)
    if len(rates) != 3 or len(first) != 3 or '2026' not in koll:
        raise ValueError('Koll tariff structure changed')
    k = {season: number(price) for season, price in rates}
    extras = [number(f) - number(rate[1]) for f, rate in zip(first, rates)]
    if len(set(extras)) != 1:
        raise ValueError('Koll first-night charge varies across seasons')
    du_ratios = {'A': statistics.median(int(a)/int(b) for a,b,c in rows), 'B': 1,
                 'C': statistics.median(int(c)/int(b) for a,b,c in rows), 'christmas': 1}
    # Weekly equivalent includes the documented first-night supplement.
    week = {s: rate + extras[0]/7 for s, rate in k.items()}
    koll_ratios = {s: week[s]/week['B'] for s in ('A', 'B', 'C')}
    koll_ratios['christmas'] = koll_ratios['A']
    return [dict(name='Dü Min Tüs', url=DU_URL, tariff_year=None, apartments=4,
                 note='Currently published but undated tariffs; linen included; cleaning not specified.',
                 rates=[{'A':int(a),'B':int(b),'C':int(c)} for a,b,c in rows], ratios=du_ratios),
            dict(name='Ferienwohnung Koll', url=KOLL_URL, tariff_year=2026, apartments=1,
                 note='2026 tariff; Christmas uses A. First-night supplement spread over seven nights.',
                 rates=k, first_night_supplement=extras[0], ratios=koll_ratios)]


def weighted_quantile(pairs, fraction):
    pairs = sorted(pairs)
    threshold = sum(w for _, w in pairs) * fraction
    cumulative = 0
    for value, weight in pairs:
        cumulative += weight
        if cumulative >= threshold:
            return value
    return pairs[-1][0]


def comparable_rows(target, records):
    houses = {}
    for row in records:
        if row['excluded'] or row['house'] == TARGET_HOUSE:
            continue
        if not row.get('locality', '').lower().startswith('nebel'):
            continue
        # Capacity must fit the whole target household. Size extrapolation is bounded.
        ratio = target['area'] / row['area']
        if row['guests'] < target['guests'] or row['guests'] > target['guests'] + 2 or not .5 <= ratio <= 2:
            continue
        distance = abs(math.log(ratio)) + .18 * abs(row['guests'] - target['guests'])
        if target['bedrooms'] and row['bedrooms'] is not None:
            distance += .15 * abs(row['bedrooms'] - target['bedrooms'])
        # At most one flat per property prevents a multi-unit landlord dominating.
        group = row.get('building', row['house'])
        if group not in houses or distance < houses[group][0]:
            houses[group] = (distance, row)
    return sorted(houses.values(), key=lambda item: (item[0], item[1]['id']))[:7]


def estimate(target, records, seasonal):
    peers = comparable_rows(target, records)
    if len(peers) < 3:
        return None
    pairs, details = [], []
    for distance, row in peers:
        # Only explicitly listed cleaning charges are added; unknown fees remain unknown.
        base = row['nightly'] + (row['cleaning'] or 0) / 7
        adjusted = base * (target['area'] / row['area']) ** .5
        weight = math.exp(-2 * distance)
        pairs.append((adjusted, weight))
        details.append(dict(id=row['id'], adjusted=round(adjusted, 2), weight=round(weight, 4)))
    mid = weighted_quantile(pairs, .5)
    low, high = weighted_quantile(pairs, .2), weighted_quantile(pairs, .8)
    output = {}
    for season in ('A', 'B', 'C', 'christmas'):
        ratios = [source['ratios'][season] for source in seasonal]
        factor = statistics.median(ratios)
        point = mid * factor
        # Descriptive sensitivity envelope, NOT a calibrated confidence interval.
        output[season] = dict(estimate=5 * math.floor(point/5 + .5),
                              low=5 * math.floor(min(low * min(ratios), point*.8)/5),
                              high=5 * math.ceil(max(high * max(ratios), point*1.2)/5),
                              factor=round(factor, 4))
    return dict(seasons=output, peers=details, property_count=len(peers), confidence='Low')


def seasons(year):
    boundaries = {2026:['01-10','02-28','06-13','09-12','10-31','12-19'],
                  2027:['01-09','02-27','06-12','09-11','10-30','12-18']}[year]
    end = f'{year+1}-' + ('01-09' if year == 2026 else '01-08')
    dates = [f'{year}-{v}' for v in boundaries] + [end]
    labels = ['Early low season','Spring shoulder','Summer peak','Autumn shoulder','Late low season','Christmas / New Year']
    return [dict(label=label, season=code, start=dates[i], end=dates[i+1])
            for i,(label,code) in enumerate(zip(labels,['C','B','A','B','C','christmas']))]


def recalculate(payload):
    for target in payload['apartments']:
        target['market'] = estimate(target, payload['observations'], payload['seasonal_evidence'])
    payload['seasons'] = {str(year): seasons(year) for year in (2026,2027)}
    payload['meta']['method_version'] = '1.0'
    return payload


def fetch(url, cache, offline=False):
    path = cache / (hashlib.sha256(url.encode()).hexdigest() + '.html')
    if not offline:
        with urlopen(Request(url, headers={'User-Agent':'AmrumMarketResearch/1.0 (public asking-price comparison)'}), timeout=35) as response:
            raw = response.read().decode('utf-8')
        path.write_text(raw, encoding='utf-8')
        time.sleep(.4)
    return path.read_text(encoding='utf-8')


def collect(cache, offline=False):
    config = json.loads((ROOT / 'config/amrum_sources.json').read_text())
    now = datetime.now(timezone.utc).isoformat(timespec='seconds')
    records, sources, errors = [], [], []

    def read(url):
        raw = fetch(url, cache, offline)
        sources.append(dict(url=url, retrieved_at=now, sha256=hashlib.sha256(raw.encode()).hexdigest()))
        return raw

    for url in config['official_urls']:
        try:
            records.extend(parse_official(read(url), url, now))
        except (OSError, ValueError) as error:
            errors.append(dict(url=url, error=type(error).__name__))
    # A listing may appear under several directory paths; one observation per unit ID.
    records = list({r['id']: r for r in records}.values())
    targets = sorted([r for r in records if r['house'] == TARGET_HOUSE], key=lambda r: r['name'])
    if len(targets) != 4 or len([r for r in records if not r['excluded']]) < 10:
        raise ValueError('Insufficient fresh source coverage; existing snapshot preserved')
    apartments = [parse_target(read(f'https://amrum.sh/wohnung{i}.php'), i, targets[i-1]) for i in range(1,5)]
    season_text = Document(read(SEASON_URL)).root.text()
    if 'Saisonzeiten 2026' not in season_text or 'Saisonzeiten 2027' not in season_text:
        raise ValueError('Official season calendar changed; review required')
    evidence = seasonal_evidence(read(DU_URL), read(KOLL_URL))
    return recalculate(dict(meta=dict(retrieved_at=now, errors=errors, discovery=config['discovery'], directory=config.get('directory'),
                                     reference_season='B', reference_year=2026, stay_nights=7,
                                     note='Portal asking prices treated as shoulder-season proxies at the September 2026 collection date. No realised bookings or occupancy data. 2027 estimates hold the price level constant; no inflation forecast.'),
                            apartments=apartments, observations=records, seasonal_evidence=evidence, sources=sources))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--refresh', action='store_true')
    parser.add_argument('--offline-cache', action='store_true')
    parser.add_argument('--cache-dir', type=Path, default=Path('/tmp/amrum-cache'))
    args = parser.parse_args()
    if args.refresh or args.offline_cache:
        args.cache_dir.mkdir(parents=True, exist_ok=True)
        # This initial model is anchored to the reviewed September 2026 shoulder season.
        # Re-anchoring at another date requires reviewing portal price semantics.
        if not args.offline_cache and not date(2026,9,12) <= date.today() < date(2026,10,31):
            raise ValueError('Refresh outside the reviewed reference season requires model review')
        payload = collect(args.cache_dir, args.offline_cache)
    else:
        payload = recalculate(json.loads(OUTPUT.read_text()))
    tmp = OUTPUT.with_suffix('.tmp')
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    tmp.replace(OUTPUT)
    print(f"Wrote {len(payload['apartments'])} apartments and {len(payload['observations'])} official records")


if __name__ == '__main__':
    main()
