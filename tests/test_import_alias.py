from __future__ import annotations


def test_xft_platform_imports() -> None:
    from xft.pipeline.recommender import run_recommendation
    from xft.warehouse import load_prophet_data

    assert callable(load_prophet_data)
    assert callable(run_recommendation)
