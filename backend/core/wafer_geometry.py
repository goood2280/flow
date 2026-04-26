"""core/wafer_geometry.py — Wafer / shot / chip / TEG coordinate helpers.

Purpose
- Normalize multiple coordinate systems used in ET / INLINE / EDS / wafer-map analysis.
- Keep current shot-level aggregation simple while allowing future TEG/chip proximity analysis.

Current modeling assumptions
- Wafer center and reference-shot center are different physical origins.
- Shot lattice is regular: each shot has pitch/size (x, y).
- TEG representative position is the TEG lower-left corner for now.
- Chip-level detail can be added later; today we support chip position from either:
  1) shot lower-left origin = (0, 0)
  2) shot center origin     = (0, 0)

Recommended absolute coordinate system
- Use wafer-centered absolute coordinates in the same linear unit (usually mm or um).
- Every shot / chip / TEG gets:
  - abs_x, abs_y
  - radius_from_wf_center
  - angle_deg
"""
from __future__ import annotations

import math
from typing import Literal

import polars as pl

CoordMode = Literal["shot_lower_left", "shot_center"]


def shot_center_abs(
    shot_x: float,
    shot_y: float,
    *,
    ref_shot_x: float,
    ref_shot_y: float,
    ref_shot_center_x: float,
    ref_shot_center_y: float,
    shot_pitch_x: float,
    shot_pitch_y: float,
) -> tuple[float, float]:
    """Return absolute shot-center coordinate.

    `ref_shot_center_*` are already expressed in the wafer-centered absolute coordinate system.
    """
    abs_x = float(ref_shot_center_x) + (float(shot_x) - float(ref_shot_x)) * float(shot_pitch_x)
    abs_y = float(ref_shot_center_y) + (float(shot_y) - float(ref_shot_y)) * float(shot_pitch_y)
    return abs_x, abs_y


def local_to_abs(
    local_x: float,
    local_y: float,
    *,
    shot_center_x: float,
    shot_center_y: float,
    shot_size_x: float,
    shot_size_y: float,
    mode: CoordMode = "shot_lower_left",
) -> tuple[float, float]:
    """Convert shot-local coordinate to wafer-absolute coordinate.

    Modes
    - `shot_lower_left`: local (0,0) = shot lower-left corner
    - `shot_center`:     local (0,0) = shot center
    """
    if mode == "shot_center":
        return float(shot_center_x) + float(local_x), float(shot_center_y) + float(local_y)
    return (
        float(shot_center_x) - float(shot_size_x) / 2.0 + float(local_x),
        float(shot_center_y) - float(shot_size_y) / 2.0 + float(local_y),
    )


def radius_and_angle(abs_x: float, abs_y: float, *, wf_center_x: float = 0.0, wf_center_y: float = 0.0) -> tuple[float, float]:
    dx = float(abs_x) - float(wf_center_x)
    dy = float(abs_y) - float(wf_center_y)
    radius = (dx * dx + dy * dy) ** 0.5
    angle = math.degrees(math.atan2(dy, dx))
    return radius, angle


def add_shot_geometry(
    df: pl.DataFrame,
    *,
    shot_x_col: str = "shot_x",
    shot_y_col: str = "shot_y",
    ref_shot_x: float = 0.0,
    ref_shot_y: float = 0.0,
    ref_shot_center_x: float = 0.0,
    ref_shot_center_y: float = 0.0,
    shot_pitch_x: float = 1.0,
    shot_pitch_y: float = 1.0,
    wf_center_x: float = 0.0,
    wf_center_y: float = 0.0,
    out_center_x: str = "shot_center_abs_x",
    out_center_y: str = "shot_center_abs_y",
    out_radius: str = "shot_radius",
    out_angle: str = "shot_angle_deg",
) -> pl.DataFrame:
    """Add absolute shot-center geometry columns to a dataframe."""
    if shot_x_col not in df.columns or shot_y_col not in df.columns:
        return df
    out = df.with_columns([
        (
            pl.lit(float(ref_shot_center_x))
            + (pl.col(shot_x_col).cast(pl.Float64, strict=False) - float(ref_shot_x)) * float(shot_pitch_x)
        ).alias(out_center_x),
        (
            pl.lit(float(ref_shot_center_y))
            + (pl.col(shot_y_col).cast(pl.Float64, strict=False) - float(ref_shot_y)) * float(shot_pitch_y)
        ).alias(out_center_y),
    ])
    return out.with_columns([
        (
            ((pl.col(out_center_x) - float(wf_center_x)) ** 2 + (pl.col(out_center_y) - float(wf_center_y)) ** 2) ** 0.5
        ).alias(out_radius),
        (
            pl.arctan2(pl.col(out_center_y) - float(wf_center_y), pl.col(out_center_x) - float(wf_center_x)) * (180.0 / math.pi)
        ).alias(out_angle),
    ])


def add_teg_geometry(
    df: pl.DataFrame,
    *,
    shot_center_x_col: str = "shot_center_abs_x",
    shot_center_y_col: str = "shot_center_abs_y",
    teg_local_x_col: str = "teg_ll_x",
    teg_local_y_col: str = "teg_ll_y",
    shot_size_x: float = 1.0,
    shot_size_y: float = 1.0,
    coord_mode: CoordMode = "shot_lower_left",
    wf_center_x: float = 0.0,
    wf_center_y: float = 0.0,
    out_x: str = "teg_abs_x",
    out_y: str = "teg_abs_y",
    out_radius: str = "teg_radius",
) -> pl.DataFrame:
    """Add absolute representative TEG coordinate.

    Current representative point = TEG lower-left in shot-local coordinates.
    """
    if shot_center_x_col not in df.columns or shot_center_y_col not in df.columns:
        return df
    if teg_local_x_col not in df.columns or teg_local_y_col not in df.columns:
        return df
    if coord_mode == "shot_center":
        out = df.with_columns([
            (pl.col(shot_center_x_col).cast(pl.Float64, strict=False) + pl.col(teg_local_x_col).cast(pl.Float64, strict=False)).alias(out_x),
            (pl.col(shot_center_y_col).cast(pl.Float64, strict=False) + pl.col(teg_local_y_col).cast(pl.Float64, strict=False)).alias(out_y),
        ])
    else:
        out = df.with_columns([
            (
                pl.col(shot_center_x_col).cast(pl.Float64, strict=False)
                - float(shot_size_x) / 2.0
                + pl.col(teg_local_x_col).cast(pl.Float64, strict=False)
            ).alias(out_x),
            (
                pl.col(shot_center_y_col).cast(pl.Float64, strict=False)
                - float(shot_size_y) / 2.0
                + pl.col(teg_local_y_col).cast(pl.Float64, strict=False)
            ).alias(out_y),
        ])
    return out.with_columns([
        (((pl.col(out_x) - float(wf_center_x)) ** 2 + (pl.col(out_y) - float(wf_center_y)) ** 2) ** 0.5).alias(out_radius),
    ])


def add_chip_geometry(
    df: pl.DataFrame,
    *,
    shot_center_x_col: str = "shot_center_abs_x",
    shot_center_y_col: str = "shot_center_abs_y",
    chip_local_x_col: str = "chip_x",
    chip_local_y_col: str = "chip_y",
    shot_size_x: float = 1.0,
    shot_size_y: float = 1.0,
    coord_mode: CoordMode = "shot_lower_left",
    wf_center_x: float = 0.0,
    wf_center_y: float = 0.0,
    out_x: str = "chip_abs_x",
    out_y: str = "chip_abs_y",
    out_radius: str = "chip_radius",
) -> pl.DataFrame:
    """Add absolute chip representative coordinate."""
    if shot_center_x_col not in df.columns or shot_center_y_col not in df.columns:
        return df
    if chip_local_x_col not in df.columns or chip_local_y_col not in df.columns:
        return df
    if coord_mode == "shot_center":
        out = df.with_columns([
            (pl.col(shot_center_x_col).cast(pl.Float64, strict=False) + pl.col(chip_local_x_col).cast(pl.Float64, strict=False)).alias(out_x),
            (pl.col(shot_center_y_col).cast(pl.Float64, strict=False) + pl.col(chip_local_y_col).cast(pl.Float64, strict=False)).alias(out_y),
        ])
    else:
        out = df.with_columns([
            (
                pl.col(shot_center_x_col).cast(pl.Float64, strict=False)
                - float(shot_size_x) / 2.0
                + pl.col(chip_local_x_col).cast(pl.Float64, strict=False)
            ).alias(out_x),
            (
                pl.col(shot_center_y_col).cast(pl.Float64, strict=False)
                - float(shot_size_y) / 2.0
                + pl.col(chip_local_y_col).cast(pl.Float64, strict=False)
            ).alias(out_y),
        ])
    return out.with_columns([
        (((pl.col(out_x) - float(wf_center_x)) ** 2 + (pl.col(out_y) - float(wf_center_y)) ** 2) ** 0.5).alias(out_radius),
    ])


def aggregate_chip_by_shot_radius(
    df: pl.DataFrame,
    *,
    shot_x_col: str = "shot_x",
    shot_y_col: str = "shot_y",
    value_cols: list[str] | None = None,
    radius_col: str = "chip_radius",
) -> pl.DataFrame:
    """Collapse chip rows to shot rows while preserving radial context.

    Useful fallback when full chip-level proximity modeling is too heavy.
    """
    value_cols = [c for c in (value_cols or []) if c in df.columns]
    keys = [c for c in (shot_x_col, shot_y_col) if c in df.columns]
    if len(keys) < 2 or not value_cols:
        return pl.DataFrame()
    aggs = [
        pl.len().alias("chip_count"),
    ]
    if radius_col in df.columns:
        aggs.extend([
            pl.col(radius_col).mean().alias("mean_radius"),
            pl.col(radius_col).min().alias("min_radius"),
            pl.col(radius_col).max().alias("max_radius"),
        ])
    for c in value_cols:
        aggs.extend([
            pl.col(c).cast(pl.Float64, strict=False).mean().alias(f"{c}_mean"),
            pl.col(c).cast(pl.Float64, strict=False).std().alias(f"{c}_std"),
        ])
    return df.group_by(keys).agg(aggs).sort(keys)
