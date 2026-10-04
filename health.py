# -*- coding: utf-8 -*-
"""
Health checks for the scrape step (log-only).

Scrapers die quietly: a site gets redesigned, the parser matches nothing, the
run reports "0 listings today" and nobody notices for days. These checks
detect that class of failure and print a loud [health] ISSUE line in the
Actions log; website.build() additionally shows a stale-digest banner on the
site when a day's digest is missing.

Detected conditions:
  - total_zero        : nothing scraped at all (both sources broken / blocked)
  - source_zero:<src> : one source returned 0 while another returned > 0
                        (that source's parser is very likely broken)
  - low_volume        : total below MIN_EXPECTED_LISTINGS (but not zero)
  - geocode_coverage  : too few listings got coordinates — the geocoder's
                        address normalisation is probably out of step with
                        the source's street text (2026-09-30: 13% of SS.com
                        flats silently had no position for weeks)
"""
import config
import utils


def update_streaks(statuses, today=None, path=None):
    """data/health_state.json — per-source consecutive failure days.

    statuses: {key: bool} — True = the source produced data this run
    (e.g. "flat:ss.com", "cars:pp.lv"). A failing day increments
    fail_days; a good day resets it and records last_ok. Returns
    {key: fail_days} for streaks >= 2 — a 1-day blip stays quiet, a
    persistent outage escalates onto the digest banner.
    """
    from datetime import date as _date
    today = today or _date.today().isoformat()
    path = path or config.HEALTH_STATE_JSON
    st = utils.read_json(path, {})
    out = {}
    for key, ok in statuses.items():
        ent = st.setdefault(key, {})
        if ok:
            ent["fail_days"] = 0
            ent["last_ok"] = today
        else:
            ent["fail_days"] = int(ent.get("fail_days") or 0) + 1
            ent["last_fail"] = today
        if ent["fail_days"] >= 2:
            out[key] = ent["fail_days"]
    try:
        utils.write_json(path, st, indent=None)
    except OSError:
        pass
    return out


def evaluate(source_counts, total, geocoded=None):
    """Return a list of (issue_key, human_message) for detected problems.

    source_counts: dict like {"ss.com": 4, "city24.lv": 29}
    geocoded: optional (n_with_coords, n_total) from geocode.coverage()
    """
    issues = []

    if total == 0:
        issues.append((
            "total_zero",
            "No listings were scraped at all. Both scrapers returned nothing. "
            "Most likely cause: a site redesign broke the parsers, or the "
            "requests are being blocked. Check scrapers/ss_com.py and "
            "scrapers/city24.py against the live pages."))
        return issues  # no point reporting anything else

    if config.ALERT_ON_SOURCE_ZERO:
        for src, n in sorted(source_counts.items()):
            if n == 0 and any(v > 0 for k, v in source_counts.items() if k != src):
                issues.append((
                    f"source_zero:{src}",
                    f"Source '{src}' returned 0 listings while other sources "
                    f"returned data ({source_counts}). That parser is probably "
                    f"broken - the other source is masking the failure, so the "
                    f"digest still goes out but with reduced coverage."))

    if 0 < total < config.MIN_EXPECTED_LISTINGS:
        issues.append((
            "low_volume",
            f"Only {total} listing(s) scraped in total, below the expected "
            f"minimum of {config.MIN_EXPECTED_LISTINGS}. This may be a quiet "
            f"day, or a parser may be partially broken. Counts: {source_counts}"))

    if geocoded and geocoded[1] > 0:
        n_ok, n_all = geocoded
        pct = 100.0 * n_ok / n_all
        if pct < config.GEOCODE_MIN_COVERAGE_PCT:
            issues.append((
                "geocode_coverage",
                f"Only {n_ok}/{n_all} listings ({pct:.0f}%) have coordinates, "
                f"below the {config.GEOCODE_MIN_COVERAGE_PCT}% floor. Flats "
                f"without a position are missing from the map and the "
                f"walking-distance section. Check geocode.address_candidates "
                f"against the failed keys in data/geocode_cache.json."))

    return issues


def gone_spike_issue(prev_rows, gone):
    """(key, message) when an implausible share of yesterday's live ads
    vanished — a partially-failed scrape marks them all 'sold', which is
    far more likely than a genuine one-night sales wave. None otherwise."""
    n_prev = len(prev_rows or [])
    n_gone = len(gone or [])
    if (n_gone < config.GONE_SPIKE_MIN
            or n_prev == 0
            or n_gone * 100 < n_prev * config.GONE_SPIKE_PCT):
        return None
    pct = 100.0 * n_gone / n_prev
    return (
        "gone_spike",
        f"{n_gone} of yesterday's {n_prev} live ads vanished ({pct:.0f}%) — "
        f"an implausibly large overnight change. A partially-failed scrape "
        f"is the likely cause: the gone list below is probably wrong. Check "
        f"the per-source counts before trusting it.")


def check(source_counts, total, context="daily", geocoded=None):
    """Evaluate health and print any issues. Returns the list of issue keys."""
    issues = evaluate(source_counts, total, geocoded)
    for issue_key, message in issues:
        print(f"[health] ISSUE ({context}) {issue_key}: {message}")
    return [k for k, _ in issues]


def check_cars(source_counts, source_errors):
    """Car-source health (log-only; printed with context 'cars').

      - source_failed:<name> : scraper raised or produced nothing eligible
      - low_eligible:<name>  : source returned >= CAR_MIN_EXPECTED_RAW ads
                               but the eligible share is implausibly low —
                               the parser/field mapping is likely broken
    """
    issues = []
    source_errors = source_errors or {}
    for name, err in sorted(source_errors.items()):
        issues.append((f"source_failed:{name}", str(err)))
    for name, c in sorted((source_counts or {}).items()):
        if name.startswith("_") or name in source_errors or \
                not isinstance(c, dict):
            continue
        raw = c.get("raw", 0)
        ok = c.get("eligible", 0)
        if raw >= config.CAR_MIN_EXPECTED_RAW and ok < max(3, int(raw * 0.05)):
            issues.append((
                f"low_eligible:{name}",
                f"Only {ok}/{raw} listing(s) eligible (<5%). If this is "
                f"not a genuinely thin day the {name} parser or the "
                f"eligible() field mapping is probably broken."))
    for issue_key, message in issues:
        print(f"[health] ISSUE (cars) {issue_key}: {message}")
    return [k for k, _ in issues]
