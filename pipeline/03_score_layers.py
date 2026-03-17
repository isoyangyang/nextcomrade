"""
pipeline/02_score_layers.py
---------------------------
Reads data/members.json and data/media_history.json.
Computes scores for all four layers for every member.
Outputs an intermediate scores object to data/layer_scores.json.

Layer 1 — Structural eligibility (multiplier 0.0–1.0)
Layer 2 — Career trajectory score (0.0–1.0)
Layer 3 — Media signal with anomaly detection (raw weighted count)
Layer 4 — Factional network centrality (PageRank proximity to Xi)

Run: python pipeline/02_score_layers.py
"""

import json
import math
import datetime
from pathlib import Path
from collections import defaultdict

try:
    import networkx as nx
except ImportError:
    raise ImportError("Run: pip install networkx")

import sys
sys.path.insert(0, str(Path(__file__).parent))
from score_layers_lib import (
    score_layer2_from_career,
    build_network_graph,
    score_layer4,
    LEVEL_SCORES,
    HISTORICAL_PROV_SEC_AGES,
)

# ── PATHS ─────────────────────────────────────────────────────────────────────

ROOT          = Path(__file__).parent.parent
MEMBERS_FILE  = ROOT / "data" / "members.json"
HISTORY_FILE  = ROOT / "data" / "media_history.json"
CN_HISTORY_FILE = ROOT / "data" / "chinese_rss_history.json"
WEIGHTS_FILE  = ROOT / "data" / "model_weights.json"
OUTPUT_FILE   = ROOT / "data" / "layer_scores.json"

# ── LOAD DATA ─────────────────────────────────────────────────────────────────

def load_json(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)

def load_all_members(data: dict) -> list:
    """Return flat list of all members — full profiles + stubs."""
    members = list(data.get("members", []))
    for m in data.get("cc_members_stub", {}).get("members", []):
        # Normalise stubs to match full profile structure
        members.append({
            "id":              m["id"],
            "name_en":         m["name_en"],
            "name_zh":         m.get("name_zh", ""),
            "name_variants":   [m["name_en"]],
            "tier":            "cc",
            "birth_year":      m.get("birth_year"),
            "fixed_probability": None,
            "career":          [],
            "network":         {"patron": None, "mentors": [], "shared_service": [], "factional_label": "unknown"},
            "flags":           m.get("flags", {}),
        })
    return members

# ── LAYER 1 — STRUCTURAL ELIGIBILITY ─────────────────────────────────────────

def score_layer1(member: dict, weights: dict, congress_year: int = 2027) -> float:
    """
    Returns a multiplier between 0.0 and 1.0.
    1.0 = fully eligible. Lower = penalised. Near-zero = effectively eliminated.
    """
    penalties = weights["layer1_penalties"]
    flags     = member.get("flags", {})
    birth     = member.get("birth_year")

    # Fixed probability overrides — bypass all layer scoring
    if member.get("fixed_probability") is not None:
        return -1.0   # sentinel: use fixed_probability directly

    # Confirmed investigation or dismissal — near-zero
    if flags.get("ccdi_confirmed"):
        return penalties["ccdi_confirmed"]
    if flags.get("soft_penalty") == "dismissed_from_office":
        return penalties["dismissed_from_office"]

    # Start with full eligibility
    multiplier = 1.0

    # Age penalty
    if birth:
        age_at_congress = congress_year - birth
        if age_at_congress > 75:
            multiplier *= penalties["age_over_75"]
        elif age_at_congress > 70:
            multiplier *= penalties["age_70_to_75"]
        elif age_at_congress > 68:
            multiplier *= penalties["age_68_to_70"]

    # CCDI investigation (unconfirmed)
    if flags.get("ccdi_investigated"):
        multiplier *= penalties["ccdi_investigated"]

    # Career eligibility penalties
    soft = flags.get("soft_penalty", "")
    if soft == "no_provincial_secretary" or not flags.get("has_provincial_secretary", True):
        # Only penalise if they also lack central pipeline
        if not flags.get("has_central_pipeline", False):
            multiplier *= penalties["no_provincial_secretary"]
    if soft == "military_track":
        multiplier *= penalties["military_track_only"]
    if soft == "factional_demotion":
        multiplier *= penalties["factional_demotion"]
    if soft == "ideologist_role":
        multiplier *= penalties["ideologist_role"]

    return max(0.001, multiplier)


# ── LAYER 2 — CAREER TRAJECTORY ───────────────────────────────────────────────

def score_layer2(member: dict, all_members: list) -> float:
    """Wrapper — delegates to shared lib function."""
    return score_layer2_from_career(member)


# ── LAYER 3 — MEDIA SIGNAL ────────────────────────────────────────────────────

def score_layer3(member_id: str, history: dict, weights: dict) -> dict:
    """
    Returns a dict with:
      - media_score: weighted mention count with Xi boost and position multiplier
      - anomaly_zscore: deviation from personal baseline (None if insufficient data)
      - last_seen: most recent date with any mention
      - mention_count_30d: total mentions in last 30 days
      - xi_count_30d: total Xi co-occurrences in last 30 days
      - position_label: headline/lead/body/unknown
      - is_silent: bool — True if anomaly_zscore < threshold
      - is_prominent: bool — True if anomaly_zscore > threshold
    """
    params   = weights["layer3_params"]
    halflife = params["recency_halflife_days"]
    xi_boost = params["xi_boost_multiplier"]

    series = history.get("series", {}).get(member_id, [])
    if not series:
        return {
            "media_score":        0.0,
            "anomaly_zscore":     None,
            "last_seen":          "Unknown",
            "mention_count_30d":  0,
            "xi_count_30d":       0,
            "position_label":     "no data",
            "is_silent":          False,
            "is_prominent":       False,
        }

    # Sort series by date
    series_sorted = sorted(series, key=lambda e: e.get("date", ""))
    today         = datetime.date.today()

    # Build daily time series from stored data
    daily = {}   # date_str -> {mentions, xi}
    for entry in series_sorted:
        dm = entry.get("daily_mentions", {})
        dx = entry.get("daily_xi", {})
        for d, c in dm.items():
            if d not in daily:
                daily[d] = {"mentions": 0, "xi": 0}
            daily[d]["mentions"] += c
        for d, c in dx.items():
            if d not in daily:
                daily[d] = {"mentions": 0, "xi": 0}
            daily[d]["xi"] += c

    # Weighted score over last 30 days
    score_30d     = 0.0
    mentions_30d  = 0
    xi_30d        = 0
    last_seen     = "Unknown"
    position_mult = 1.0
    position_lbl  = "unknown"

    # Get best position label from most recent entry
    if series_sorted:
        latest = series_sorted[-1]
        articles = latest.get("recent_articles", [])
        if articles:
            position_lbl = "body"
            for art in articles:
                title = art.get("title", "").lower()
                # Position assessment from title alone (full page fetch done in step 1)
                # For now use article count as proxy — headline detection in step 1
                position_lbl = "body"

    for d_str, counts in daily.items():
        try:
            d_date = datetime.date.fromisoformat(d_str)
        except ValueError:
            continue
        days_ago = (today - d_date).days
        if days_ago < 0 or days_ago > 30:
            continue

        m = counts["mentions"]
        x = counts["xi"]
        boosted        = m + (x * xi_boost)
        recency_weight = 2 ** (-days_ago / halflife)
        score_30d     += boosted * recency_weight
        mentions_30d  += m
        xi_30d        += x

        if m > 0 and (last_seen == "Unknown" or d_str > last_seen):
            last_seen = d_str

    # Apply position multiplier from stored label
    recent_articles = []
    if series_sorted:
        recent_articles = series_sorted[-1].get("recent_articles", [])

    # Anomaly detection — Z-score vs personal baseline
    anomaly_window   = params["anomaly_window_days"]
    baseline_window  = params["anomaly_baseline_days"]
    anomaly_zscore   = None

    if len(daily) >= baseline_window // 2:
        # Recent window
        recent_counts = []
        for d_str in sorted(daily.keys(), reverse=True)[:anomaly_window]:
            recent_counts.append(daily[d_str]["mentions"])

        # Baseline window (excludes recent)
        baseline_counts = []
        sorted_dates = sorted(daily.keys(), reverse=True)
        for d_str in sorted_dates[anomaly_window:anomaly_window + baseline_window]:
            baseline_counts.append(daily[d_str]["mentions"])

        if baseline_counts:
            baseline_mean = sum(baseline_counts) / len(baseline_counts)
            baseline_std  = math.sqrt(
                sum((x - baseline_mean) ** 2 for x in baseline_counts) / len(baseline_counts)
            ) if len(baseline_counts) > 1 else 1.0
            if baseline_std < 0.1:
                baseline_std = 0.1   # avoid division by near-zero

            recent_mean   = sum(recent_counts) / len(recent_counts) if recent_counts else 0
            anomaly_zscore = round((recent_mean - baseline_mean) / baseline_std, 2)

    is_silent   = anomaly_zscore is not None and anomaly_zscore < params["anomaly_silent_threshold"]
    is_prominent = anomaly_zscore is not None and anomaly_zscore > params["anomaly_spike_threshold"]

    return {
        "media_score":        round(score_30d, 4),
        "anomaly_zscore":     anomaly_zscore,
        "last_seen":          last_seen,
        "mention_count_30d":  mentions_30d,
        "xi_count_30d":       xi_30d,
        "position_label":     position_lbl,
        "is_silent":          is_silent,
        "is_prominent":       is_prominent,
    }


def blend_chinese_rss(l3_result: dict, cn_history: dict, member_id: str, weights: dict) -> dict:
    """
    Blend Chinese RSS signal into the existing Layer 3 score.
    Chinese RSS mentions are weighted at CN_RSS_BLEND (0.4) of the total
    media signal — meaningful but not dominant, since the feed covers
    only 3 days of content vs GDELT's 30-day window.

    Modifies l3_result in place and returns it.
    """
    CN_RSS_BLEND   = 0.4    # weight given to Chinese RSS in final media score
    XI_ZH          = "习近平"
    xi_boost       = weights["layer3_params"]["xi_boost_multiplier"]

    cn_series = cn_history.get("series", {}).get(member_id, [])
    if not cn_series:
        l3_result["cn_mentions"]        = 0
        l3_result["cn_xi_cooccurrence"] = 0
        return l3_result

    # Use most recent entry
    latest      = sorted(cn_series, key=lambda e: e.get("date",""))[-1]
    cn_mentions = latest.get("mentions", 0)
    cn_xi       = latest.get("xi_cooccurrence", 0)

    # Compute Chinese RSS score — same boosting logic as GDELT
    cn_score = cn_mentions + (cn_xi * xi_boost)

    # Normalise to same scale as GDELT score (cap at 50 — RSS covers 3 days
    # vs GDELT's 30, so a fair comparison is ~1/10th the cap)
    cn_score_norm = min(500.0, cn_score * 10)   # scale up to match 30-day window

    # Blend: final = (1-blend)*gdelt + blend*chinese_rss
    original_score = l3_result["media_score"]
    blended_score  = (1 - CN_RSS_BLEND) * original_score + CN_RSS_BLEND * cn_score_norm

    l3_result["media_score"]         = round(blended_score, 4)
    l3_result["cn_mentions"]         = cn_mentions
    l3_result["cn_xi_cooccurrence"]  = cn_xi
    l3_result["cn_sample_titles"]    = latest.get("sample_titles", [])

    # Update last_seen — Chinese RSS may be more recent
    cn_last_seen = latest.get("last_seen", "Unknown")
    current_last = l3_result.get("last_seen", "Unknown")
    if cn_last_seen and cn_last_seen != "Unknown":
        if current_last == "Unknown" or cn_last_seen > current_last:
            l3_result["last_seen"] = cn_last_seen

    return l3_result


# ── LAYER 4 — network scoring imported from score_layers_lib ─────────────────
# build_network_graph() and score_layer4() are imported from score_layers_lib.py




# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    print(f"02_score_layers.py — {datetime.datetime.now().isoformat()}")

    data       = load_json(MEMBERS_FILE)
    members    = load_all_members(data)
    history    = load_json(HISTORY_FILE) if HISTORY_FILE.exists() else {"series": {}}
    cn_history = load_json(CN_HISTORY_FILE) if CN_HISTORY_FILE.exists() else {"series": {}}
    weights    = load_json(WEIGHTS_FILE)

    print(f"Members: {len(members)} | "
          f"GDELT series: {len(history.get('series', {}))} | "
          f"Chinese RSS series: {len(cn_history.get('series', {}))} | "
          f"Layer weights: {weights['layer_weights']}\n")

    # Build network graph once
    print("Building network graph...")
    graph = build_network_graph(members)
    print(f"  Nodes: {graph.number_of_nodes()} | Edges: {graph.number_of_edges()}\n")

    scores = []

    for m in members:
        mid  = m["id"]
        name = m["name_en"]
        tier = m.get("tier", "cc")

        # Layer 1
        l1_mult = score_layer1(m, weights)
        fixed   = m.get("fixed_probability")

        # Layers 2–4
        l2 = score_layer2(m, members)
        l3 = score_layer3(mid, history, weights)
        l3 = blend_chinese_rss(l3, cn_history, mid, weights)  # blend in Chinese RSS
        l4 = score_layer4(mid, graph)

        # Normalise media score to 0-1 range (cap at 500 raw weighted mentions)
        l3_normalised = min(1.0, l3["media_score"] / 500.0)

        print(f"  [{tier.upper()}] {name:<22} "
              f"L1: {l1_mult:.3f}  L2: {l2:.3f}  "
              f"L3: {l3_normalised:.3f} (raw:{l3['media_score']:.1f}, "
              f"cn:{l3.get('cn_mentions',0)}, z:{l3['anomaly_zscore']})  "
              f"L4: {l4:.4f}"
              + (" [FIXED]" if fixed is not None else "")
              + (" [SILENT]" if l3["is_silent"] else "")
              + (" [SPIKE]" if l3["is_prominent"] else ""))

        scores.append({
            "id":              mid,
            "name_en":         name,
            "name_zh":         m.get("name_zh", ""),
            "tier":            tier,
            "role":            m.get("role", "Central Committee Member"),
            "birth_year":      m.get("birth_year"),
            "age":             (datetime.date.today().year - m["birth_year"]) if m.get("birth_year") else None,
            "fixed_probability": fixed,

            "layer1_multiplier":  l1_mult,
            "layer2_career":      l2,
            "layer3_media":       l3_normalised,
            "layer3_raw":         l3,
            "layer4_network":     l4,

            "flags": m.get("flags", {}),
            "network_faction": m.get("network", {}).get("factional_label", "unknown"),
        })

    # Write intermediate output
    output = {
        "generated_at": datetime.datetime.utcnow().isoformat() + "Z",
        "member_count": len(scores),
        "scores":       scores,
    }
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"\nDone. Written to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
