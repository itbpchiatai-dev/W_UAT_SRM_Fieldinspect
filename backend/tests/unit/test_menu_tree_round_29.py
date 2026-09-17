"""The sidebar hides a group that has nothing in it (round 29).

api/v1/me.py's _build_tree has always SAID it drops a permission-less parent
whose children were all filtered out, "so the sidebar doesn't show an empty
Settings parent". It never did: it decided a node "had children" by looking
at the children that SURVIVED the filter, so a group whose every child was
filtered out looked like a childless leaf and was kept. Nothing tested it.

Round 29 made that visible. The "รายงาน" group now carries no permission of
its own (each report does), so every role without a report key — Field
Officer, Supplier Staff and others — would have seen an empty "รายงาน" header
on top of the empty "การตั้งค่า" / "FarmLog" ones they already had. With the
user's decision, a group is shown only when something inside it is.
"""
from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from app.api.v1.me import _build_tree


def _item(key, *, parent=None, perm="", order=0, label=None):
    return SimpleNamespace(
        id=uuid4(), key=key, label_th=label or key, label_en=label or key, icon=None,
        path=f"/{key}", parent_id=parent.id if parent else None, order_index=order,
        required_permission_key=perm, is_system=True,
    )


def _keys(nodes) -> list:
    return [(n.key, _keys(n.children)) for n in nodes]


def _catalogue():
    dashboard = _item("dashboard", order=0)                       # a plain leaf, no perm
    farmlog = _item("farmlog", order=1)                           # a group, no perm
    plots = _item("farmlog.plots", parent=farmlog, perm="plots.read", order=1)
    reports = _item("farmlog.reports", parent=farmlog, order=2)   # a nested group, no perm
    plot_status = _item("farmlog.reports.plotstatus", parent=reports,
                        perm="reports.plot_status", order=1)
    supplier_status = _item("farmlog.reports.plotstatus.supplier", parent=reports,
                            perm="reports.plot_status_supplier", order=2)
    settings = _item("settings", order=2)                         # a group, no perm
    users = _item("settings.users", parent=settings, perm="users.read", order=1)
    return [dashboard, farmlog, plots, reports, plot_status, supplier_status, settings, users]


def test_a_group_with_nothing_visible_inside_is_hidden() -> None:
    """Supplier Staff today: no report key, no settings key."""
    tree = _build_tree(_catalogue(), allowed={"plots.read"})
    assert _keys(tree) == [
        ("dashboard", []),
        ("farmlog", [("farmlog.plots", [])]),
        # no "farmlog.reports", no "settings"
    ]


def test_a_group_is_shown_with_exactly_the_children_you_may_open() -> None:
    """A Supplier Owner sees the Supplier copy only."""
    tree = _build_tree(_catalogue(), allowed={"plots.read", "reports.plot_status_supplier"})
    assert ("farmlog", [
        ("farmlog.plots", []),
        ("farmlog.reports", [("farmlog.reports.plotstatus.supplier", [])]),
    ]) in _keys(tree)


def test_a_group_emptied_all_the_way_down_disappears_with_its_parent() -> None:
    """Nothing visible anywhere under FarmLog: neither the report group nor
    FarmLog itself should be a dead-end header."""
    tree = _build_tree(_catalogue(), allowed=set())
    assert _keys(tree) == [("dashboard", [])]


def test_a_permission_less_leaf_is_always_shown() -> None:
    """Dashboard has no children at all in the catalogue — it is a page, not
    a group, and hiding "empty groups" must never take it away."""
    tree = _build_tree(_catalogue(), allowed=set())
    assert ("dashboard", []) in _keys(tree)


def test_an_admin_with_everything_sees_everything() -> None:
    allowed = {"plots.read", "reports.plot_status", "reports.plot_status_supplier",
               "users.read"}
    tree = _build_tree(_catalogue(), allowed=allowed)
    assert _keys(tree) == [
        ("dashboard", []),
        ("farmlog", [
            ("farmlog.plots", []),
            ("farmlog.reports", [
                ("farmlog.reports.plotstatus", []),
                ("farmlog.reports.plotstatus.supplier", []),
            ]),
        ]),
        ("settings", [("settings.users", [])]),
    ]


def test_a_gated_node_you_lack_is_not_shown_even_if_it_had_children() -> None:
    """A permission on the group itself still gates the whole branch."""
    admin = _item("farmlog.admin", perm="suppliers.read")
    child = _item("farmlog.admin.plots", parent=admin, perm="plots.read")
    tree = _build_tree([admin, child], allowed={"plots.read"})
    assert _keys(tree) == []
