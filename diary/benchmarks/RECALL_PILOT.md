# Archived exploratory recall pilot

This preliminary experiment is retained for reproducibility, not presented as
representative Diary performance evidence. Its synthetic, manually checkpointed
workload did not distinguish the two memory approaches. It did not test repeated
native context compaction during long-running real work.

The run used GPT-5.5 / medium and completed 39 model calls. See the
[benchmark methods and reproduction commands](README.md) for the harness details.
No measurements have been changed or discarded.

### Recall after controlled resets

![Measured recall failure curves; both arms remain at zero in this pilot.](../assets/recall-comparison.svg)

| Cumulative history tokens | Requirement checks per arm | Rolling-summary failures | Diary failures |
| ---: | ---: | ---: | ---: |
| 4,000 | 96 | 0 | 0 |
| 8,000 | 144 | 0 | 0 |
| 16,000 | 192 | 0 | 0 |
| 32,000 | 240 | 0 | 0 |
| **Total** | **672** | **0** | **0** |

Each point combines three histories. Requirements recur across checkpoints, so
672 checks are not 672 independent trials. The final histories contain 240 distinct
required codes across the three seeds. Latest corrections, obsolete-value reuse,
completed-action recall, proposed repetition, pending actions, and unknown-value
fabrication all had zero errors in both arms. Three full-history positive controls
passed 240/240 exact requirements.

The rolling-summary target was 2,048 estimated tokens, and no observed summary
exceeded it. Complete answers needed 552–1,399 estimated tokens, so the target did
not force facts to be discarded. Summaries were not clipped or tuned after seeing
results. The flat curves are an observation, not missing data.

The two representations had different costs:

| History tokens | Mean summary memory tokens | Mean Diary memory tokens | Mean summary probe time | Mean Diary probe time |
| ---: | ---: | ---: | ---: | ---: |
| 4,000 | 867.7 | 4,175.7 | 14.15 s | 14.54 s |
| 8,000 | 1,456.0 | 8,370.7 | 18.34 s | 19.58 s |
| 16,000 | 1,783.3 | 16,645.0 | 24.42 s | 37.34 s |
| 32,000 | 1,659.3 | 33,077.7 | 29.47 s | 50.06 s |

Memory sizes are `o200k_base` estimates for the supplied memory payload, not the
whole provider request. This Diary arm deliberately expands the selected full
block after compact recovery; it is not an optimized selective-retrieval study.
Its extra available history did not improve scores in this workload.

Summary maintenance is additional work and must not disappear from the comparison:

| Model-call category | Calls | Reported input tokens | Cached input tokens | Output tokens | Sum of call wall times |
| --- | ---: | ---: | ---: | ---: | ---: |
| Rolling-summary updates | 12 | 328,040 | 174,592 | 18,197 | 369.52 s |
| Summary continuations | 12 | 238,891 | 185,856 | 12,052 | 259.15 s |
| Diary continuations | 12 | 408,403 | 181,760 | 17,681 | 364.56 s |
| Full-history controls | 3 | 151,617 | 41,088 | 4,709 | 95.18 s |

Input counts include cached input, not an additional amount to add to it. Raw data
also retains separately reported reasoning counters; do not add them again to
output without establishing counter semantics. Call times are summed across three
concurrent seed sequences, not measured total elapsed experiment time. Diary
logging here was harness-managed, so this table is **not an end-to-end ROI test**.
Adding the separate writer-fixture cost would not make it one.

The [manifest](results/recall-20260905-gpt55/manifest.json),
[pre-generated fixtures and oracles](results/recall-20260905-gpt55/synthetic-fixtures-and-oracles.json),
and [results](results/recall-20260905-gpt55/results.json) are public. Individual call
files in that directory contain exact synthetic inputs, final responses, usage,
timing, and scores. A separate agent review recomputed all scores and checked hashes;
this is not an external laboratory replication.
