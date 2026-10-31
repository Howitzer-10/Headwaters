"""HDW-109: KV math, fit, num_ctx and weights calibration (worked example from the ticket)."""

from __future__ import annotations

import pytest

from headwaters.core.catalog.schema import Arch, ModelEntry
from headwaters.core.hardware.fit import (
    GIB,
    FitConfig,
    WeightsCalibration,
    evaluate_fit,
    kv_bytes,
    kv_bytes_per_token,
    max_ctx_tokens,
)

ARCH = Arch(n_layers=48, n_kv_heads=8, head_dim=128)


def coder14b(**kw: object) -> ModelEntry:
    base: dict[str, object] = {
        "id": "qwen-coder-14b",
        "provider": "ollama",
        "size_gb": 9.0,
        "arch": ARCH,
        "expected_tps": {"laptop_12gb": 35.0, "desktop_16gb": 50.0},
    }
    base.update(kw)
    return ModelEntry(**base)  # type: ignore[arg-type]


def test_kv_bytes_formula() -> None:
    assert kv_bytes_per_token(ARCH, "f16") == 2 * 48 * 8 * 128 * 2  # ~0.19 MB
    assert kv_bytes_per_token(ARCH, "q8_0") == 2 * 48 * 8 * 128  # ~0.1 MB
    assert kv_bytes_per_token(ARCH, "q4_0") == 2 * 48 * 8 * 128 * 0.5
    assert kv_bytes_per_token(ARCH, "weird") == kv_bytes_per_token(ARCH, "f16")
    assert kv_bytes(ARCH, 32768, "q8_0") / GIB == pytest.approx(3.0)  # ~3.2 GB decimal


def test_worked_example_laptop_budget_10_5() -> None:
    res = evaluate_fit(coder14b(), 10.5, est_tokens=1000)
    assert res.status == "fits"
    assert 12_000 <= res.max_ctx_tokens <= 14_000
    assert res.num_ctx == 8192  # largest step <= the ~12k that fit
    assert evaluate_fit(coder14b(), 10.5, est_tokens=12_000).status == "offload"


def test_worked_example_desktop_budget_14_5() -> None:
    res = evaluate_fit(coder14b(), 14.5, est_tokens=20_000)
    assert res.status == "fits"
    assert res.num_ctx is not None
    assert res.num_ctx >= 32768
    assert res.max_ctx_tokens >= 32768


def test_request_larger_than_largest_step_is_no_fit() -> None:
    res = evaluate_fit(coder14b(), 14.5, est_tokens=60_000)  # x1.2 = 72k > 64k
    assert (res.status, res.reason) == ("no_fit", "context_too_large")
    assert not res.usable


def test_headroom_factor_applies() -> None:
    # 7000 tokens x 1.2 = 8400 > 8192, so 8k is not enough on a roomy budget.
    res = evaluate_fit(coder14b(), 14.5, est_tokens=7000)
    assert res.num_ctx is not None
    assert res.num_ctx >= 16384


def test_model_ctx_limit_caps_steps() -> None:
    res = evaluate_fit(coder14b(ctx=16384), 14.5, est_tokens=1000)
    assert res.num_ctx == 16384
    assert evaluate_fit(coder14b(ctx=4096), 14.5, est_tokens=1000).status == "no_fit"


def test_offload_vs_no_fit_by_weights() -> None:
    big = evaluate_fit(coder14b(size_gb=15.0), 10.5, est_tokens=1000)
    assert (big.status, big.reason) == ("offload", "weights_exceed_budget")
    huge = evaluate_fit(coder14b(size_gb=40.0), 10.5, est_tokens=1000)
    assert (huge.status, huge.reason) == ("no_fit", "weights_too_large")
    assert evaluate_fit(coder14b(), 0.0, est_tokens=10).status == "no_fit"  # no GPU: excluded


def test_missing_catalog_data_is_no_fit() -> None:
    res = evaluate_fit(coder14b(arch=None), 10.5, est_tokens=10)
    assert (res.status, res.reason) == ("no_fit", "missing_catalog_data")


def test_kv_type_changes_capacity() -> None:
    q8 = evaluate_fit(coder14b(), 10.5, 1000, FitConfig(kv_type="q8_0"))
    f16 = evaluate_fit(coder14b(), 10.5, 1000, FitConfig(kv_type="f16"))
    assert f16.max_ctx_tokens < q8.max_ctx_tokens
    assert max_ctx_tokens(ARCH, 20.0, 10.5, FitConfig()) == 0


def test_calibration_replaces_catalog_weights() -> None:
    cal = WeightsCalibration()
    assert cal.get("qwen-coder-14b") is None
    ps_size = int(8.0 * GIB + kv_bytes(ARCH, 8192, "q8_0"))
    weights = cal.observe("qwen-coder-14b", ps_size, 8192, ARCH, "q8_0")
    assert weights == pytest.approx(8.0)
    res = evaluate_fit(coder14b(), 10.5, 1000, calibration=cal)
    assert res.weights_calibrated
    assert res.weights_gb == pytest.approx(8.0)
    assert res.max_ctx_tokens > evaluate_fit(coder14b(), 10.5, 1000).max_ctx_tokens
    assert cal.observe("x", 1, 8192, ARCH, "q8_0") is None  # size smaller than KV: bogus


def test_sysmem_flag_forces_offload() -> None:
    res = evaluate_fit(coder14b(), 14.5, 1000, sysmem_flagged=True)
    assert (res.status, res.reason) == ("offload", "sysmem_fallback_detected")
