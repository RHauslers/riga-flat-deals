# -*- coding: utf-8 -*-
"""
Health checks / failure alerting for the OPERATOR (not the deal recipient).

Scrapers die quietly: a site gets redesigned, the parser matches nothing, the
run reports "0 listings today", no email is sent, and nobody notices for days.
These checks detect that class of failure and email the operator.

Detected conditions:
  - total_zero        : nothing scraped at all (both sources broken / blocked)
  - source_zero:<src> : one source returned 0 while another returned > 0
                        (that source's parser is very likely broken)
  - low_volume        : total below MIN_EXPECTED_LISTINGS (but not zero)

Alerts are throttled to once per issue per day (data/ops_alerts.json) so an
hourly scan cannot send 24 identical warnings.
"""
import config
import history
import notifier


def evaluate(source_counts, total):
    """Return a list of (issue_key, human_message) for detected problems.

    source_counts: dict like {"ss.com": 4, "city24.lv": 29}
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

    return issues


def check_and_alert(source_counts, total, context="daily"):
    """Evaluate health and email the operator about any new issues.

    Returns the list of issue keys that triggered an alert this run.
    """
    if not config.HEALTH_ALERTS_ENABLED:
        return []

    issues = evaluate(source_counts, total)
    if not issues:
        return []

    sent_keys = []
    for issue_key, message in issues:
        print(f"[health] ISSUE {issue_key}: {message}")
        if not history.should_send_ops_alert(issue_key):
            print(f"[health] already alerted '{issue_key}' today - throttled")
            continue
        ok, info = notifier.send_ops_alert(issue_key, message, source_counts,
                                          total, context)
        if ok:
            history.mark_ops_alert_sent(issue_key)
            sent_keys.append(issue_key)
        else:
            print(f"[health] could not send ops alert: {info}")
    return sent_keys
