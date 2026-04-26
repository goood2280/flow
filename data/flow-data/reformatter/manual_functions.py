"""Custom manual() hook for flow reformatter.

Edit this file when engineers need a domain-specific index formula that is too
awkward for plain `max()/min()/abs()/...` expressions.

Expression examples:
  max({Rc_abs}, {Vth_n_abs}, {Vth_p_abs})
  manual({Rc_abs}, {Vth_n_abs}, {Vth_p_abs}, [], 20, 10)

The default contract used by core.reformatter is:
  manual(v1, v2, v3, coeffs, center, scale) -> float

where:
  - v1..vN are scalar values resolved from the current row
  - coeffs is usually a list of optional weights
  - center / scale are free parameters for engineers
"""

from __future__ import annotations


def _num(v):
    try:
        if v is None or v == "":
            return None
        return float(v)
    except Exception:
        return None


def manual(*args):
    """Example engineer-defined manual index.

    Current default:
      weighted_sum(values) -> normalized by (center, scale)

    Example:
      manual(a, b, c, [], 20, 10)
      -> ((a + b + c) - 20) / 10
    """
    list_args = [a for a in args if isinstance(a, (list, tuple))]
    scalar_args = [a for a in args if not isinstance(a, (list, tuple))]
    numeric = [_num(v) for v in scalar_args]
    numeric = [v for v in numeric if v is not None]
    if not numeric:
        return None
    if len(list_args) > 0:
        coeffs = [_num(v) for v in list_args[0]]
        coeffs = [v for v in coeffs if v is not None]
    else:
        coeffs = []
    if len(numeric) >= 3:
        center = numeric[-2]
        scale = numeric[-1] if numeric[-1] not in (None, 0) else 1.0
        values = numeric[:-2]
    else:
        center = 0.0
        scale = 1.0
        values = numeric
    if not values:
        return None
    if not coeffs:
        coeffs = [1.0] * len(values)
    if len(coeffs) < len(values):
        coeffs = coeffs + [1.0] * (len(values) - len(coeffs))
    total = sum(v * coeffs[i] for i, v in enumerate(values))
    return (total - center) / (scale or 1.0)
