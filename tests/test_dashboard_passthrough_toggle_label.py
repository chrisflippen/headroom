"""Regression test for the transformations feed's passthrough-filter toggle label."""

from __future__ import annotations

from headroom.dashboard import get_dashboard_html


def test_hide_passthrough_toggle_label_is_honest_about_what_it_filters() -> None:
    """The checkbox is bound to ``feedHideCountTokens`` and filters out any
    transformation with the ``passthrough`` flag set — which
    ``is_passthrough_model()`` sets for every ``passthrough:<endpoint>``
    model, not just ``count_tokens`` (batches, embeddings, moderations,
    audio too). The label must say "passthrough", not just "count_tokens"."""

    html = get_dashboard_html()

    assert 'x-model="feedHideCountTokens"' in html
    assert "Hide count_tokens" not in html
    assert "Hide passthrough" in html
