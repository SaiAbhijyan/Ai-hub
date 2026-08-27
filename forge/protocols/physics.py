"""Physics protocols — numerical experiments whose numbers are measured on the run."""

from __future__ import annotations

import math


def integrator_energy_drift(orbits: int = 8, steps_per_orbit: int = 400) -> dict:
    """Compare energy conservation of explicit Euler against velocity-Verlet.

    A unit-mass body on a circular orbit in a 1/r^2 field has constant total
    energy. Explicit Euler is not symplectic and should gain energy steadily;
    velocity-Verlet is symplectic and should keep the error bounded. We integrate
    both and measure the drift rather than assuming it.
    """
    def energy(x, y, vx, vy):
        r = math.hypot(x, y)
        return 0.5 * (vx * vx + vy * vy) - 1.0 / r

    def step_euler(x, y, vx, vy, dt):
        r = math.hypot(x, y)
        ax, ay = -x / r ** 3, -y / r ** 3
        return x + vx * dt, y + vy * dt, vx + ax * dt, vy + ay * dt

    def step_verlet(x, y, vx, vy, dt):
        r = math.hypot(x, y)
        ax, ay = -x / r ** 3, -y / r ** 3
        x += vx * dt + 0.5 * ax * dt * dt
        y += vy * dt + 0.5 * ay * dt * dt
        r2 = math.hypot(x, y)
        ax2, ay2 = -x / r2 ** 3, -y / r2 ** 3
        vx += 0.5 * (ax + ax2) * dt
        vy += 0.5 * (ay + ay2) * dt
        return x, y, vx, vy

    dt = 2 * math.pi / steps_per_orbit
    results = {}
    series = []
    for name, stepper in (("euler", step_euler), ("verlet", step_verlet)):
        x, y, vx, vy = 1.0, 0.0, 0.0, 1.0
        e0 = energy(x, y, vx, vy)
        worst = 0.0
        for orbit in range(1, orbits + 1):
            for _ in range(steps_per_orbit):
                x, y, vx, vy = stepper(x, y, vx, vy, dt)
            drift = abs((energy(x, y, vx, vy) - e0) / e0)
            worst = max(worst, drift)
            series.append({"integrator": name, "orbit": orbit,
                           "relative_energy_drift": round(drift, 8)})
        results[name] = worst

    supported = results["verlet"] < results["euler"]
    ratio = results["euler"] / results["verlet"] if results["verlet"] else float("inf")
    return {
        "series": series,
        "summary": {
            "orbits": orbits,
            "steps_per_orbit": steps_per_orbit,
            "euler_worst_drift": round(results["euler"], 8),
            "verlet_worst_drift": round(results["verlet"], 8),
            "drift_ratio_euler_over_verlet": round(ratio, 1),
        },
        "supported": supported,
        "conclusion": (
            f"After {orbits} orbits, explicit Euler's relative energy error reached "
            f"{results['euler']:.2e} while velocity-Verlet stayed at "
            f"{results['verlet']:.2e} — a factor of {ratio:.0f}."
        ),
    }


def projectile_with_drag(max_drag_millis: int = 500, speed: float = 40.0) -> dict:
    """Measure how quadratic air drag shortens projectile range.

    Launch at 45 degrees and integrate the trajectory with drag coefficients from
    zero upward. With no drag the measured range must match the analytic
    v^2 sin(2theta)/g; that agreement is the check that the integrator is sound
    before any drag conclusion is drawn.
    """
    g = 9.81
    dt = 0.0005
    theta = math.radians(45.0)
    series = []
    for milli in range(0, max_drag_millis + 1, max(max_drag_millis // 10, 1)):
        k = milli / 1000.0
        x, y = 0.0, 0.0
        vx, vy = speed * math.cos(theta), speed * math.sin(theta)
        while y >= 0.0:
            v = math.hypot(vx, vy)
            ax, ay = -k * v * vx, -g - k * v * vy
            vx, vy = vx + ax * dt, vy + ay * dt
            x, y = x + vx * dt, y + vy * dt
        series.append({"drag_coefficient": round(k, 3), "range_m": round(x, 3)})

    analytic = speed ** 2 * math.sin(2 * theta) / g
    measured_no_drag = series[0]["range_m"]
    integrator_error_pct = 100 * abs(measured_no_drag - analytic) / analytic
    monotonic = all(b["range_m"] < a["range_m"] for a, b in zip(series, series[1:]))
    supported = monotonic and integrator_error_pct < 1.0
    return {
        "series": series,
        "summary": {
            "launch_speed_m_s": speed,
            "analytic_vacuum_range_m": round(analytic, 3),
            "measured_vacuum_range_m": measured_no_drag,
            "integrator_error_pct": round(integrator_error_pct, 4),
            "range_at_max_drag_m": series[-1]["range_m"],
            "range_lost_pct": round(100 * (1 - series[-1]["range_m"] / measured_no_drag), 2),
        },
        "supported": supported,
        "conclusion": (
            f"With no drag the integrator reproduced the analytic range to "
            f"{integrator_error_pct:.3f}%. Raising the drag coefficient to "
            f"{series[-1]['drag_coefficient']} cut the range by "
            f"{100 * (1 - series[-1]['range_m'] / measured_no_drag):.1f}%, "
            + ("decreasing monotonically throughout." if monotonic
               else "but the decline was not monotonic.")
        ),
    }


def oscillator_period_vs_amplitude(max_amplitude_deg: int = 90) -> dict:
    """Test whether pendulum period depends on amplitude — small-angle vs. reality.

    The small-angle approximation predicts a period independent of amplitude. The
    exact nonlinear pendulum does not. We integrate the true equation and measure
    the period by zero-crossing, so any dependence found is measured, not assumed.
    """
    g, length = 9.81, 1.0
    small_angle_period = 2 * math.pi * math.sqrt(length / g)
    dt = 0.00002
    series = []
    for deg in range(10, max_amplitude_deg + 1, 10):
        theta0 = math.radians(deg)
        theta, omega, t = theta0, 0.0, 0.0
        prev = theta
        # Integrate to the first return through zero going the same direction:
        # a quarter period is from release to the first zero crossing.
        while True:
            alpha = -(g / length) * math.sin(theta)
            omega += alpha * dt
            theta += omega * dt
            t += dt
            if prev > 0 >= theta:
                break
            prev = theta
            if t > 10:
                break
        period = 4 * t
        series.append({
            "amplitude_deg": deg,
            "measured_period_s": round(period, 5),
            "small_angle_period_s": round(small_angle_period, 5),
            "excess_pct": round(100 * (period - small_angle_period) / small_angle_period, 3),
        })
    increasing = all(b["measured_period_s"] > a["measured_period_s"]
                     for a, b in zip(series, series[1:]))
    small_ok = abs(series[0]["excess_pct"]) < 1.0
    supported = increasing and small_ok
    return {
        "series": series,
        "summary": {
            "small_angle_period_s": round(small_angle_period, 5),
            "excess_at_10deg_pct": series[0]["excess_pct"],
            "excess_at_max_pct": series[-1]["excess_pct"],
            "max_amplitude_deg": series[-1]["amplitude_deg"],
        },
        "supported": supported,
        "conclusion": (
            f"At 10 degrees the measured period exceeded the small-angle value by only "
            f"{series[0]['excess_pct']:.2f}%, but by {series[-1]['excess_pct']:.2f}% at "
            f"{series[-1]['amplitude_deg']} degrees — the period is amplitude-dependent, "
            f"and the small-angle approximation is good only where it claims to be."
        ),
    }


PROTOCOLS = [
    {
        "id": "phys.integrator_energy",
        "domain": "physics",
        "title": "Symplectic versus non-symplectic integration: measured energy drift",
        "question": "How much total energy does each integrator gain or lose over many orbits?",
        "hypothesis": "Velocity-Verlet keeps relative energy error bounded while explicit Euler drifts without bound.",
        "falsifier": "Worst-case relative energy drift under "
                      "Velocity-Verlet coming out at or above explicit "
                      "Euler's refutes it.",
        "params": {
            "orbits": {"type": "int", "min": 2, "max": 40, "default": 8,
                       "doc": "number of orbits to integrate"},
            "steps_per_orbit": {"type": "int", "min": 100, "max": 2000, "default": 400,
                                "doc": "time resolution"},
        },
        "fn": integrator_energy_drift,
    },
    {
        "id": "phys.projectile_drag",
        "domain": "physics",
        "title": "Range of a projectile under quadratic drag",
        "question": "How much range is lost as the drag coefficient rises?",
        "hypothesis": "Range falls monotonically with drag, and the zero-drag case reproduces the analytic range to within 1%.",
        "falsifier": "Range failing to fall at any step of the drag sweep, "
                      "or the zero-drag range missing the analytic value by "
                      "1% or more, refutes it.",
        "params": {
            "max_drag_millis": {"type": "int", "min": 50, "max": 2000, "default": 500,
                                "doc": "largest drag coefficient, in thousandths"},
            "speed": {"type": "float", "min": 5.0, "max": 200.0, "default": 40.0,
                      "doc": "launch speed in m/s"},
        },
        "fn": projectile_with_drag,
    },
    {
        "id": "phys.pendulum_period",
        "domain": "physics",
        "title": "Does pendulum period depend on amplitude?",
        "question": "Where does the small-angle approximation stop being usable?",
        "hypothesis": "Measured period grows with amplitude, while agreeing with the small-angle value to under 1% at 10 degrees.",
        "falsifier": "Period failing to increase at any amplitude step, or "
                      "the smallest amplitude differing from the small-angle "
                      "formula by 1% or more, refutes it.",
        "params": {
            "max_amplitude_deg": {"type": "int", "min": 20, "max": 150, "default": 90,
                                  "doc": "largest release angle in degrees"},
        },
        "fn": oscillator_period_vs_amplitude,
    },
]
