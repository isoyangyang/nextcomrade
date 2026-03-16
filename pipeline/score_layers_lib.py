"""
pipeline/score_layers_lib.py
-----------------------------
Shared scoring functions used by both 02_score_layers.py and 04_train_model.py.
Keeping them here avoids duplication and import path issues.

Exported:
  - score_layer2_from_career(member) -> float
  - build_network_graph(members) -> nx.DiGraph
  - score_layer4(member_id, graph) -> float
  - LEVEL_SCORES dict
"""

import networkx as nx

# ── CAREER LEVEL SCORES ───────────────────────────────────────────────────────

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

# Historical ages at first provincial secretary role — used as cohort benchmark
HISTORICAL_PROV_SEC_AGES = [52, 54, 55, 53, 56, 50, 57, 54, 53, 55, 58, 51, 56, 54]


# ── LAYER 2 — CAREER TRAJECTORY ───────────────────────────────────────────────

def score_layer2_from_career(member: dict) -> float:
    """
    Scores a member's career trajectory between 0.0 and 1.0.
    Used by both 02_score_layers.py and 04_train_model.py.
    """
    career = member.get("career", [])
    flags  = member.get("flags", {})
    birth  = member.get("birth_year")
    tier   = member.get("tier", "cc")

    if not career and tier == "cc":
        return 0.15

    score = 0.0

    # 1. Peak role quality
    role_levels = [LEVEL_SCORES.get(r.get("level", ""), 0.2) for r in career]
    peak_role   = max(role_levels) if role_levels else 0.2
    score      += peak_role * 0.40

    # 2. Provincial secretary breadth
    prov_sec_count = len([r for r in career if r.get("level") == "provincial_secretary"])
    score         += min(1.0, prov_sec_count * 0.4) * 0.20

    # 3. Central pipeline experience
    has_pipeline = flags.get("has_central_pipeline", False) or any(
        r.get("level") == "central_pipeline" for r in career
    )
    score += 0.15 if has_pipeline else 0.0

    # 4. Career velocity — age at first provincial secretary role
    first_prov_age = None
    if birth:
        for r in sorted(career, key=lambda x: x.get("start_year", 9999)):
            if r.get("level") == "provincial_secretary":
                first_prov_age = r.get("start_year", 0) - birth
                break

    if first_prov_age is not None:
        benchmark = sum(HISTORICAL_PROV_SEC_AGES) / len(HISTORICAL_PROV_SEC_AGES)
        velocity  = max(0.0, min(1.0, (benchmark - first_prov_age + 10) / 15))
        score    += velocity * 0.15
    else:
        score += 0.05

    # 5. Experience diversity
    has_provincial = any(r.get("level") in (
        "provincial_secretary", "vice_provincial", "provincial_deputy"
    ) for r in career)
    has_central = any(r.get("level") in (
        "central_pipeline", "state_council_vp", "state_council_premier",
        "cmc_vice_chair", "state_council_minister"
    ) for r in career)
    score += (0.05 if has_provincial else 0.0) + (0.05 if has_central else 0.0)

    return round(min(1.0, score), 4)


# ── LAYER 4 — NETWORK GRAPH ───────────────────────────────────────────────────

def build_network_graph(members: list) -> nx.DiGraph:
    """
    Build a directed weighted patronage graph from members list.
    Nodes = member ids. Edges = weighted relationships.
    """
    G = nx.DiGraph()

    for m in members:
        G.add_node(m["id"], name=m.get("name_en", m["id"]), tier=m.get("tier", "cc"))

    # Ensure Xi is always present as anchor node
    if "xi_jinping" not in G:
        G.add_node("xi_jinping", name="Xi Jinping", tier="gs")

    for m in members:
        mid     = m["id"]
        network = m.get("network", {})

        # Patron: strong directed tie (patron -> protege)
        patron = network.get("patron")
        if patron and patron != mid:
            if patron not in G:
                G.add_node(patron, name=patron, tier="historical")
            G.add_edge(patron, mid, weight=3.0, type="patron")

        # Mentors: strong directed tie
        for mentor in network.get("mentors", []):
            if mentor and mentor != mid:
                if mentor not in G:
                    G.add_node(mentor, name=mentor, tier="historical")
                if not G.has_edge(mentor, mid):
                    G.add_edge(mentor, mid, weight=3.0, type="mentor")

        # Shared service: weaker bidirectional tie
        for colleague in network.get("shared_service", []):
            if colleague and colleague != mid:
                if colleague not in G:
                    G.add_node(colleague, name=colleague, tier="historical")
                if not G.has_edge(colleague, mid):
                    G.add_edge(colleague, mid, weight=1.0, type="shared_service")
                if not G.has_edge(mid, colleague):
                    G.add_edge(mid, colleague, weight=1.0, type="shared_service")

    return G


def score_layer4(member_id: str, graph: nx.DiGraph) -> float:
    """
    Personalised PageRank anchored to Xi Jinping.
    Returns proximity score 0.0–1.0 (higher = closer to Xi).
    """
    if member_id not in graph or "xi_jinping" not in graph:
        return 0.0
    try:
        pr = nx.pagerank(
            graph,
            alpha=0.85,
            personalization={"xi_jinping": 1.0},
            weight="weight",
            max_iter=200,
        )
        return round(pr.get(member_id, 0.0), 6)
    except Exception:
        return 0.0
