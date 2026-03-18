"""
pipeline/03_combine_scores.py
------------------------------
Reads data/layer_scores.json and data/model_weights.json.
Combines the four layer scores into final probabilities.
Writes scores.json to the repo root for the frontend.

Combination formula:
  raw_score = layer1_multiplier × (
      w2 × layer2_career  +
      w3 × layer3_media   +
      w4 × layer4_network
  )

  For fixed_probability members, raw_score = fixed_probability directly.

  Tier floors are applied after combination.
  Scores are normalised to sum to 100%.

Run: python pipeline/03_combine_scores.py
"""

import json
import datetime
from pathlib import Path

# ── PATHS ─────────────────────────────────────────────────────────────────────

ROOT          = Path(__file__).parent.parent
SCORES_FILE   = ROOT / "data" / "layer_scores.json"
WEIGHTS_FILE  = ROOT / "data" / "model_weights.json"
OUTPUT_FILE   = ROOT / "scores.json"
MEMBERS_FILE  = ROOT / "data" / "members.json"

# ── HELPERS ───────────────────────────────────────────────────────────────────

def load_json(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def days_since(date_str: str) -> int | None:
    if not date_str or date_str in ("Unknown", "N/A", "no data"):
        return None
    try:
        d = datetime.date.fromisoformat(date_str)
        return (datetime.date.today() - d).days
    except ValueError:
        return None


def last_seen_label(date_str: str) -> str:
    d = days_since(date_str)
    if d is None:
        return "Unknown"
    if d == 0:
        return "Today"
    if d == 1:
        return "Yesterday"
    if d < 7:
        return f"{d} days ago"
    if d < 30:
        return f"{d // 7}w ago"
    return date_str


def assessment_label(pct: float, tier: str, l3_raw: dict, l1_mult: float) -> dict:
    """Return assessment label and colour for the frontend."""
    is_silent   = l3_raw.get("is_silent", False)
    is_prominent = l3_raw.get("is_prominent", False)
    z           = l3_raw.get("anomaly_zscore")
    ccdi        = l1_mult < 0.10

    if ccdi:
        return {"label": "⛔ Under review",    "color": "#C0392B"}
    if is_silent:
        return {"label": "📉 Sources concerned", "color": "#C0392B"}
    if is_prominent:
        return {"label": "⚡ Momentum spike",   "color": "#B7950B"}
    if tier == "psc" and pct > 15:
        return {"label": "🔥 Scorching",        "color": "#C0392B"}
    if tier == "psc" and pct > 10:
        return {"label": "📈 Very warm",         "color": "#BA7517"}
    if tier == "psc":
        return {"label": "🤔 Possible",          "color": "#888"}
    if tier == "pb" and pct > 3:
        return {"label": "👀 Watching",          "color": "#1A5276"}
    if tier == "pb":
        return {"label": "🙏 Optimistic",        "color": "#888"}
    if pct > 1.0:
        return {"label": "⚡ Dark horse",        "color": "#B7950B"}
    return   {"label": "✉ Has applied",          "color": "#aaa"}


# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    print(f"03_combine_scores.py — {datetime.datetime.now().isoformat()}")

    layer_data = load_json(SCORES_FILE)
    weights    = load_json(WEIGHTS_FILE)
    members_db = load_json(MEMBERS_FILE)

    lw      = weights["layer_weights"]
    floors  = weights["tier_floors"]
    w2, w3, w4 = lw["layer2_career"], lw["layer3_media"], lw["layer4_network"]

    # Build gossip lookup from members.json succession_notes
    gossip_map = {}
    for m in members_db.get("members", []):
        gossip_map[m["id"]] = m.get("succession_notes", "")

    print(f"Layer weights: career={w2} media={w3} network={w4}\n")

    results = []

    for s in layer_data["scores"]:
        mid   = s["id"]
        tier  = s["tier"]
        fixed = s.get("fixed_probability")
        l1    = s["layer1_multiplier"]
        l2    = s["layer2_career"]
        l3    = s["layer3_media"]
        l3raw = s["layer3_raw"]
        l4    = s["layer4_network"]

        # Fixed probability — bypass combination
        if fixed is not None:
            raw_score = fixed
        elif l1 == -1.0:
            raw_score = fixed or 0.01
        else:
            # Scale L4 — PageRank values are tiny (0.0001–0.01 range)
            # Scale up so the top network score ≈ 1.0
            # We use 50x as a reasonable multiplier given our graph size
            l4_scaled = min(1.0, l4 * 50.0)
            combined  = w2 * l2 + w3 * l3 + w4 * l4_scaled
            raw_score = l1 * combined

        floor = floors.get(tier, 0.1)
        _ = floor  # retained for reference in model_weights.json but not applied here

        results.append({
            "id":                   mid,
            "name_en":              s["name_en"],
            "name_zh":              s.get("name_zh", ""),
            "tier":                 tier,
            "role":                 s.get("role", "Central Committee Member"),
            "birth_year":           s.get("birth_year"),
            "age":                  s.get("age"),
            "raw_score":            raw_score,
            "fixed":                fixed is not None,
            "age_penalised":        s.get("flags", {}).get("soft_penalty") in (
                                        "age_over_68", "age_marginal"
                                    ) or (
                                        s.get("birth_year") and
                                        2027 - s["birth_year"] > 68
                                    ),
            "layer1_multiplier":    l1,
            "layer2_career":        l2,
            "layer3_media":         l3,
            "layer4_network":       l4,
            "mention_count":        l3raw.get("mention_count_30d", 0),
            "xi_cooccurrence_count": l3raw.get("xi_count_30d", 0),
            "last_seen":            l3raw.get("last_seen", "Unknown"),
            "anomaly_zscore":       l3raw.get("anomaly_zscore"),
            "is_silent":            l3raw.get("is_silent", False),
            "is_prominent":         l3raw.get("is_prominent", False),
            "network_faction":      s.get("network_faction", "unknown"),
            "position_label":       l3raw.get("position_label", "unknown"),
            "succession_notes":     gossip_map.get(mid, ""),
        })

    # Proportional tier minimums — PSC members must score at least
    # 3x the average Politburo score, Politburo at least 3x average CC.
    # Applied only if real scores violate hierarchy, not unconditionally.
    # Fixed members (Wang Huning) are always excluded.
    non_fixed = [r for r in results if not r["fixed"]]

    cc_scores  = [r["raw_score"] for r in non_fixed if r["tier"] == "cc"]
    pb_scores  = [r["raw_score"] for r in non_fixed if r["tier"] == "pb"]
    psc_scores = [r["raw_score"] for r in non_fixed if r["tier"] == "psc"]

    cc_avg  = sum(cc_scores)  / len(cc_scores)  if cc_scores  else 0.01
    pb_avg  = sum(pb_scores)  / len(pb_scores)  if pb_scores  else 0.01
    psc_avg = sum(psc_scores) / len(psc_scores) if psc_scores else 0.01

    pb_min  = cc_avg  * 2.0   # Politburo should be at least 2x CC average
    psc_min = pb_avg  * 3.0   # PSC should be at least 3x Politburo average

    for r in results:
        if r["fixed"]:
            continue
        if r["tier"] == "pb":
            r["raw_score"] = max(r["raw_score"], pb_min * 0.5)
        elif r["tier"] == "psc":
            r["raw_score"] = max(r["raw_score"], psc_min * 0.5)

    # Normalise to percentages
    total = sum(r["raw_score"] for r in results)
    for r in results:
        r["probability"] = round(r["raw_score"] / total * 100, 4)

    # Sort and rank
    results.sort(key=lambda r: r["probability"], reverse=True)
    for i, r in enumerate(results):
        r["rank"] = i + 1

    # Attach assessment labels
    for r in results:
        assessment = assessment_label(r["probability"], r["tier"], {
            "is_silent":     r["is_silent"],
            "is_prominent":  r["is_prominent"],
            "anomaly_zscore": r["anomaly_zscore"],
        }, r["layer1_multiplier"])
        r["assessment_label"] = assessment["label"]
        r["assessment_color"] = assessment["color"]
        r["last_seen_label"]  = last_seen_label(r["last_seen"])

    # Build output
    output = {
        "generated_at":    datetime.datetime.utcnow().isoformat() + "Z",
        "model_version":   2,
        "member_count":    len(results),
        "layer_weights":   lw,
        "members":         results,
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"Written to {OUTPUT_FILE}\n")
    print("Top 10:")
    for r in results[:10]:
        xi   = r.get("xi_cooccurrence_count", 0)
        z    = r.get("anomaly_zscore")
        z_str = f"z:{z:+.1f}" if z is not None else "z:n/a"
        flags = ""
        if r.get("fixed"):          flags += " [FIXED]"
        if r.get("age_penalised"):  flags += " [AGE]"
        if r.get("is_silent"):      flags += " [SILENT]"
        if r.get("is_prominent"):   flags += " [SPIKE]"
        print(f"  {r['rank']:>3}. {r['name_en']:<22} ({r['tier'].upper()}) "
              f"{r['probability']:>6.2f}%  "
              f"[{r['mention_count']}m, {xi}xi, {z_str}, "
              f"L2:{r['layer2_career']:.2f} L3:{r['layer3_media']:.2f} "
              f"L4:{r['layer4_network']:.4f}]{flags}")


if __name__ == "__main__":
    main()
