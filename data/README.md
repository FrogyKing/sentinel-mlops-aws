# Data provenance and limitations

The default demo generates **synthetic** nonlinear fraud-like data with a 5% positive rate. It is deliberately easier and more balanced than real fraud. Its measurements are engineering evidence, not estimates of production performance.

`sentinel.data.acquire_public(Path("data/raw/creditcard.csv"))` fetches the public CSV referenced in the [official TensorFlow imbalanced classification tutorial](https://www.tensorflow.org/tutorials/structured_data/imbalanced_data), a mirror of the ULB European credit card dataset. Source data contains 284,807 transactions over two days in September 2013, with 492 frauds (approximately 0.172%). It includes Time, Amount and anonymized PCA features V1–V28. Dataset access is public without account credentials; availability depends on the upstream mirror. OpenML acquisition was evaluated but its API and direct parquet returned HTTP 500 during local execution. Raw CSV is roughly 150 MB and never committed. The acquisition function bounds downloads to 200 MiB and saves source metadata, row count, licensing metadata, ordering proxy status and SHA-256 of the acquired CSV.

The [upstream Kaggle listing](https://www.kaggle.com/datasets/mlg-ulb/creditcardfraud) identifies Open Database / Database Contents terms. Review its current terms and attribution obligations before redistribution. A mirror's 'Public' access designation does not grant additional rights. Cite Dal Pozzolo et al., *Calibrating Probability with Undersampling for Unbalanced Classification*, IEEE CIDM, 2015. No dataset is redistributed here.

Training accepts either schema: numeric features are inferred, excluding target and time. Rows are deduplicated before a chronological 60/20/20 split; preprocessing fits training rows only. Threshold and candidate selection use validation labels only. Test rows are held out. Published row order is only a proxy when Time is unavailable; acquisition reports this explicitly. Each chronological partition must contain both classes or training fails.

Anonymized PCA features limit business explanations and realistic drift simulation. Two days of old data cannot establish modern fraud generalization, seasonal robustness, account-level separation, or absence of temporal dependence. Original account IDs are unavailable; exact deduplication cannot prevent all same-customer leakage. Synthetic feature shifts demonstrate distribution monitoring only. Data drift does not establish concept drift or performance degradation. Re-estimate operational costs and thresholds with representative labeled data before real use.

## DVC workflow

The local dataset pointer is `data/raw/creditcard.csv.dvc`; its file hash and size are
committed, while records stay ignored. `dvc.yaml` tracks preprocessing/training sources,
the dependency lock and CSV as inputs. `dvc.lock` records the actually executed pipeline.
The selected model is exported separately from serving state and cached with DVC.

```sh
uvx --from 'dvc[s3]==3.67.1' dvc status
uvx --from 'dvc[s3]==3.67.1' dvc repro
```

On a fresh clone, acquire the public data before reproducing. No remote cache has been
configured or synchronized. An operator may configure a project-owned S3 DVC remote
later; that incurs storage/request charges and is outside this delivery's execution scope.
