"""Dated owner tariffs and matched-apartment tests of annual price effects.

These are currently published historical schedules, not archived observations
of bookings. Native season codes are retained: they differ between owners.
"""
from collections import defaultdict
import math
import re
import statistics

SOURCES = {
    'sanskiin': ('Hüs Sanskiin', 'https://www.sanskiin-amrum.de/wohnungen/saison-zeiten-preise-2025-2026/'),
    'uesdrum': ('Üs Drum', 'https://www.amrum-uesdrum.de/preise'),
    'suedspitze': ('Ferien am Meer / Südspitze', 'https://www.web-amrum.de/preise-u-freie-zeiten/'),
    'jensen': ('Käpt’n Jensen', 'https://www.kaeptn-jensen.de/preise/'),
}


def parse(raw, source, document):
    doc = document(raw).root
    text = doc.text().replace('\xad', '')
    rows = []

    def add(unit, year, rates, fee, basis):
        for season, nightly in rates.items():
            rows.append(dict(provider=source, apartment=unit, year=year, source_season=season,
                             nightly=float(nightly), mandatory_stay_fee=fee, fee_note=basis,
                             url=SOURCES[source][1]))

    if source == 'sanskiin':
        if 'Preise bis 31.12.2025' not in text or 'Preise ab 01.01.2026' not in text:
            raise ValueError('Sanskiin year headings changed')
        for year, section in ((2025,text.split('Preise bis 31.12.2025')[1].split('Preise ab 01.01.2026')[0]),
                              (2026,text.split('Preise ab 01.01.2026')[1].split('Nebenkosten-Pauschale')[0])):
            matches = re.findall(r'(Aawerkant|Brombelbei|Driiwholt|Eerdglüper|Fiiwfut|Goodshenk|Hopelfask)\s+€\s*(\d+),-\*\s+€\s*(\d+),-\*\s+€\s*(\d+),-\*\s+€\s*(\d+),-\*',section)
            if len(matches) != 7:
                raise ValueError('Sanskiin rates changed')
            for unit,*rates in matches:
                add(unit,year,dict(zip('DCBA',rates)),None,
                    'Additional stay and booking fees shown without a historical fee year; excluded from annual room-rate comparisons. Native D is off-season, C shoulder, B intermediate, A peak.')
    elif source == 'uesdrum':
        for year in (2026,2027):
            if f'Preise {year}' not in text:
                raise ValueError('Üs Drum year heading missing')
            section = text.split(f'Preise {year}')[1].split('Saisonzeiten')[0]
            matches = re.findall(r'(Kniepsand|Satteldüne|Strandgut).*?(\d+),-\s+(\d+),-\s+(\d+),-', section)
            if len(matches) != 3:
                raise ValueError('Üs Drum rates changed')
            for unit,*rates in matches:
                add(unit,year,dict(zip('ABC',rates)),None,'Seven-night rate; self cleaning and own linen, optional paid service has no stated amount.')
    elif source == 'suedspitze':
        for year in (2026,2027):
            tables = [t for t in doc.all('table') if f'Saisonzeiten {year}' in t.text()]
            if len(tables) != 1:
                raise ValueError('Südspitze year table missing')
            rates = {}
            for tr in tables[0].all('tr'):
                cells = [c.text() for c in tr.all('td')]
                season = next((re.match(r'([ABC])\s*-',c) for c in cells if re.match(r'([ABC])\s*-',c)),None)
                if season:
                    amount = re.search(r'(\d+),-\s*€',cells[-1])
                    if not amount:
                        raise ValueError('Südspitze amount missing')
                    code,value = season[1],int(amount[1])
                    if code in rates and rates[code] != value:
                        raise ValueError('Südspitze rates vary within season')
                    rates[code] = value
            if set(rates) != set('ABC'):
                raise ValueError('Südspitze seasons changed')
            for unit in ('Dünenrose','Weiße Düne'):
                add(unit,year,rates,0,'At least seven nights; cleaning, linen, towels and energy included.')
    elif source == 'jensen':
        if 'Preise und Saisonzeiten 2026 / 2027' not in text:
            raise ValueError('Jensen tariff year scope changed')
        matches = re.findall(r'Ferienwohnung ([23]) Saison A:\s*([\d,]+) € Saison B:\s*([\d,]+) € Saison C:\s*([\d,]+) €',text)
        if len(matches) != 2:
            raise ValueError('Jensen tariff structure changed')
        fees = dict(re.findall(r'Ferienwohnung ([23]):\s*(\d+),00 €',text))
        for unit,*rates in matches:
            for year in (2026,2027):
                add('Wohnung '+unit,year,dict(zip('ABC',[float(p.replace(',','.')) for p in rates])),float(fees[unit]),
                    'Shared 2026/2027 schedule; mandatory service includes cleaning and linen.')
    else:
        raise ValueError('Unknown historical source')
    return rows


def seasonal_sources(rows):
    """Only unambiguous A/B/C schedules feed the existing seasonal comparison."""
    result = []
    for provider in ('uesdrum','suedspitze','jensen'):
        units = defaultdict(dict)
        for r in rows:
            if r['provider'] == provider and r['year'] == 2026:
                units[r['apartment']][r['source_season']] = r['nightly'] + (r['mandatory_stay_fee'] or 0)/7
        if not units:
            continue
        ratios = {s: statistics.median(v[s]/v['B'] for v in units.values()) for s in 'ABC'}
        ratios['christmas'] = 1.
        result.append(dict(name=SOURCES[provider][0],url=SOURCES[provider][1],tariff_year=2026,
                           apartments=len(units),rates=list(units.values()),ratios=ratios,
                           note='2026 A/B/C schedule; Christmas uses B. Seven-night basis with documented mandatory fees; unspecified service charges remain unknown.'))
    return result


def year_effects(rows):
    """Paired differences eliminate apartment and native-season fixed effects.

Equal owner weights prevent a large portfolio dominating the year dummy.
Use leave-owner-out prediction of matched log changes to decide whether to apply.
"""
    lookup = {(r['provider'],r['apartment'],r['year'],r['source_season']):r for r in rows}
    result = {}
    for year in (2025,2027):
        changes = defaultdict(list)
        for r in rows:
            base = lookup.get((r['provider'],r['apartment'],2026,r['source_season']))
            if r['year'] == year and base:
                changes[r['provider']].append(math.log(r['nightly']/base['nightly']))
        effects = {p:statistics.mean(v) for p,v in changes.items()}
        candidate = statistics.mean(effects.values()) if effects else None
        with_year = without_year = None
        if len(effects) >= 3:
            with_year = math.sqrt(statistics.mean(statistics.mean((v-statistics.mean([e for p,e in effects.items() if p != provider]))**2 for v in values) for provider,values in changes.items()))
            without_year = math.sqrt(statistics.mean(statistics.mean(v*v for v in values) for values in changes.values()))
        accepted = with_year is not None and with_year < without_year
        result[str(year)] = dict(reference_year=2026, provider_count=len(effects), matched_season_pairs=sum(map(len,changes.values())),
                                 candidate_factor=round(math.exp(candidate),4) if candidate is not None else None,
                                 supported_factor=round(math.exp(candidate),4) if accepted else 1., applied_factor=1.,
                                 provider_factors={p:round(math.exp(v),4) for p,v in effects.items()},
                                 accepted=accepted,
                                 validation=dict(year_dummy_log_rmse=round(with_year,4) if with_year is not None else None,
                                                 no_year_dummy_log_rmse=round(without_year,4) if without_year is not None else None),
                                 note='Matched apartment and native season, room rates only. Leave-one-owner-out validation; fees cannot be historically harmonized. '+
                                      ('Year adjustment improves held-owner prediction.' if accepted else 'No year adjustment: insufficient independent owners or no validation improvement.'))
    return result


def monthly_series(current, archived, current_stamp, target_house):
    """Descriptive archive series; never a model input.

One record per unit/month (latest available capture), balanced by building.
Matched indices divide each historical room rate by the same current unit's
room rate, conditional on unchanged advertised area and guest capacity.
"""
    import amrum_model
    eligible = amrum_model.training_rows(current, target_house)
    lookup = {r['id']:r for r in eligible}
    months = defaultdict(dict)
    for row in archived:
        if row.get('excluded') or row['id'] not in lookup or not row.get('nightly') or row['nightly'] <= 0:
            continue
        stamp = row['captured_at']
        if stamp[:10] > current_stamp[:10]:
            continue
        month = stamp[:7]
        previous = months[month].get(row['id'])
        if previous is None or stamp > previous[0]:
            months[month][row['id']] = (stamp,row)
    for row in eligible:
        months[current_stamp[:7]][row['id']] = (current_stamp,row)
    result = []
    for month, entries in sorted(months.items()):
        rows = [r for _,r in entries.values()]
        matched = [r for r in rows if r['area']==lookup[r['id']]['area'] and r['guests']==lookup[r['id']]['guests']]
        result.append(dict(month=month,apartments=len(rows),buildings=len({amrum_model.building(r) for r in rows}),
                           median_nightly=round(amrum_model.quantile([(r['nightly'],w) for r,w in zip(rows,amrum_model.weights(rows))],.5),2),
                           matched_apartments=len(matched),
                           matched_index=round(amrum_model.quantile([(100*r['nightly']/lookup[r['id']]['nightly'],w) for r,w in zip(matched,amrum_model.weights(matched))],.5),1) if matched else None,
                           observation_ids=sorted(entries)))
    return dict(reference_month=current_stamp[:7], periods=result,
                note='Current eligible apartment IDs only. Building-weighted monthly medians; same-unit index additionally requires unchanged area and capacity. Room rates exclude separate fees. Sparse capture dates are not stay dates; seasonal and sample changes remain possible. History is not used by the pricing model.')
