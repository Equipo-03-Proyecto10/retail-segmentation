# Model studies

Reproducible studies of the segmentation models (phase 23). Nothing here is deployed,
and nothing that runs imports it. Each study is a script that regenerates its tables
from a seed, with its results in [`docs/evidence/`](../docs/evidence/).

| File | What it is |
|---|---|
| `dataset.py` | A deterministic generator with planted behaviour. It writes no file: `data/` and `*.csv` are gitignored |
| `load.py` | Loads that dataset into a local throwaway database, after the three SQL scripts |

## The evaluation dataset

The seed holds 30 customers, which cannot show how a model behaves. `generate()`
builds 5,000 customers and about 52,000 sales over 365 days, and its result is
identified by `Dataset.fingerprint()`. The same seed and size always give the same
fingerprint, and a test pins it, so a change to the rows has to be deliberate.

| Planted group | Share | Purchases a year | Last purchase, days ago | Median ticket |
|---|---|---|---|---|
| `CHAMPION` | 8% | 30 | 0 to 10 | 850 |
| `LOYAL` | 17% | 16 | 0 to 30 | 420 |
| `POTENTIAL` | 15% | 3, all in the last 60 days | 0 to 30 | 320 |
| `AT_RISK` | 15% | 14 | 70 to 120 | 480 |
| `HIBERNATING` | 20% | 5 | 120 to 170 | 340 |
| `LOST` | 25% | 3 | 150 to 330 | 160 |

On top of those groups:
- **Seasonality:** a December peak and a January dip (`SEASON`).
- **Extreme spenders:** 1% of customers spend 25 times their group's ticket, which is what min-max normalization is sensitive to.
- **Mixes:** each group has its own category and channel weights, and 90% of a customer's sales are in one home store.

The group names come from the label vocabulary, but they are what was planted, not
what a model is expected to call them. All of it is synthetic: `load.py` writes every
sale as `SEEDED`, so any run over it is marked Synthetic (ADR-0052). No public dataset
is used.

## Run it

```bash
python -c "from analysis.dataset import generate; print(generate().fingerprint())"
```

To load it, build a local database with the three SQL scripts, then:

```bash
python -m analysis.load --database-url postgresql://postgres@localhost:5432/retail
```

A URL that is not on this machine is refused: this data must never reach the instance.
