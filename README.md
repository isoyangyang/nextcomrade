# Next Comrade — CCP Succession Probability Model

**nextcomrade.com** — A data-driven probability tracker for Xi Jinping's succession.

---

## How It Works

The model combines four independent scoring layers to assign probability
distributions over CCP succession candidates. Scores are updated daily via
GitHub Actions and served as a static JSON file to the frontend.

---

## Repository Structure

```
nextcomrade/
├── index.html                        # Frontend — reads scores.json on load
├── scores.json                       # Daily output — only file frontend reads
├── README.md                         # This file
│
├── data/
│   ├── members.json                  # Biographical database — source of truth
│   ├── media_history.json            # 180-day rolling GDELT time series
│   ├── model_weights.json            # Layer combination weights (auto-updated weekly)
│   └── historical_outcomes.json      # Past congress outcomes for calibration
│
├── pipeline/
│   ├── 01_fetch_media.py             # GDELT fetch → media_history.json
│   ├── 02_score_layers.py            # All four layers → intermediate scores
│   ├── 03_combine_scores.py          # Weighted combination → scores.json
│   └── 04_train_model.py             # Historical calibration → model_weights.json
│
└── .github/workflows/
    ├── daily.yml                     # Runs steps 1–3 every morning at 05:00 UTC
    └── weekly.yml                    # Runs step 4 every Sunday at 03:00 UTC
```

---

## The Four Scoring Layers

### Layer 1 — Structural Eligibility

**What it does:** Applies hard and soft constraints that reflect the informal norms
of CCP succession. Eliminates or penalises candidates who are structurally
ineligible regardless of media prominence.

**Hard constraints (near-zero probability):**
- Confirmed CCDI investigation
- Age over 75 at the next Party Congress (2027)
- Fixed overrides (e.g. Wang Huning — ideologist, not a succession candidate)

**Soft penalties (probability reduction, not elimination):**
- Age 69–75: 85% reduction (AGE_PENALTY_FACTOR = 0.15)
- No provincial secretary experience: 40% reduction
- Military-only career track: 50% reduction
- Confirmed factional demotion (e.g. Hu Chunhua 2022): 60% reduction

**Data source:** `data/members.json` — flags field

**Why soft rather than hard:** The 68-year norm has historical exceptions.
A hard cutoff would miss the next Bo Xilai-style rule-breaking appointment.

---

### Layer 2 — Career Trajectory

**What it does:** Scores each candidate's career path against the historical
pattern of officials who eventually reached the PSC. Faster and more
complete trajectories score higher.

**Features used:**
- Age at first provincial Party secretary appointment (younger = better)
- Number of distinct provinces served (breadth signals national trust)
- Presence of central pipeline roles (Organisation Dept, General Office, CCDI)
- Promotion velocity vs birth-year cohort peers
- Whether career spans both economic governance and political/security roles

**Data source:** `data/members.json` — career array

**Benchmark:** Historical PSC members since 2002, encoded in
`data/members.json` → historical_outcomes

---

### Layer 3 — Media Signal with Anomaly Detection

**What it does:** Tracks each member's Xinhua and People's Daily coverage
as a daily time series. Scores both absolute prominence and deviation from
personal baseline — disappearances are as significant as prominence spikes.

**Sub-signals:**
- **Recency-weighted mention count** — last 30 days, most recent week
  counts double (exponential decay, 7-day half-life)
- **Xi co-occurrence boost** — articles mentioning both the member and Xi
  count 3x (base + 2x multiplier), reflecting political proximity
- **Position bonus** — headline mention = 1.6x, lead paragraph = 1.25x,
  applied to top 30 members only
- **Anomaly Z-score** — mentions in last 14 days vs 90-day personal baseline.
  Z > +2.0: prominence spike (positive signal).
  Z < −2.0 for 14+ days: disappearance signal (SILENT badge, strong negative)

**Data source:** GDELT DOC 2.0 API → `data/media_history.json`

**Why timelinevolraw:** Unlike artlist mode (250-article cap), timelinevolraw
returns true daily counts. This is essential for accurate time series and
anomaly detection.

---

### Layer 4 — Factional Network Centrality

**What it does:** Builds a directed weighted graph of patronage relationships
and computes each candidate's proximity to Xi Jinping in that graph.
CCP succession is fundamentally about coalition building — network position
is the structural predictor that media coverage cannot capture.

**Graph construction:**
- Nodes: all members in `data/members.json`
- Directed edges: patron → protégé (strong tie, weight 3.0)
- Shared provincial service edges (weak tie, weight 1.0)
- Peer edges from same CPS cohort (weak tie, weight 0.5)

**Centrality metric:** Personalised PageRank anchored to Xi Jinping.
This directly measures distance from Xi in the patronage network,
which is the operationally relevant measure for succession.

**Data source:** `data/members.json` — network field

---

## Score Combination

The four layer scores are combined as a weighted sum:

```
final_score = (
    w1 × layer1_eligibility_multiplier ×
    (w2 × layer2_career_score +
     w3 × layer3_media_score +
     w4 × layer4_network_score)
)
```

Layer 1 acts as a multiplier (0.0–1.0) applied to the weighted sum of layers 2–4.
This means ineligible candidates score near-zero regardless of other signals.

Weights (`data/model_weights.json`) are calibrated weekly via logistic regression
against historical congress outcomes (2002–2022). Initial weights before
sufficient training data:

| Layer | Initial Weight | Rationale |
|-------|---------------|-----------|
| Layer 2 (career) | 0.35 | Strongest historical predictor |
| Layer 3 (media) | 0.30 | Real-time signal, noisy |
| Layer 4 (network) | 0.35 | High-quality but sparse data |

Scores are normalised to sum to 100% across all members after application
of tier floors (PSC floor: 5.0, Politburo floor: 1.0, CC floor: 0.1).

---

## Data Sources

| Source | Used for | Cost | Reliability |
|--------|----------|------|-------------|
| GDELT DOC 2.0 API | Daily media signals | Free, no key | High — documented API |
| Xinhua official biographies | Career histories | Free | High |
| Baidu Baike | Career histories, birth dates | Free | Medium-high |
| NPC/CPPCC member registries | Role verification | Free | High |
| Victor Shih et al. (academic) | Network relationships | Free (published) | High |

---

## Running the Pipeline Locally

```bash
# Install dependencies
pip install requests beautifulsoup4 gdeltdoc networkx scikit-learn pandas

# Run full pipeline
python pipeline/01_fetch_media.py    # ~20 min — fetches GDELT data
python pipeline/02_score_layers.py   # ~1 min  — computes layer scores
python pipeline/03_combine_scores.py # ~1 sec  — writes scores.json
python pipeline/04_train_model.py    # ~1 sec  — updates model weights
```

---

## GitHub Actions Schedule

| Workflow | Schedule | Steps | Commits |
|----------|----------|-------|---------|
| `daily.yml` | 05:00 UTC daily | 01, 02, 03 | `data/media_history.json`, `scores.json` |
| `weekly.yml` | 03:00 UTC Sunday | 04 | `data/model_weights.json` |

To trigger manually: GitHub repo → Actions tab → select workflow → Run workflow.

---

## Adding New Members

Edit `data/members.json`. For full profile members, add a complete entry to
the `members` array. For stub members, add to `cc_members_stub.members`.

Minimum required fields for a stub:
```json
{
  "id": "unique_snake_case_id",
  "name_en": "Name In English",
  "name_zh": "中文名字",
  "birth_year": 1965,
  "flags": {
    "age_eligible": true,
    "ccdi_investigated": false
  }
}
```

---

## Known Limitations

1. **GDELT coverage floor:** GDELT indexes major English-language outlets
   reliably. Coverage of very junior CC members may be sparse. Tier floors
   prevent these members from dropping to zero.

2. **180-day history window:** Anomaly detection is unreliable for the first
   60–90 days of operation while baselines are being established.

3. **Article cap:** artlist mode caps at 250 articles per query. This affects
   only position scoring for the most prominent members. timelinevolraw
   mode (used for counts) has no cap.

4. **Network data sparsity:** Factional relationships for newer, less-studied
   officials are incomplete. Network scores for CC stubs are lower confidence.

5. **Ground truth scarcity:** Party congresses happen every 5 years.
   With only ~200 historical PSC appointments since 2002, the trained model
   weights carry significant uncertainty. Treat probability outputs as
   informed estimates, not predictions.

---

## Disclaimer

All probabilities are produced by a meticulous intelligence gathering process,
geopolitical analysis, inside sources, and Zhongnanhai cleaning staff.

---

*Last updated: 2026-03-16 | Schema version: 2*
