"""Building-balanced hedonic log-price regression; standard library only.

All eligible island flats contribute. Ridge strength is chosen by five-fold
building-held-out validation. Errors measure asking-price prediction, not bookings.
"""
from collections import Counter
import hashlib
import math

FEATURES = ['intercept', 'log_area', 'guests', 'bedrooms', 'bedrooms_missing',
            'Norddorf', 'Wittduen', 'Sueddorf', 'Steenodde', 'Westerheide']


def building(row):
    return row.get('building') or row['house']


def features(row):
    locality = row.get('locality', 'Nebel').lower()
    return [1., math.log(row['area']), float(row['guests']),
            float(row['bedrooms']) if row.get('bedrooms') is not None else 0.,
            float(row.get('bedrooms') is None),
            *[float(name in locality) for name in ('norddorf', 'wittdün', 'süddorf', 'steenodde', 'westerheide')]]


def price(row):
    # Unknown cleaning remains unknown; this is a partial-fee asking-price basis.
    return row['nightly'] + (row.get('cleaning') or 0) / 7


def training_rows(records, target_house):
    target_buildings = {building(r) for r in records if r['house'] == target_house}
    return sorted({r['id']: r for r in records
                   if not r.get('excluded') and r['house'] != target_house
                   and building(r) not in target_buildings
                   and r.get('nightly') is not None and r['nightly'] > 0
                   and r.get('area') is not None and 20 <= r['area'] <= 100
                   and r.get('guests') is not None and r['guests'] > 0}.values(), key=lambda r: r['id'])


def weights(rows):
    counts = Counter(building(r) for r in rows)
    return [1 / counts[building(r)] for r in rows]


def solve(a, b):
    """Pivoted Gaussian elimination for the small penalized normal equations."""
    a = [row[:] + [v] for row, v in zip(a, b)]
    n = len(b)
    for j in range(n):
        pivot = max(range(j, n), key=lambda i: abs(a[i][j]))
        a[j], a[pivot] = a[pivot], a[j]
        divisor = a[j][j]
        if abs(divisor) < 1e-12:
            raise ValueError('Singular model design')
        a[j] = [v / divisor for v in a[j]]
        for i in range(n):
            if i != j:
                factor = a[i][j]
                a[i] = [v - factor * w for v, w in zip(a[i], a[j])]
    return [row[-1] for row in a]


def fit(rows, penalty):
    xs, ws = [features(r) for r in rows], weights(rows)
    total, n = sum(ws), len(FEATURES)
    means = [0.] + [sum(w*x[j] for w, x in zip(ws, xs))/total for j in range(1,n)]
    scales = [1.] + [max(math.sqrt(sum(w*(x[j]-means[j])**2 for w,x in zip(ws,xs))/total), 1e-8) for j in range(1,n)]
    # A constant training feature must stay neutral for unseen categories.
    scales = [s if s > 1e-7 else 1. for s in scales]
    xs = [[(v-m)/s for v,m,s in zip(x,means,scales)] for x in xs]
    a = [[0.]*n for _ in range(n)]
    b = [0.]*n
    for row, x, w in zip(rows,xs,ws):
        y = math.log(price(row))
        for j in range(n):
            b[j] += w*x[j]*y/total
            for k in range(n):
                a[j][k] += w*x[j]*x[k]/total
    for j in range(1,n):
        a[j][j] += penalty
    return dict(coefficients=solve(a,b), means=means, scales=scales, penalty=penalty)


def predict_log(model, row):
    return sum(c*(x-m)/s for c,x,m,s in zip(model['coefficients'],features(row),model['means'],model['scales']))


def quantile(pairs, fraction):
    threshold = sum(w for _,w in pairs)*fraction
    total = 0
    for value,w in sorted(pairs):
        total += w
        if total >= threshold:
            return value
    return max(v for v,_ in pairs)


def train(records, target_house):
    rows = training_rows(records, target_house)
    groups = sorted({building(r) for r in rows}, key=lambda g: hashlib.sha256(g.encode()).hexdigest())
    if len(rows) < 30 or len(groups) < 10:
        return None
    folds = {g:i % 5 for i,g in enumerate(groups)}
    ws = weights(rows)
    trials = []
    for penalty in (.001, .01, .1, 1.):
        residuals, errors, baseline = [], [], []
        for fold in range(5):
            train_rows = [r for r in rows if folds[building(r)] != fold]
            fitted = fit(train_rows, penalty)
            base = quantile([(price(r),w) for r,w in zip(train_rows,weights(train_rows))], .5)
            for row,w in zip(rows,ws):
                if folds[building(row)] == fold:
                    predicted = predict_log(fitted,row)
                    residuals.append((math.log(price(row))-predicted,w))
                    errors.append((abs(price(row)-math.exp(predicted)),w))
                    baseline.append((abs(price(row)-base),w))
        mse = sum(v*v*w for v,w in residuals)/sum(ws)
        trials.append((mse,penalty,residuals,errors,baseline))
    mse,penalty,residuals,errors,baseline = min(trials, key=lambda t:t[0])
    model = fit(rows,penalty)
    model.update(name='Building-balanced ridge regression of log asking price', features=FEATURES,
                 observation_count=len(rows), property_count=len(groups),
                 observation_ids=[r['id'] for r in rows],
                 residual_log_quantiles=[quantile(residuals,.1),quantile(residuals,.9)],
                 validation=dict(method='Five folds holding out entire buildings; used to select ridge penalty, not an independent final test',
                                 folds=5, log_rmse=round(math.sqrt(mse),4),
                                 mae_eur=round(sum(v*w for v,w in errors)/sum(ws),2),
                                 baseline_mae_eur=round(sum(v*w for v,w in baseline)/sum(ws),2),
                                 candidates=[dict(penalty=p,log_rmse=round(math.sqrt(e),4)) for e,p,*_ in trials]),
                 fee_basis='Nightly asking price plus explicitly listed cleaning / 7; unknown fees remain unfilled')
    return model
