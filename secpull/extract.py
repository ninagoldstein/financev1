from secpull.models import FinancialFact, METRIC_TAGS
from secpull.quality import COMPLETE, PARTIAL, PARTIAL_QUALITY_TAGS

ACCEPTED_UNITS = {
    "eps_diluted": "USD/shares",
    "shares_diluted": "shares",
}
_VALID_FORMS = {"10-K", "10-Q"}
_REQUIRED_FIELDS = {"val", "fy", "fp", "form", "end", "filed"}


def _fiscal_year_from_end(end_date: str) -> int:
    # Jan/Feb year-ends belong to the prior calendar year (standard US fiscal convention)
    year, month = int(end_date[:4]), int(end_date[5:7])
    return year - 1 if month <= 2 else year


def pick_tag(facts_usgaap: dict, candidates: list[str], unit: str) -> str | None:
    for tag in candidates:
        if facts_usgaap.get(tag, {}).get("units", {}).get(unit):
            return tag
    return None


def dedupe_latest_filed(points: list[dict]) -> list[dict]:
    best: dict[tuple, dict] = {}
    for pt in points:
        key = (pt["fp"], pt["form"], pt["end"])
        if key not in best or pt["filed"] > best[key]["filed"]:
            best[key] = pt
    return list(best.values())


def extract_metrics(cik: str, payload: dict) -> list[FinancialFact]:
    usgaap = payload.get("facts", {}).get("us-gaap", {})
    results = []

    for metric, candidates in METRIC_TAGS.items():
        unit = ACCEPTED_UNITS.get(metric, "USD")
        tag = pick_tag(usgaap, candidates, unit)
        if tag is None:
            continue

        valid = [
            pt for pt in usgaap[tag]["units"][unit]
            if _REQUIRED_FIELDS.issubset(pt) and pt["form"] in _VALID_FORMS
        ]

        quality = PARTIAL_QUALITY_TAGS.get(tag, COMPLETE)
        for pt in dedupe_latest_filed(valid):
            results.append(FinancialFact(
                cik=cik,
                metric=metric,
                tag_used=tag,
                value=float(pt["val"]),
                unit=unit,
                fiscal_year=_fiscal_year_from_end(pt["end"]),
                fiscal_period=pt["fp"],
                form=pt["form"],
                end_date=pt["end"],
                filed_date=pt["filed"],
                coverage_quality=quality,
            ))

    return results
