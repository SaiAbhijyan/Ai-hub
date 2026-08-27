"""AI-systems protocols — models actually trained here, reporting real metrics.

Nothing in this module reports an accuracy it did not measure on held-out data.
Every model is implemented from scratch in the standard library so that a reader
can check the arithmetic line by line.
"""

from __future__ import annotations

import math
import random


def gradient_descent_learning_rate(epochs: int = 200, samples: int = 400,
                                   seed: int = 23) -> dict:
    """Find where gradient descent converges and where it diverges, by running it.

    A least-squares problem with a known optimum is solved by gradient descent at a
    range of learning rates. For this problem the stability threshold is
    2/L where L is the largest curvature; we locate the empirical threshold and
    compare it to that theoretical bound.
    """
    rng = random.Random(seed)
    true_w, true_b = 2.5, -1.25
    xs = [rng.uniform(-3, 3) for _ in range(samples)]
    ys = [true_w * x + true_b + rng.gauss(0, 0.25) for x in xs]

    # Largest curvature of the MSE objective: 2 * mean(x^2) (plus bias term).
    mean_x2 = sum(x * x for x in xs) / samples
    lipschitz = 2 * max(mean_x2, 1.0)
    theoretical_max_lr = 2.0 / lipschitz

    series = []
    for lr_milli in (1, 5, 10, 50, 100, 200, 400, 800):
        lr = lr_milli / 1000.0
        w, b = 0.0, 0.0
        losses = []
        diverged = False
        for _ in range(epochs):
            grad_w = grad_b = 0.0
            loss = 0.0
            for x, y in zip(xs, ys):
                pred = w * x + b
                err = pred - y
                loss += err * err
                grad_w += 2 * err * x
                grad_b += 2 * err
            loss /= samples
            losses.append(loss)
            if not math.isfinite(loss) or loss > 1e12:
                diverged = True
                break
            w -= lr * grad_w / samples
            b -= lr * grad_b / samples
        final_loss = losses[-1] if losses else float("inf")
        series.append({
            "learning_rate": lr,
            "converged": not diverged,
            "final_loss": round(final_loss, 6) if math.isfinite(final_loss) else None,
            "epochs_run": len(losses),
            "learned_w": round(w, 4) if math.isfinite(w) else None,
            "learned_b": round(b, 4) if math.isfinite(b) else None,
            "w_error": round(abs(w - true_w), 4) if math.isfinite(w) else None,
        })

    converged = [r for r in series if r["converged"]]
    diverged = [r for r in series if not r["converged"]]
    empirical_threshold = (max(r["learning_rate"] for r in converged)
                           if converged else 0.0)
    best = min(converged, key=lambda r: r["final_loss"]) if converged else None
    supported = bool(converged and diverged and best and best["w_error"] < 0.1)
    return {
        "series": series,
        "summary": {
            "true_w": true_w,
            "true_b": true_b,
            "best_learning_rate": best["learning_rate"] if best else None,
            "best_final_loss": best["final_loss"] if best else None,
            "recovered_w": best["learned_w"] if best else None,
            "recovered_b": best["learned_b"] if best else None,
            "largest_converging_lr": empirical_threshold,
            "theoretical_stability_bound": round(theoretical_max_lr, 4),
        },
        "supported": supported,
        "conclusion": (
            f"Gradient descent converged for learning rates up to {empirical_threshold} and "
            f"diverged above it, against a theoretical stability bound of "
            f"{theoretical_max_lr:.3f}. The best rate recovered w={best['learned_w']} against a "
            f"true {true_w} and b={best['learned_b']} against a true {true_b}."
            if best else "No learning rate converged."
        ),
    }


def logistic_regression_training(samples: int = 800, epochs: int = 300,
                                 seed: int = 29) -> dict:
    """Train a logistic-regression classifier and report held-out accuracy.

    Data is generated from two overlapping Gaussian classes, split 70/30 into train
    and test, and the model is trained by gradient descent. The reported accuracy
    is measured on the test split only, and is compared against the majority-class
    baseline so the number means something.
    """
    rng = random.Random(seed)
    data = []
    for _ in range(samples):
        if rng.random() < 0.5:
            point = (rng.gauss(-1.0, 1.0), rng.gauss(-0.5, 1.0), 0)
        else:
            point = (rng.gauss(1.2, 1.0), rng.gauss(0.8, 1.0), 1)
        data.append(point)
    rng.shuffle(data)
    split = int(0.7 * len(data))
    train, test = data[:split], data[split:]

    def sigmoid(z):
        if z < -30:
            return 0.0
        if z > 30:
            return 1.0
        return 1.0 / (1.0 + math.exp(-z))

    w1 = w2 = b = 0.0
    lr = 0.1
    curve = []
    for epoch in range(epochs):
        g1 = g2 = gb = 0.0
        loss = 0.0
        for x1, x2, y in train:
            p = sigmoid(w1 * x1 + w2 * x2 + b)
            loss += -(y * math.log(max(p, 1e-12)) + (1 - y) * math.log(max(1 - p, 1e-12)))
            err = p - y
            g1 += err * x1
            g2 += err * x2
            gb += err
        n = len(train)
        w1 -= lr * g1 / n
        w2 -= lr * g2 / n
        b -= lr * gb / n
        if epoch % max(epochs // 12, 1) == 0 or epoch == epochs - 1:
            curve.append({"epoch": epoch, "train_log_loss": round(loss / n, 5)})

    def accuracy(rows):
        correct = sum(1 for x1, x2, y in rows
                      if (1 if sigmoid(w1 * x1 + w2 * x2 + b) >= 0.5 else 0) == y)
        return correct / len(rows)

    train_acc, test_acc = accuracy(train), accuracy(test)
    majority = max(sum(1 for r in test if r[2] == c) for c in (0, 1)) / len(test)
    tp = sum(1 for x1, x2, y in test if y == 1 and sigmoid(w1 * x1 + w2 * x2 + b) >= 0.5)
    fp = sum(1 for x1, x2, y in test if y == 0 and sigmoid(w1 * x1 + w2 * x2 + b) >= 0.5)
    fn = sum(1 for x1, x2, y in test if y == 1 and sigmoid(w1 * x1 + w2 * x2 + b) < 0.5)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0

    supported = test_acc > majority + 0.05 and curve[-1]["train_log_loss"] < curve[0]["train_log_loss"]
    return {
        "series": curve,
        "summary": {
            "train_examples": len(train),
            "test_examples": len(test),
            "train_accuracy_pct": round(100 * train_acc, 2),
            "test_accuracy_pct": round(100 * test_acc, 2),
            "majority_baseline_pct": round(100 * majority, 2),
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
            "weights": {"w1": round(w1, 4), "w2": round(w2, 4), "b": round(b, 4)},
            "initial_log_loss": curve[0]["train_log_loss"],
            "final_log_loss": curve[-1]["train_log_loss"],
        },
        "supported": supported,
        "conclusion": (
            f"Trained on {len(train)} examples and evaluated on {len(test)} held out, the "
            f"classifier reached {100 * test_acc:.1f}% test accuracy against a "
            f"{100 * majority:.1f}% majority-class baseline (F1 {f1:.3f}). Training log-loss "
            f"fell from {curve[0]['train_log_loss']} to {curve[-1]['train_log_loss']}. The gap "
            f"between train ({100 * train_acc:.1f}%) and test accuracy indicates "
            + ("no meaningful overfitting." if abs(train_acc - test_acc) < 0.05
               else "some overfitting.")
        ),
    }


def kmeans_elbow(clusters: int = 4, points_per_cluster: int = 120,
                 restarts: int = 10, seed: int = 31) -> dict:
    """Run k-means for a range of k and test whether the elbow finds the true k.

    Points are generated around a known number of centres. For each k, Lloyd's
    algorithm is run from several random initialisations and the lowest inertia is
    kept, because a single random start regularly converges to a local optimum bad
    enough to make inertia rise with k — which cannot happen at the true optimum.
    The restart count is a reported parameter, and monotonicity is *measured* and
    reported rather than assumed, so a failure of it is visible in the record.
    """
    rng = random.Random(seed)
    centres = [(rng.uniform(-10, 10), rng.uniform(-10, 10)) for _ in range(clusters)]
    points = []
    for cx, cy in centres:
        for _ in range(points_per_cluster):
            points.append((rng.gauss(cx, 0.8), rng.gauss(cy, 0.8)))

    def lloyd(k: int, init_seed: int, iterations: int = 100) -> float:
        local = random.Random(init_seed)
        cents = local.sample(points, k)
        for _ in range(iterations):
            groups = [[] for _ in range(k)]
            for px, py in points:
                best = min(range(k),
                           key=lambda i: (px - cents[i][0]) ** 2 + (py - cents[i][1]) ** 2)
                groups[best].append((px, py))
            new = []
            for i, g in enumerate(groups):
                if g:
                    new.append((sum(p[0] for p in g) / len(g),
                                sum(p[1] for p in g) / len(g)))
                else:  # empty cluster: re-seed it on a random point
                    new.append(local.choice(points))
            if new == cents:
                break
            cents = new
        return sum(min((px - cx) ** 2 + (py - cy) ** 2 for cx, cy in cents)
                   for px, py in points)

    series = []
    for k in range(1, clusters + 4):
        trials = [lloyd(k, seed * 1000 + k * 37 + r) for r in range(restarts)]
        series.append({
            "k": k,
            "inertia": round(min(trials), 2),
            "worst_restart_inertia": round(max(trials), 2),
            "restart_spread": round(max(trials) - min(trials), 2),
        })

    inertias = [r["inertia"] for r in series]
    decreasing = all(b <= a for a, b in zip(inertias, inertias[1:]))
    second_diffs = [inertias[i - 1] - 2 * inertias[i] + inertias[i + 1]
                    for i in range(1, len(inertias) - 1)]
    elbow_k = series[1 + second_diffs.index(max(second_diffs))]["k"] if second_diffs else None
    supported = elbow_k == clusters and decreasing
    worst_spread = max(r["restart_spread"] for r in series)
    return {
        "series": series,
        "summary": {
            "true_clusters": clusters,
            "elbow_k_detected": elbow_k,
            "points": len(points),
            "restarts_per_k": restarts,
            "inertia_at_true_k": series[clusters - 1]["inertia"],
            "inertia_monotonically_decreasing": decreasing,
            "worst_restart_spread": worst_spread,
        },
        "supported": supported,
        "conclusion": (
            f"With {restarts} restarts per k, inertia "
            + ("decreased monotonically in k, as the objective requires. "
               if decreasing else
               "did NOT decrease monotonically in k, which means some k converged to a "
               "local optimum despite the restarts. ")
            + f"The largest second difference placed the elbow at k={elbow_k} against "
            f"{clusters} generating centres — "
            + ("the elbow recovered the true number of clusters."
               if elbow_k == clusters else "the elbow did not recover the true k.")
            + f" Restart-to-restart inertia varied by up to {worst_spread:.0f}, showing how "
            f"sensitive a single-start run would have been."
        ),
    }


PROTOCOLS = [
    {
        "id": "ai.gradient_descent_lr",
        "domain": "ai systems",
        "title": "Where gradient descent converges and where it blows up",
        "question": "How does learning rate determine convergence, and does the empirical threshold match theory?",
        "hypothesis": "There is a learning-rate threshold below which training converges to the true parameters and above which it diverges.",
        "params": {
            "epochs": {"type": "int", "min": 50, "max": 1000, "default": 200,
                       "doc": "training epochs per learning rate"},
            "samples": {"type": "int", "min": 100, "max": 2000, "default": 400,
                        "doc": "training examples"},
            "seed": {"type": "int", "min": 0, "max": 999999, "default": 23, "doc": "RNG seed"},
        },
        "fn": gradient_descent_learning_rate,
    },
    {
        "id": "ai.logistic_regression",
        "domain": "ai systems",
        "title": "A logistic classifier trained from scratch, scored on held-out data",
        "question": "Can the model beat the majority-class baseline on data it has not seen?",
        "hypothesis": "Test accuracy exceeds the majority baseline by more than five points and training loss decreases.",
        "params": {
            "samples": {"type": "int", "min": 200, "max": 4000, "default": 800,
                        "doc": "total examples before the 70/30 split"},
            "epochs": {"type": "int", "min": 50, "max": 1000, "default": 300,
                       "doc": "training epochs"},
            "seed": {"type": "int", "min": 0, "max": 999999, "default": 29, "doc": "RNG seed"},
        },
        "fn": logistic_regression_training,
    },
    {
        "id": "ai.kmeans_elbow",
        "domain": "ai systems",
        "title": "Does the k-means elbow recover the true number of clusters?",
        "question": "Can an automatic elbow criterion find the generating cluster count?",
        "hypothesis": "Inertia decreases monotonically in k and the largest second difference falls at the true k.",
        "params": {
            "clusters": {"type": "int", "min": 2, "max": 8, "default": 4,
                         "doc": "true number of generating centres"},
            "points_per_cluster": {"type": "int", "min": 30, "max": 500, "default": 120,
                                   "doc": "points sampled around each centre"},
            "restarts": {"type": "int", "min": 1, "max": 40, "default": 10,
                         "doc": "random initialisations per k; the best is kept"},
            "seed": {"type": "int", "min": 0, "max": 999999, "default": 31, "doc": "RNG seed"},
        },
        "fn": kmeans_elbow,
    },
]
