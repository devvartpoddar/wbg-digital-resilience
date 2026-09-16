#!/usr/bin/env python3
"""Stage 3: score every paragraph with each trained asset classifier.

Reads  data/intermediate/models/asset_*.npz, data/intermediate/models/asset_models.json
       data/paragraph_emb.npy, data/emb_index.csv, data/paragraphs.csv
       inputs/labels/paragraph_labels.csv   (optional, for the held-out summary)
Writes data/intermediate/detect/paragraph_asset.csv
       data/detect_assets_report.txt

  python3 src/detect_assets.py
  python3 src/detect_assets.py --top 30

All eight rows are written for every paragraph, including those below threshold.
That is methodology Stage 3 step 2, and the reason is operational: the operating
threshold is meant to be set later on a calibration draw, and keeping only the
fired rows would mean re-running the model every time it moved.

The report is built to answer "where is this weak", not "did it work". Fire rate
is the number to read first: a class firing on a third of the corpus is broken
whatever its cross-validated AUC says, because the labelled draw was enriched
and cannot tell you that.
"""
import argparse, csv, json, os, sys, time
from collections import defaultdict

import numpy

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DETECT_DIR = os.path.join("intermediate", "detect")
MODEL_DIR = os.path.join("intermediate", "models")

COLS = ["paragraph_id", "asset_id", "probability", "threshold", "fired",
        "model_version", "threshold_version", "run_id"]

# Blocks the classifiers were trained on. Everything else is still scored and
# still written - filtering belongs at query time - but the fire rates are
# reported separately for these, because a probability on a table row is not
# comparable to one on a paragraph of prose.
PROSE = ("narrative", "annex")


def load_models(data):
    path = os.path.join(data, MODEL_DIR, "asset_models.json")
    if not os.path.exists(path):
        raise SystemExit(f"detect: no {path}. Run src/train_assets.py first.")
    with open(path, encoding="utf-8") as fh:
        manifest = json.load(fh)
    models = {}
    for aid in sorted(manifest["classes"]):
        npz = os.path.join(data, MODEL_DIR, f"asset_{aid}.npz")
        if not os.path.exists(npz):
            raise SystemExit(f"detect: {aid} is in the manifest but {npz} is missing")
        z = numpy.load(npz)
        models[aid] = {"w": numpy.asarray(z["w"], dtype="float32"),
                       "threshold": float(z["threshold"]),
                       "a": float(z["platt_a"]), "b": float(z["platt_b"])}
    return manifest, models


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=os.path.join(ROOT, "data"))
    ap.add_argument("--inputs", default=os.path.join(ROOT, "inputs"))
    ap.add_argument("--top", type=int, default=20,
                    help="highest-scoring paragraphs to print per class")
    ap.add_argument("--run-id", default="")
    args = ap.parse_args()

    manifest, models = load_models(args.data)
    with open(os.path.join(args.data, "emb_index.csv"), newline="", encoding="utf-8") as fh:
        index = list(csv.DictReader(fh))
    with open(os.path.join(args.data, "paragraphs.csv"), newline="", encoding="utf-8") as fh:
        meta = {r["paragraph_id"]: r for r in csv.DictReader(fh)}
    arr = numpy.load(os.path.join(args.data, "paragraph_emb.npy"), mmap_mode="r")
    if arr.shape[0] != len(index):
        raise SystemExit(f"detect: matrix has {arr.shape[0]} rows, index lists "
                         f"{len(index)} - they are from different runs")

    labelled = set()
    lab_path = os.path.join(args.inputs, "labels", "paragraph_labels.csv")
    if os.path.exists(lab_path):
        with open(lab_path, newline="", encoding="utf-8") as fh:
            labelled = {(r["paragraph_id"], r["asset_id"]) for r in csv.DictReader(fh)}

    run_id = args.run_id or time.strftime("detect-%Y%m%d-%H%M%S", time.gmtime())
    model_version = manifest.get("trained_at", "")
    pids = [r["paragraph_id"] for r in index]
    blocks = numpy.array([meta.get(p, {}).get("block", "") for p in pids])
    is_prose = numpy.isin(blocks, PROSE)

    os.makedirs(os.path.join(args.data, DETECT_DIR), exist_ok=True)
    out_path = os.path.join(args.data, DETECT_DIR, "paragraph_asset.csv")
    tmp = out_path + ".part"

    # Scored in blocks so the 34,005 x 3072 matrix is never fully resident.
    CHUNK = 4096
    scores = {aid: numpy.empty(len(pids), dtype="float32") for aid in models}
    for lo in range(0, len(pids), CHUNK):
        X = numpy.asarray(arr[lo:lo + CHUNK], dtype="float32")
        for aid, m in models.items():
            scores[aid][lo:lo + CHUNK] = X @ m["w"]

    probs, fired = {}, {}
    for aid, m in models.items():
        probs[aid] = 1.0 / (1.0 + numpy.exp(-(m["a"] * scores[aid] + m["b"])))
        fired[aid] = scores[aid] >= m["threshold"]

    with open(tmp, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(COLS)
        for i, pid in enumerate(pids):
            for aid, m in models.items():
                w.writerow([pid, aid, f"{probs[aid][i]:.6f}", f"{m['threshold']:.6f}",
                            "true" if fired[aid][i] else "false",
                            model_version, "f1-on-training-draw", run_id])
    os.replace(tmp, out_path)

    out = [f"run {run_id}", f"model {model_version}",
           f"paragraphs scored: {len(pids):,}   prose (narrative+annex): "
           f"{int(is_prose.sum()):,}", ""]
    out.append(f"{'asset':<21}{'n':>6}{'pos':>5}{'AUC':>7}{'base':>7}"
               f"{'fired':>9}{'% all':>8}{'% prose':>9}")
    out.append("-" * 76)
    for aid in sorted(models, key=lambda a: -manifest["classes"][a]["auc_oof"]):
        c = manifest["classes"][aid]
        f = fired[aid]
        out.append(f"{aid:<21}{c['n']:>6}{c['positives']:>5}{c['auc_oof']:>7.3f}"
                   f"{c['auc_centroid_baseline']:>7.3f}{int(f.sum()):>9,}"
                   f"{100.0 * f.mean():>7.1f}%"
                   f"{100.0 * f[is_prose].mean():>8.1f}%")
    out.append("-" * 76)
    out.append("'base' is the centroid baseline's AUC. Where it matches or beats the")
    out.append("model, ridge is not earning its complexity for that class.")
    out.append("")
    out.append("Fire rate is the number to read first. The labelled draw was enriched")
    out.append("for training, so its 22% positive rate is NOT a corpus base rate and")
    out.append("cannot tell you whether a fire rate is plausible. A class firing on a")
    out.append("third of the corpus is wrong however good its AUC looks.")

    thin = [a for a in models if manifest["classes"][a]["positives"] < 25]
    if thin:
        out.append("")
        out.append(f"Thin classes ({', '.join(thin)}): fewer than 25 positives, so "
                   f"their AUC has wide error bars.")

    multi = numpy.zeros(len(pids), dtype="int32")
    for aid in models:
        multi += fired[aid].astype("int32")
    out.append("")
    out.append("classes firing per paragraph (prose only):")
    for k in range(0, min(9, len(models) + 1)):
        n = int((multi[is_prose] == k).sum())
        out.append(f"  {k}: {n:>7,}  ({100.0 * n / max(int(is_prose.sum()), 1):>5.1f}%)")
    out.append("A paragraph firing for six or seven of eight classes is a gate the")
    out.append("methodology asks to be reported rather than hidden - see section 7.")

    cache = {}
    def text_of(pid):
        r = meta.get(pid)
        if not r:
            return ""
        did = r["doc_id"]
        if did not in cache:
            with open(os.path.join(args.data, "clean", f"{did}.txt"), encoding="utf-8") as fh:
                cache[did] = fh.read()
        return " ".join(cache[did][int(r["char_start"]):int(r["char_end"])].split())

    for aid in sorted(models):
        out.append("")
        out.append("=" * 76)
        out.append(f"{aid}: {args.top} highest-scoring paragraphs")
        out.append("=" * 76)
        order = numpy.argsort(-scores[aid])
        shown = 0
        for i in order:
            if not is_prose[i]:
                continue
            pid = pids[i]
            seen = " [labelled]" if (pid, aid) in labelled else ""
            out.append(f"  p={probs[aid][i]:.3f}  {pid}{seen}")
            out.append(f"     {text_of(pid)[:190]}")
            shown += 1
            if shown >= args.top:
                break

    body = "\n".join(out)
    with open(os.path.join(args.data, "detect_assets_report.txt"), "w",
              encoding="utf-8") as fh:
        fh.write(body + "\n")
    print("\n".join(out[:40]))
    print(f"...\nwrote {out_path}")
    print(f"wrote {os.path.join(args.data, 'detect_assets_report.txt')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
