"""Round 8-14F — level/severity/irrigation/fertilizer removed from every
seed source (no production consumer; admin UI has hidden them since round
8-14E/8-14E.1; existing rows were deleted from local/dev DB in this round).

Pure module-attribute inspection — importing these seed modules does NOT
open a DB connection or run any seeding function, so none of these tests
touch a real database, let alone reset/reseed one.
"""
from __future__ import annotations

from app.db import seed, seed_mock_farmlog, seed_reset_farmlog_full

_REMOVED_TYPES = {"level", "severity", "irrigation", "fertilizer"}
_KEPT_TYPES = {"crop", "variety", "growth_stage", "weather", "province"}


def test_seed_py_master_data_has_no_removed_types() -> None:
    assert not (_REMOVED_TYPES & seed._MASTER_DATA.keys())
    assert not (_REMOVED_TYPES & seed._MASTER_DATA_SUPPLEMENT.keys())


def test_seed_py_master_data_still_has_kept_types() -> None:
    # province lives only on the base dict; variety is tracked separately
    # via _VARIETIES/_VARIETY_SUPPLEMENT, not as a _MASTER_DATA key.
    assert _KEPT_TYPES - {"variety"} <= seed._MASTER_DATA.keys()
    assert seed._VARIETIES


def test_seed_mock_farmlog_master_data_new_has_no_removed_types() -> None:
    assert not (_REMOVED_TYPES & seed_mock_farmlog._MASTER_DATA_NEW.keys())


def test_seed_mock_farmlog_master_data_new_still_has_kept_types() -> None:
    assert {"crop", "growth_stage", "weather"} <= seed_mock_farmlog._MASTER_DATA_NEW.keys()
    assert seed_mock_farmlog._MASTER_DATA_NEW_VARIETY


def test_seed_reset_farmlog_full_master_data_has_no_removed_types() -> None:
    assert not (_REMOVED_TYPES & seed_reset_farmlog_full._MASTER_DATA.keys())


def test_seed_reset_farmlog_full_master_data_still_has_kept_types() -> None:
    assert _KEPT_TYPES - {"variety"} <= seed_reset_farmlog_full._MASTER_DATA.keys()
    assert seed_reset_farmlog_full._VARIETIES


def test_no_seed_module_source_mentions_the_removed_types_as_dict_keys() -> None:
    import inspect

    for module in (seed, seed_mock_farmlog, seed_reset_farmlog_full):
        src = inspect.getsource(module)
        for removed in _REMOVED_TYPES:
            assert f'"{removed}":' not in src, f"{module.__name__} still defines a {removed!r} entry"
