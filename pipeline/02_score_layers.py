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

# ── PATHS ─────────────────────────────────────────────────────────────────────

ROOT          = Path(__file__).parent.parent
MEMBERS_FILE  = ROOT / "data" / "members.json"
HISTORY_FILE  = ROOT / "data" / "media_history.json"
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

# Historical PSC members' first provincial secretary ages (from members.json outcomes)
# Used as cohort benchmark for career velocity scoring
HISTORICAL_PROV_SEC_AGES = [52, 54, 55, 53, 56, 50, 57, 54, 53, 55, 58, 51, 56, 54]  # approximate from literature

LEVEL_SCORES = {
    "provincial_secretary":       1.0,
    "state_council_premier":      1.0,
    "cmc_vice_chair":             0.9,
    "state_council_vp":           0.85,
    "central_pipeline":           0.8,
    "npc_chair":                  0.5,
    "cppcc_chair":                0.5,
    "state_vp":                   0.4,
    "provincial_deputy":          0.6,
    "vice_provincial_secretary":  0.55,
    "military_theater":           0.7,
    "military_region":            0.6,
    "cmc_staff":                  0.65,
    "state_council_minister":     0.6,
    "central_ministry":           0.55,
    "central_agency":             0.5,
    "soe_head":                   0.4,
    "technical_agency":           0.4,
    "prefecture_secretary":       0.45,
    "vice_provincial":            0.4,
    "diplomat":                   0.3,
    "military_regional":          0.5,
    "military_track":             0.45,
}


def score_layer2(member: dict, all_members: list) -> float:
    """
    Returns a career trajectory score between 0.0 and 1.0.
    Higher = more complete, faster, better-positioned career path.
    """
    career  = member.get("career", [])
    flags   = member.get("flags", {})
    birth   = member.get("birth_year")
    tier    = member.get("tier", "cc")

    if not career and tier == "cc":
        # Stub CC member — use tier as weak prior
        return 0.15

    score = 0.0

    # 1. Peak role quality — highest level role achieved
    role_levels = [LEVEL_SCORES.get(r.get("level", ""), 0.2) for r in career]
    peak_role   = max(role_levels) if role_levels else 0.2
    score      += peak_role * 0.40

    # 2. Provincial secretary breadth — more provinces = broader trust base
    prov_sec_roles = [r for r in career if r.get("level") == "provincial_secretary"]
    prov_count     = len(prov_sec_roles)
    prov_score     = min(1.0, prov_count * 0.4)    # 0: 0.0, 1: 0.4, 2: 0.8, 3+: 1.0
    score         += prov_score * 0.20

    # 3. Central pipeline experience
    has_pipeline = flags.get("has_central_pipeline", False) or any(
        r.get("level") == "central_pipeline" for r in career
    )
    score += (0.15 if has_pipeline else 0.0)

    # 4. Career velocity — age at first provincial secretary role
    first_prov_age = None
    if birth:
        for r in sorted(career, key=lambda x: x.get("start_year", 9999)):
            if r.get("level") == "provincial_secretary":
                first_prov_age = r.get("start_year", 0) - birth
                break

    if first_prov_age is not None:
        # Score relative to historical benchmark (~54 years old)
        benchmark = sum(HISTORICAL_PROV_SEC_AGES) / len(HISTORICAL_PROV_SEC_AGES)
        velocity  = max(0.0, min(1.0, (benchmark - first_prov_age + 10) / 15))
        score    += velocity * 0.15
    else:
        score += 0.05   # partial credit for unknown

    # 5. Experience diversity — penalise single-track careers
    has_provincial = any(r.get("level") in ("provincial_secretary", "vice_provincial",
                                             "provincial_deputy") for r in career)
    has_central    = any(r.get("level") in ("central_pipeline", "state_council_vp",
                                             "state_council_premier", "cmc_vice_chair",
                                             "state_council_minister") for r in career)
    diversity      = (0.05 if has_provincial else 0.0) + (0.05 if has_central else 0.0)
    score         += diversity

    return round(min(1.0, score), 4)


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


# ── LAYER 4 — NETWORK CENTRALITY ─────────────────────────────────────────────

def build_network_graph(members: list) -> nx.DiGraph:
    """
    Build a directed weighted patronage graph.
    Nodes = member ids. Edges = relationships with weights.
    """
    G = nx.DiGraph()

    # Add all members as nodes
    for m in members:
        G.add_node(m["id"], name=m["name_en"], tier=m.get("tier", "cc"))

    # Add Xi as anchor node if not already present
    if "xi_jinping" not in G:
        G.add_node("xi_jinping", name="Xi Jinping", tier="gs")

    # Add edges from network data
    for m in members:
        mid     = m["id"]
        network = m.get("network", {})

        # Strong tie: direct patron relationship
        patron = network.get("patron")
        if patron and patron != mid:
            if patron not in G:
                G.add_node(patron, name=patron, tier="historical")
            G.add_edge(patron, mid, weight=3.0, type="patron")

        # Strong tie: explicit mentors
        for mentor in network.get("mentors", []):
            if mentor and mentor != mid:
                if mentor not in G:
                    G.add_node(mentor, name=mentor, tier="historical")
                if not G.has_edge(mentor, mid):
                    G.add_edge(mentor, mid, weight=3.0, type="mentor")

        # Weak tie: shared service
        for colleague in network.get("shared_service", []):
            if colleague and colleague != mid:
                if colleague not in G:
                    G.add_node(colleague, name=colleague, tier="historical")
                # Shared service is bidirectional and weaker
                if not G.has_edge(colleague, mid):
                    G.add_edge(colleague, mid, weight=1.0, type="shared_service")
                if not G.has_edge(mid, colleague):
                    G.add_edge(mid, colleague, weight=1.0, type="shared_service")

    return G


def score_layer4(member_id: str, graph: nx.DiGraph) -> float:
    """
    Returns personalised PageRank score anchored to xi_jinping.
    Higher = closer to Xi in the patronage network.
    Returns 0.0 if member not in graph or no path to Xi.
    """
    if member_id not in graph:
        return 0.0
    if "xi_jinping" not in graph:
        return 0.0

    try:
        # Personalised PageRank — concentrates probability mass on Xi
        personalization = {"xi_jinping": 1.0}
        pr = nx.pagerank(
            graph,
            alpha=0.85,
            personalization=personalization,
            weight="weight",
            max_iter=200,
        )
        return round(pr.get(member_id, 0.0), 6)
    except nx.PowerIterationFailedConvergence:
        return 0.0
    except Exception:
        return 0.0


# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    print(f"02_score_layers.py — {datetime.datetime.now().isoformat()}")

    data     = load_json(MEMBERS_FILE)
    members  = load_all_members(data)
    history  = load_json(HISTORY_FILE) if HISTORY_FILE.exists() else {"series": {}}
    weights  = load_json(WEIGHTS_FILE)

    print(f"Members: {len(members)} | "
          f"History series: {len(history.get('series', {}))} | "
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
        l4 = score_layer4(mid, graph)

        # Normalise media score to 0-1 range (cap at 500 raw weighted mentions)
        l3_normalised = min(1.0, l3["media_score"] / 500.0)

        print(f"  [{tier.upper()}] {name:<22} "
              f"L1: {l1_mult:.3f}  L2: {l2:.3f}  "
              f"L3: {l3_normalised:.3f} (raw:{l3['media_score']:.1f}, z:{l3['anomaly_zscore']})  "
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
