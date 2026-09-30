# -*- coding: utf-8 -*-
"""
Health checks for the scrape step (log-only — nothing is emailed).

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


def check(source_counts, total, context="daily", geocoded=None):
    """Evaluate health and print any issues. Returns the list of issue keys."""
    issues = evaluate(source_counts, total, geocoded)
    for issue_key, message in issues:
        print(f"[health] ISSUE ({context}) {issue_key}: {message}")
    return [k for k, _ in issues]
