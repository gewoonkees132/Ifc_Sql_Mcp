"""The rules the viewer decides in Python, not in the page: projects, the set shown, wording."""

from datetime import UTC, datetime

import pytest

from ifc_sql_mcp.server.tools import DataQualityStatus, ModelSummary
from ifc_sql_mcp.viewer.http import ModelSet, projects, scene_hint, scene_summary, shown


def model(
    model_id: int,
    project_group: str | None,
    *,
    replaced_by: int | None = None,
    file_name: str = "house.ifc",
) -> ModelSummary:
    return ModelSummary(
        model_id=model_id,
        file_name=file_name,
        discipline="ARC",
        ifc_schema="IFC4",
        mvd=None,
        authoring_app=None,
        project_name=None,
        project_long_name=None,
        project_group=project_group,
        replaces_model_id=None,
        replaced_by_model_id=replaced_by,
        length_unit="m",
        ingested_at=datetime(2026, 11, 23, tzinfo=UTC),
        storeys=1,
        spaces=0,
        elements=1,
        property_values=0,
        quantities=0,
        materials=0,
        data_quality=DataQualityStatus(
            status="never_checked",
            dq_run_id=None,
            finished_at=None,
            errors=None,
            warnings=None,
            info=None,
        ),
    )


def test_the_summary_adds_the_three_counts_up_to_the_models_elements() -> None:
    assert scene_summary(218, 0, 50) == (
        "268 elements: 218 drawn, 0 without shape, 50 openings not drawn"
    )
    assert scene_summary(11_297, 1, 3_314) == (
        "14,612 elements: 11,297 drawn, 1 without shape, 3,314 openings not drawn"
    )


@pytest.mark.parametrize(
    ("file_name", "hint"),
    [
        ("Duplex_A.ifc", "run ifc-sql ingest Duplex_A.ifc --discipline ARC --viewer"),
        ("My House.ifc", 'run ifc-sql ingest "My House.ifc" --discipline ARC --viewer'),
        ("Skolebygg_ø.ifc", "run ifc-sql ingest Skolebygg_ø.ifc --discipline ARC --viewer"),
    ],
)
def test_the_hint_is_a_command_to_copy(file_name: str, hint: str) -> None:
    assert scene_hint(file_name, "ARC") == hint


def test_projects_group_the_current_models_newest_scene_first() -> None:
    models = [
        model(1, "Duplex"),
        model(2, "Duplex"),
        model(3, "School", replaced_by=6),
        model(4, None, file_name="loose.ifc"),
        model(5, None, file_name="other.ifc"),
        model(6, "School"),
        model(7, "Office"),
    ]

    sets = projects(models, scenes={1, 2, 6})

    assert sets == [
        # 6 has the newest scene; 3, which it replaces, is left out.
        ModelSet(label="School", project_group="School", model_ids=[6]),
        ModelSet(label="Duplex", project_group="Duplex", model_ids=[1, 2]),
        # No scene: the newest model first. Each model of no project is a set of its own.
        ModelSet(label="Office", project_group="Office", model_ids=[7]),
        ModelSet(label="other.ifc (model 5)", project_group=None, model_ids=[5]),
        ModelSet(label="loose.ifc (model 4)", project_group=None, model_ids=[4]),
    ]


def test_a_project_named_like_a_model_id_is_still_a_project() -> None:
    sets = projects([model(5, None), model(6, "5")], scenes=set())

    assert [entry.label for entry in sets] == ["5", "house.ifc (model 5)"]


def test_shown_first_is_the_first_set_by_default() -> None:
    models = [model(1, "Duplex"), model(2, "School")]
    sets = projects(models, scenes={1})

    assert shown(sets, models, project=None, model_ids=()) == sets[0]
    assert shown([], [], project=None, model_ids=()) == ModelSet(
        label="no models are loaded", project_group=None, model_ids=[]
    )


def test_shown_first_is_the_project_asked_for() -> None:
    models = [model(1, "Duplex"), model(2, "School")]
    sets = projects(models, scenes={1})

    assert shown(sets, models, project="School", model_ids=()).model_ids == [2]
    # A project whose models were deleted since view started shows nothing.
    assert shown(sets, models, project="Gone", model_ids=()) == ModelSet(
        label="Gone", project_group="Gone", model_ids=[]
    )


def test_shown_first_are_the_models_asked_for_in_their_order_once_each() -> None:
    models = [model(1, "Duplex"), model(2, "Duplex", replaced_by=3), model(3, "Duplex")]

    chosen = shown(projects(models, set()), models, project="Duplex", model_ids=[3, 2, 3, 9])

    # --model wins over --project; a replaced model may be named; a deleted one is left out.
    assert chosen == ModelSet(label="models 3, 2", project_group=None, model_ids=[3, 2])
