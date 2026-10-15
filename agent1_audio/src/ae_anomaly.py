"""
Agent 1 - convolutional autoencoder for anomaly detection (owner A2).
Runs on CPU in a few minutes (Lightning AI CPU Studio is enough).

    python agent1_audio/src/ae_anomaly.py --epochs 2     # smoke test
    python agent1_audio/src/ae_anomaly.py                # full run

Idea: the autoencoder only ever sees HEALTHY sounds (normal, normal_brakes, normal_startup)
during training. It learns to compress and rebuild a healthy log-mel spectrogram.
A faulty sound is something it never learned, so it rebuilds it badly:
reconstruction error = anomaly score. No fault labels are needed for training.

Output: agent1_audio/results/ae_anomaly.json (+ comparison with the Isolation Forests),
        models/agent1_ae.pt
"""
import argparse
import csv
import json
import time
from pathlib import Path

import librosa
import numpy as np
import torch
import torch.nn as nn

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data" / "audio" / "processed"
RES = ROOT / "agent1_audio" / "results"
MODELS = ROOT / "models"
SR, WIN = 16000, 1.5
N_MELS, HOP, N_FFT = 64, 256, 1024
FRAMES = 96                                   # 1.5 s = 94 frames, padded to 96 (divisible by 8)
HEALTHY = {"normal", "normal_brakes", "normal_startup"}
SEED = 42


def logmel(w):
    m = librosa.feature.melspectrogram(y=w, sr=SR, n_fft=N_FFT, hop_length=HOP, n_mels=N_MELS)
    m = librosa.power_to_db(m, ref=1.0, top_db=80.0)
    m = m[:, :FRAMES]
    if m.shape[1] < FRAMES:
        m = np.pad(m, ((0, 0), (0, FRAMES - m.shape[1])), mode="edge")
    return m.astype(np.float32)


def windows(x):
    n = int(SR * WIN)
    if len(x) < n:
        return [np.pad(x, (0, n - len(x)))]
    return [x[i:i + n] for i in range(0, len(x) - n + 1, n)]


def load():
    rows = [r for r in csv.DictReader(open(DATA / "metadata.csv", encoding="utf-8"))
            if r["split"] != "hypothesis"]
    cache = RES / "logmel_cache.npz"
    if cache.exists():
        z = np.load(cache, allow_pickle=True)
        if list(z["files"]) == [r["file"] for r in rows]:
            return rows, z["X"], z["clip_id"]
    X, clip_id, t = [], [], time.time()
    for i, r in enumerate(rows):
        x, _ = librosa.load(DATA / r["file"], sr=SR, mono=True)
        for w in windows(x):
            X.append(logmel(w))
            clip_id.append(i)
        if (i + 1) % 300 == 0:
            print(f"  log-mel {i + 1}/{len(rows)} ({time.time() - t:.0f} s)")
    X, clip_id = np.stack(X), np.array(clip_id)
    np.savez(cache, files=np.array([r["file"] for r in rows], dtype=object), X=X, clip_id=clip_id)
    return rows, X, clip_id


class ConvAE(nn.Module):
    """64x96 log-mel -> 64 x 8 x 12 bottleneck -> 64x96."""

    def __init__(self):
        super().__init__()

        def down(i, o):
            return nn.Sequential(nn.Conv2d(i, o, 3, stride=2, padding=1), nn.BatchNorm2d(o),
                                 nn.LeakyReLU(0.1))

        def up(i, o):
            return nn.Sequential(nn.ConvTranspose2d(i, o, 4, stride=2, padding=1),
                                 nn.BatchNorm2d(o), nn.LeakyReLU(0.1))

        self.enc = nn.Sequential(down(1, 16), down(16, 32), down(32, 64))
        self.dec = nn.Sequential(up(64, 32), up(32, 16), nn.ConvTranspose2d(16, 1, 4, 2, 1))

    def forward(self, x):
        return self.dec(self.enc(x))


def errors(model, X, mu, sd, batch=256):
    model.eval()
    out = []
    with torch.no_grad():
        for i in range(0, len(X), batch):
            xb = torch.from_numpy((X[i:i + batch] - mu) / sd).unsqueeze(1)
            out.append(((model(xb) - xb) ** 2).mean(dim=(1, 2, 3)).numpy())
    return np.concatenate(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--epochs", type=int, default=30)
    a = ap.parse_args()
    from sklearn.metrics import roc_auc_score

    torch.manual_seed(SEED)
    np.random.seed(SEED)
    RES.mkdir(parents=True, exist_ok=True)
    MODELS.mkdir(parents=True, exist_ok=True)
    rows, X, clip_id = load()
    y = np.array([r["label"] for r in rows])
    split = np.array([r["split"] for r in rows])
    healthy = np.isin(y, list(HEALTHY))
    win_split, win_healthy = split[clip_id], healthy[clip_id]
    tr = (win_split == "train") & win_healthy
    va = (win_split == "val") & win_healthy
    print(f"{len(X)} windows; training on {tr.sum()} healthy windows, {va.sum()} for validation")

    mu, sd = X[tr].mean(), X[tr].std() + 1e-6
    model = ConvAE()
    opt = torch.optim.Adam(model.parameters(), lr=1e-3, weight_decay=1e-5)
    Xtr = torch.from_numpy((X[tr] - mu) / sd).unsqueeze(1)
    best, best_state, t = 1e9, None, time.time()
    for ep in range(1, a.epochs + 1):
        model.train()
        perm = torch.randperm(len(Xtr))
        for i in range(0, len(Xtr), 64):
            xb = Xtr[perm[i:i + 64]]
            loss = ((model(xb) - xb) ** 2).mean()
            opt.zero_grad()
            loss.backward()
            opt.step()
        v = float(errors(model, X[va], mu, sd).mean())
        if v < best:                                   # early stopping on healthy val error
            best, best_state = v, {k: x.clone() for k, x in model.state_dict().items()}
        print(f"  epoch {ep:2d}  train {loss.item():.4f}  val(healthy) {v:.4f}  ({time.time() - t:.0f} s)")
    model.load_state_dict(best_state)

    err = errors(model, X, mu, sd)
    test = np.where(split == "test")[0]
    clip_err = np.array([err[clip_id == c].mean() for c in test])
    auc = roc_auc_score(~healthy[test], clip_err)
    by_label = {lab: round(float(clip_err[y[test] == lab].mean()), 4)
                for lab in sorted(set(y[test]))}
    healthy_train_err = np.sort(err[tr])

    res = {"model": "conv autoencoder on 64x96 log-mel, trained on healthy sounds only",
           "epochs": a.epochs, "best_val_error": round(best, 4),
           "test_auc_fault_vs_healthy": round(auc, 3),
           "mean_error_by_label": by_label}
    comp = {}
    for name, f in [("MFCC + Isolation Forest", "baseline_mfcc.json"),
                    ("AST + Isolation Forest", "ast.json")]:
        p = RES / f
        if p.exists():
            d = json.loads(p.read_text("utf-8"))
            comp[name] = d.get("anomaly_auc") or d["anomaly_isolation_forest"]["test_auc_fault_vs_healthy"]
    comp["Conv autoencoder"] = res["test_auc_fault_vs_healthy"]
    res["anomaly_auc_comparison"] = comp
    (RES / "ae_anomaly.json").write_text(json.dumps(res, indent=2), encoding="utf-8")
    torch.save({"state": model.state_dict(), "mu": float(mu), "sd": float(sd),
                "healthy_train_err": torch.from_numpy(healthy_train_err),
                # calibration reference = healthy VALIDATION clips (never trained on),
                # so a new healthy sound gets a mid-range score, not ~1.0
                "healthy_val_err": torch.from_numpy(np.sort(err[va]))}, MODELS / "agent1_ae.pt")

    print("\nAnomaly detection, test AUC (fault vs healthy):")
    for k, v in comp.items():
        print(f"  {k:26s} {v}")
    print("\nmean reconstruction error by label (higher = more unusual):")
    for k, v in sorted(by_label.items(), key=lambda kv: kv[1]):
        print(f"  {k:16s} {v}{'   (healthy)' if k in HEALTHY else ''}")


if __name__ == "__main__":
    main()
