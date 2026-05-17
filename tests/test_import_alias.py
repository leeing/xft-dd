from __future__ import annotations


def test_xft_platform_imports() -> None:
    from xft.pipeline.diligence import run_company_graph
    from xft.pipeline.recommender import run_recommendation
    from xft.scoring import score_products
    from xft.web import run_web_enrichment
    from xft.warehouse import load_prophet_data

    assert callable(load_prophet_data)
    assert callable(run_web_enrichment)
    assert callable(score_products)
    assert callable(run_company_graph)
    assert callable(run_recommendation)


def test_xft_diligence_compat_imports() -> None:
    from xft.batch import run_batch
    from xft.config import load_config
    from xft.graph import run_company_graph
    from xft.nodes.init_node import make_run_id

    assert callable(load_config)
    assert callable(run_company_graph)
    assert callable(run_batch)
    assert callable(make_run_id)


