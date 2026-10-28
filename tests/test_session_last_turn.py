"""The relay names the turn it kept in ``x-nanomuse-request``; the hook keeps that id for the
turn's later word on it (a thumb, why it was stopped). A response that does not carry the
header -- or one the hook cannot read at all -- leaves the previous id alone and never fails
the call: the hook runs inside ``proxied_http``'s event hooks, where a raise would take down
the model call it was only meant to annotate."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx
import pytest

from nanomuse.llm import session

HEADER = "x-nanomuse-request"


@pytest.fixture(autouse=True)
def _fresh_context() -> Iterator[None]:
    session.clear_last_turn()
    yield
    session.clear_last_turn()


def _response(headers: dict[str, str] | None = None) -> httpx.Response:
    return httpx.Response(200, headers=headers or {})


def test_the_relay_id_is_kept_as_a_cloud_turn() -> None:
    session.note_response(_response({HEADER: "req-7"}))
    assert session.last_turn() == ("req-7", "cloud")


def test_a_response_without_the_header_leaves_the_last_id_alone() -> None:
    session.note_response(_response({HEADER: "req-7"}))
    session.note_response(_response())
    assert session.last_turn() == ("req-7", "cloud")


def test_an_unreadable_response_is_ignored_rather_than_raised() -> None:
    class Broken:
        @property
        def headers(self) -> Any:
            raise RuntimeError("no headers on this one")

    session.note_response(Broken())
    assert session.last_turn() == ("", "")


def test_a_long_id_is_cut_to_the_length_the_record_keeps() -> None:
    session.note_response(_response({HEADER: "a" * 200}))
    assert session.last_turn() == ("a" * 96, "cloud")


def test_the_relays_word_replaces_a_turn_of_ours() -> None:
    session.note_own_turn("0123456789abcdef")
    session.note_response(_response({HEADER: "req-9"}))
    assert session.last_turn() == ("req-9", "cloud")
