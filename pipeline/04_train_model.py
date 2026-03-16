"""
pipeline/04_train_model.py
--------------------------
Calibrates layer combination weights using historical congress outcomes.
Runs weekly. Requires at least 60 days of media_history.json data.
Updates data/model_weights.json in place.

Method:
  - Loads historical PSC appointments from members.json
  - For each historical congress, scores each then-eligible candidate
    using layers 2 and 4 (layer 3 unavailable for pre-2017 events)
  - Uses logistic regression to learn which layer weights best predict
    who actually made it onto the PSC
  - Blends learned weights with prior weights to avoid overfitting
    on the small historical dataset (~200 data points)

Run: python pipeline/04_train_model.py
"""

import json
import datetime
import math
from pathlib import Path

try:
    import numpy as np
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    HAS_SKLEARN = True
except ImportError:
    HAS_SKLEARN = False
    print("WARNING: scikit-learn not available. Using prior weights only.")

ROOT          = Path(__file__).parent.parent
MEMBERS_FILE  = ROOT / "data" / "members.json"
HISTORY_FILE  = ROOT / "data" / "media_history.json"
WEIGHTS_FILE  = ROOT / "data" / "model_weights.json"

PRIOR_WEIGHTS = {
    "layer2_career":  0.35,
    "layer3_media":   0.30,
    "layer4_network": 0.35,
}

MIN_HISTORY_DAYS = 60   # don't attempt media weight training below this threshold
BLEND_ALPHA      = 0.4  # how much to blend new weights vs prior (0=all prior, 1=all new)


def load_json(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def history_depth(history: dict) -> int:
    """Return the maximum number of days in any member's series."""
    return max(
        (len(s) for s in history.get("series", {}).values() if s),
        default=0
    )


def blend_weights(learned: dict, prior: dict, alpha: float) -> dict:
    """Blend learned weights toward prior to avoid overfitting."""
    blended = {}
    for k in prior:
        blended[k] = alpha * learned.get(k, prior[k]) + (1 - alpha) * prior[k]
    # Normalise to sum to 1.0
    total = sum(blended.values())
    return {k: round(v / total, 4) for k, v in blended.items()}


def main():
    print(f"04_train_model.py — {datetime.datetime.now().isoformat()}")

    members_data = load_json(MEMBERS_FILE)
    weights      = load_json(WEIGHTS_FILE)
    history      = load_json(HISTORY_FILE) if HISTORY_FILE.exists() else {"series": {}}
    depth        = history_depth(history)

    print(f"Media history depth: {depth} days (minimum for training: {MIN_HISTORY_DAYS})")

    # Always update the metadata even if we don't retrain
    weights["training_data_days"] = depth
    weights["last_trained"]       = datetime.date.today().isoformat()

    if not HAS_SKLEARN:
        print("scikit-learn not available — keeping prior weights.")
        with open(WEIGHTS_FILE, "w") as f:
            json.dump(weights, f, indent=2)
        return

    # Import layer scoring functions from step 2
    import sys
    sys.path.insert(0, str(ROOT / "pipeline"))
    from score_layers_lib import score_layer2_from_career, build_network_graph, score_layer4

    # Build features from historical outcomes
    # Each data point: (member, congress) -> label (1=made PSC, 0=did not)
    outcomes   = members_data.get("historical_outcomes", {}).get("congresses", [])
    all_members = list(members_data.get("members", []))

    if not outcomes:
        print("No historical outcomes found in members.json — keeping prior weights.")
        with open(WEIGHTS_FILE, "w") as f:
            json.dump(weights, f, indent=2)
        return

    # Build network graph from current member data
    graph = build_network_graph(all_members)

    X = []   # feature vectors
    y = []   # labels

    for congress in outcomes:
        year        = congress["year"]
        new_psc_ids = set(congress["new_psc_members"])

        for m in all_members:
            mid = m["id"]
            # Career score (always available)
            l2 = score_layer2_from_career(m)
            # Network score (always available)
            l4 = score_layer4(mid, graph)
            # Media score — only use if we have history, else 0.5 (neutral)
            l3 = 0.5 if depth < MIN_HISTORY_DAYS else 0.5   # placeholder for now

            label = 1 if mid in new_psc_ids else 0
            X.append([l2, l3, l4])
            y.append(label)

    if not X or sum(y) < 3:
        print(f"Insufficient training data ({sum(y)} positive examples) — keeping prior weights.")
        with open(WEIGHTS_FILE, "w") as f:
            json.dump(weights, f, indent=2)
        return

    print(f"\nTraining on {len(X)} examples ({sum(y)} PSC appointments)")

    X_arr = np.array(X)
    y_arr = np.array(y)

    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X_arr)

    model = LogisticRegression(
        C=0.5,                    # regularisation — important for small dataset
        class_weight="balanced",  # handle class imbalance (few PSC vs many non-PSC)
        max_iter=1000,
        random_state=42,
    )
    model.fit(X_scaled, y_arr)

    # Extract coefficients as weight proportions
    coefs     = model.coef_[0]
    coefs_pos = [max(0.01, c) for c in coefs]   # clip negatives to small positive
    total     = sum(coefs_pos)
    learned   = {
        "layer2_career":  coefs_pos[0] / total,
        "layer3_media":   coefs_pos[1] / total,
        "layer4_network": coefs_pos[2] / total,
    }

    print(f"Learned weights (pre-blend): {learned}")

    # Blend with prior
    blended = blend_weights(learned, PRIOR_WEIGHTS, BLEND_ALPHA)
    print(f"Blended weights (alpha={BLEND_ALPHA}): {blended}")

    weights["layer_weights"]      = blended
    weights["training_data_days"] = depth
    weights["last_trained"]       = datetime.date.today().isoformat()

    with open(WEIGHTS_FILE, "w", encoding="utf-8") as f:
        json.dump(weights, f, indent=2, ensure_ascii=False)

    print(f"\nDone. Updated {WEIGHTS_FILE}")


if __name__ == "__main__":
    main()
