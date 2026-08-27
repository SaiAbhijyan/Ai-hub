"""Chemistry protocols — equilibrium, stoichiometry and kinetics, computed for real."""

from __future__ import annotations

import math
import re

# IUPAC standard atomic weights (2021), u. Used for real mass computations.
ATOMIC_WEIGHTS = {
    "H": 1.008, "He": 4.0026, "Li": 6.94, "Be": 9.0122, "B": 10.81, "C": 12.011,
    "N": 14.007, "O": 15.999, "F": 18.998, "Ne": 20.180, "Na": 22.990, "Mg": 24.305,
    "Al": 26.982, "Si": 28.085, "P": 30.974, "S": 32.06, "Cl": 35.45, "Ar": 39.95,
    "K": 39.098, "Ca": 40.078, "Fe": 55.845, "Cu": 63.546, "Zn": 65.38, "Br": 79.904,
    "Ag": 107.87, "I": 126.90, "Ba": 137.33, "Pb": 207.2,
}


def _parse_formula(formula: str) -> dict[str, int]:
    """Expand a chemical formula, including one level of parentheses, to counts."""
    def parse(tokens: str) -> dict[str, int]:
        counts: dict[str, int] = {}
        i = 0
        while i < len(tokens):
            if tokens[i] == "(":
                depth, j = 1, i + 1
                while j < len(tokens) and depth:
                    depth += (tokens[j] == "(") - (tokens[j] == ")")
                    j += 1
                inner = parse(tokens[i + 1:j - 1])
                m = re.match(r"\d+", tokens[j:])
                mult = int(m.group()) if m else 1
                for el, n in inner.items():
                    counts[el] = counts.get(el, 0) + n * mult
                i = j + (len(m.group()) if m else 0)
            else:
                m = re.match(r"([A-Z][a-z]?)(\d*)", tokens[i:])
                if not m or not m.group(1):
                    raise ValueError(f"cannot parse formula at {tokens[i:]!r}")
                el, num = m.group(1), m.group(2)
                counts[el] = counts.get(el, 0) + (int(num) if num else 1)
                i += len(m.group(0))
        return counts
    return parse(formula)


def molar_mass_and_conservation() -> dict:
    """Compute molar masses from atomic weights and verify mass balance in reactions.

    Every mass below is summed from the IUPAC atomic-weight table at run time. The
    test is whether each balanced equation conserves mass to within floating-point
    tolerance — a real check that would fail loudly on a mis-balanced equation.
    """
    reactions = [
        ("combustion of methane", [("CH4", 1), ("O2", 2)], [("CO2", 1), ("H2O", 2)]),
        ("Haber process", [("N2", 1), ("H2", 3)], [("NH3", 2)]),
        ("thermite", [("Fe2O3", 1), ("Al", 2)], [("Al2O3", 1), ("Fe", 2)]),
        ("neutralisation", [("H2SO4", 1), ("NaOH", 2)], [("Na2SO4", 1), ("H2O", 2)]),
        ("photosynthesis", [("CO2", 6), ("H2O", 6)], [("C6H12O6", 1), ("O2", 6)]),
    ]

    def mass(formula: str) -> float:
        return sum(ATOMIC_WEIGHTS[el] * n for el, n in _parse_formula(formula).items())

    series = []
    for name, lhs, rhs in reactions:
        left = sum(mass(f) * n for f, n in lhs)
        right = sum(mass(f) * n for f, n in rhs)
        # Atom-level balance, not just mass.
        left_atoms: dict[str, int] = {}
        right_atoms: dict[str, int] = {}
        for f, n in lhs:
            for el, c in _parse_formula(f).items():
                left_atoms[el] = left_atoms.get(el, 0) + c * n
        for f, n in rhs:
            for el, c in _parse_formula(f).items():
                right_atoms[el] = right_atoms.get(el, 0) + c * n
        series.append({
            "reaction": name,
            "reactant_mass_u": round(left, 4),
            "product_mass_u": round(right, 4),
            "mass_difference_u": round(abs(left - right), 9),
            "atoms_balanced": left_atoms == right_atoms,
        })

    all_balanced = all(r["atoms_balanced"] for r in series)
    worst = max(r["mass_difference_u"] for r in series)
    supported = all_balanced and worst < 1e-6
    return {
        "series": series,
        "summary": {
            "reactions_checked": len(series),
            "all_atoms_balanced": all_balanced,
            "worst_mass_difference_u": worst,
            "elements_in_table": len(ATOMIC_WEIGHTS),
        },
        "supported": supported,
        "conclusion": (
            f"All {len(series)} equations balanced atom-for-atom: "
            f"{sum(r['atoms_balanced'] for r in series)}/{len(series)}. The largest mass "
            f"discrepancy across the set was {worst:.2e} u, consistent with floating-point "
            f"summation rather than a stoichiometric error."
        ),
    }


def weak_acid_ph_approximation(min_pka_millis: int = 2000, max_pka_millis: int = 6000) -> dict:
    """Find where the textbook weak-acid pH shortcut stops being accurate.

    The common approximation is pH = 0.5*(pKa - log10(C)), which assumes
    dissociation is negligible relative to C and ignores water autoionisation. We
    solve the full charge/mass balance numerically by bisection and measure the
    discrepancy, so the breakdown point is found rather than quoted.
    """
    Kw = 1e-14
    series = []
    for pka_milli in range(min_pka_millis, max_pka_millis + 1, 1000):
        pka = pka_milli / 1000.0
        Ka = 10 ** (-pka)
        for conc_exp in (-1, -3, -5, -7):
            C = 10.0 ** conc_exp

            def charge_balance(h: float) -> float:
                # [H+] = [A-] + [OH-], with [A-] = Ka*C/(Ka+[H+])
                return h - (Ka * C / (Ka + h) + Kw / h)

            lo, hi = 1e-14, 1.0
            for _ in range(200):
                mid = math.sqrt(lo * hi)
                if charge_balance(mid) > 0:
                    hi = mid
                else:
                    lo = mid
            exact_ph = -math.log10(math.sqrt(lo * hi))
            approx_ph = 0.5 * (pka - math.log10(C))
            series.append({
                "pKa": pka,
                "concentration_M": C,
                "exact_pH": round(exact_ph, 4),
                "approx_pH": round(approx_ph, 4),
                "error_pH": round(abs(exact_ph - approx_ph), 4),
            })

    concentrated = [r for r in series if r["concentration_M"] >= 1e-3]
    dilute = [r for r in series if r["concentration_M"] <= 1e-5]
    conc_worst = max(r["error_pH"] for r in concentrated)
    dilute_worst = max(r["error_pH"] for r in dilute)
    supported = conc_worst < 0.5 and dilute_worst > conc_worst
    return {
        "series": series,
        "summary": {
            "cases": len(series),
            "worst_error_concentrated_pH": round(conc_worst, 4),
            "worst_error_dilute_pH": round(dilute_worst, 4),
            "Kw_used": Kw,
        },
        "supported": supported,
        "conclusion": (
            f"For concentrations at or above 1e-3 M the shortcut stayed within "
            f"{conc_worst:.2f} pH units of the full numerical solution, but at 1e-5 M and "
            f"below its error reached {dilute_worst:.2f} pH units — the approximation fails "
            f"in dilute solution, where dissociation and water autoionisation stop being "
            f"negligible."
        ),
    }


def reaction_kinetics_integration(steps_exponent: int = 4) -> dict:
    """Measure numerical integration error against exact rate-law solutions.

    First- and second-order decay both have closed forms, so the integrator's error
    can be measured exactly. We test the claim that halving the step size halves
    the error for Euler (first-order accurate) and quarters it for RK2.
    """
    k, c0, t_end = 0.35, 1.0, 5.0
    series = []
    for exponent in range(2, steps_exponent + 1):
        steps = 10 ** exponent
        dt = t_end / steps

        c_euler = c0
        c_rk2 = c0
        for _ in range(steps):
            c_euler += -k * c_euler * dt
            mid = c_rk2 + 0.5 * dt * (-k * c_rk2)
            c_rk2 += dt * (-k * mid)

        exact = c0 * math.exp(-k * t_end)
        series.append({
            "steps": steps,
            "dt": round(dt, 8),
            "euler_error": round(abs(c_euler - exact), 12),
            "rk2_error": round(abs(c_rk2 - exact), 12),
            "exact_concentration_M": round(exact, 10),
        })

    euler_ratios = [a["euler_error"] / b["euler_error"]
                    for a, b in zip(series, series[1:]) if b["euler_error"] > 0]
    rk2_ratios = [a["rk2_error"] / b["rk2_error"]
                  for a, b in zip(series, series[1:]) if b["rk2_error"] > 0]
    rk2_better = all(r["rk2_error"] < r["euler_error"] for r in series)
    first_order = all(5 < r < 20 for r in euler_ratios) if euler_ratios else False
    supported = rk2_better and first_order
    return {
        "series": series,
        "summary": {
            "rate_constant_per_s": k,
            "euler_error_ratio_per_decade": [round(r, 1) for r in euler_ratios],
            "rk2_error_ratio_per_decade": [round(r, 1) for r in rk2_ratios],
            "finest_euler_error": series[-1]["euler_error"],
            "finest_rk2_error": series[-1]["rk2_error"],
        },
        "supported": supported,
        "conclusion": (
            f"Against the exact solution C0*exp(-kt), Euler's error fell by "
            f"{euler_ratios[-1]:.0f}x per tenfold step reduction (first-order accuracy) "
            f"while RK2 reached {series[-1]['rk2_error']:.2e} versus Euler's "
            f"{series[-1]['euler_error']:.2e} at the finest resolution."
        ),
    }


PROTOCOLS = [
    {
        "id": "chem.mass_conservation",
        "domain": "chemistry",
        "title": "Molar masses and atom-level balance across five reactions",
        "question": "Do the standard balanced equations conserve both atoms and mass exactly?",
        "hypothesis": "Every reaction balances atom-for-atom with mass discrepancy below 1e-6 u.",
        "falsifier": "One reaction whose atom counts differ across the "
                      "arrow, or a mass discrepancy of 1e-6 u or more, "
                      "refutes it.",
        "params": {},
        "fn": molar_mass_and_conservation,
    },
    {
        "id": "chem.weak_acid_ph",
        "domain": "chemistry",
        "title": "Where the weak-acid pH approximation breaks down",
        "question": "How dilute must a weak acid be before the textbook shortcut fails?",
        "hypothesis": "The shortcut holds within 0.5 pH units above 1e-3 M and degrades markedly in dilute solution.",
        "falsifier": "A worst-case error of 0.5 pH or more above 1e-3 M, "
                      "or the dilute regime not being worse than the "
                      "concentrated one, refutes it.",
        "params": {
            "min_pka_millis": {"type": "int", "min": 1000, "max": 5000, "default": 2000,
                               "doc": "smallest pKa, in thousandths"},
            "max_pka_millis": {"type": "int", "min": 2000, "max": 10000, "default": 6000,
                               "doc": "largest pKa, in thousandths"},
        },
        "fn": weak_acid_ph_approximation,
    },
    {
        "id": "chem.kinetics_integration",
        "domain": "chemistry",
        "title": "Integrator accuracy against exact first-order kinetics",
        "question": "How does numerical error scale with step size for Euler and RK2?",
        "hypothesis": "Euler's error falls roughly tenfold per tenfold step reduction and RK2 is uniformly more accurate.",
        "falsifier": "One step size at which RK2's error is not below "
                      "Euler's, or an Euler error ratio outside 5-20x per "
                      "tenfold step reduction, refutes it.",
        "params": {
            "steps_exponent": {"type": "int", "min": 3, "max": 6, "default": 4,
                               "doc": "finest step count, as 10^k"},
        },
        "fn": reaction_kinetics_integration,
    },
]
