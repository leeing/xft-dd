from __future__ import annotations


def test_xft_platform_imports() -> None:
    from xft.pipeline.diligence import run_company_graph
    from xft.pipeline.recommender import run_recommendation
    from xft.warehouse import load_prophet_data

    assert callable(load_prophet_data)
    assert callable(run_company_graph)
    assert callable(run_recommendation)


def test_xft_diligence_real_imports() -> None:
    from xft.pipeline.diligence.batch import run_batch
    from xft.pipeline.diligence.config import load_config
    from xft.pipeline.diligence.graph import run_company_graph
    from xft.pipeline.diligence.nodes.init_node import make_run_id

    assert callable(load_config)
    assert callable(run_company_graph)
    assert callable(run_batch)
    assert callable(make_run_id)
