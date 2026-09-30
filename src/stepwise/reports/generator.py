from __future__ import annotations

import html

from stepwise.core.models import AnalysisResult


def _format(value: object, digits: int = 2) -> str:
    if value is None:
        return "Unavailable"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return html.escape(str(value))


def generate_user_report(result: AnalysisResult) -> str:
    summary = result.summary
    cards = "".join(
        f"""
        <article class="finding">
          <div class="finding-head"><h3>{html.escape(card.title)}</h3><span class="badge {card.level.lower()}">{html.escape(card.level)}</span></div>
          <p><strong>What this suggests:</strong> {html.escape(card.interpretation)}</p>
          <p><strong>Sensor evidence:</strong> {html.escape(" | ".join(card.evidence))}</p>
          <p><strong>Recommended next step:</strong> {html.escape(card.action)}</p>
          <p class="limitation">{html.escape(card.limitation)}</p>
        </article>"""
        for card in result.screening_cards
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>StepWise Analysis Report</title><style>
:root{{--ink:#13242f;--navy:#123b4b;--teal:#238b78;--line:#d8e2e6;--soft:#f4f7f8}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--soft);color:var(--ink);font-family:Arial,sans-serif}}
header{{background:var(--navy);color:#fff;padding:32px max(24px,calc((100% - 1040px)/2))}} header h1{{margin:0 0 8px;font-size:30px}}
main{{max-width:1040px;margin:auto;padding:24px}} section{{background:#fff;border:1px solid var(--line);border-radius:8px;padding:22px;margin-bottom:18px}}
.metrics{{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px}} .metric{{border-left:4px solid var(--teal);padding:10px 12px;background:#f9fbfb}}
.metric span{{display:block;color:#60727d;font-size:13px}} .metric strong{{font-size:22px}} .finding{{border-top:1px solid var(--line);padding:16px 0}} .finding:first-of-type{{border-top:0}}
.finding-head{{display:flex;align-items:center;justify-content:space-between;gap:12px}} h2,h3{{color:var(--navy)}} .badge{{padding:5px 10px;border-radius:999px;font-weight:700;font-size:12px;background:#e8eef1}}
.badge.high{{background:#fee2e2;color:#8a1c1c}} .badge.medium{{background:#fff0c7;color:#755100}} .badge.low{{background:#dff4eb;color:#17624d}}
.limitation{{color:#60727d;font-size:14px}} img{{width:100%;height:auto;border:1px solid var(--line)}} footer{{max-width:1040px;margin:auto;padding:0 24px 30px;color:#60727d}}
</style></head><body><header><h1>StepWise Analysis Report</h1><p>Generated {result.generated_at:%Y-%m-%d %H:%M UTC}</p></header><main>
<section><h2>Trial summary</h2><div class="metrics">
<div class="metric"><span>Data quality</span><strong>{_format(summary.get("data_quality"))}</strong></div>
<div class="metric"><span>Detected stances</span><strong>{_format(summary.get("detected_steps_single_foot"), 0)}</strong></div>
<div class="metric"><span>Sample rate</span><strong>{_format(summary.get("estimated_sample_rate_hz"))} Hz</strong></div>
<div class="metric"><span>Duration</span><strong>{_format(summary.get("duration_s"))} s</strong></div></div></section>
<section><h2>Screening results</h2>{cards}</section>
<section><h2>Pressure and stance timing</h2><img src="pressure.png" alt="Pressure traces and stance phases"></section>
<section><h2>Foot orientation</h2><img src="orientation.png" alt="Pitch, roll, and yaw signals"></section>
</main><footer>{html.escape(result.disclaimer)}</footer></body></html>"""
