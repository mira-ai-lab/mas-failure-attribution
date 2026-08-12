from __future__ import annotations

from pathlib import Path

import dataset.dataset as dataset_module


def test_load_tasks_applies_window_and_skip(monkeypatch) -> None:
    fake_dataset_path = Path("/tmp/fake/gaia_validation.parquet")

    monkeypatch.setattr(
        dataset_module,
        "resolve_dataset_argument",
        lambda dataset_name: fake_dataset_path,
    )
    monkeypatch.setattr(
        dataset_module,
        "load_local_dataset_rows",
        lambda dataset_path: [
            {
                "question_ID": "t1",
                "Question": "Q1",
                "Final answer": "A1",
            },
            {
                "id": "t2",
                "task": "Q2",
                "answer": "A2",
            },
            {
                "task_id": "t3",
                "question": "Q3",
                "reference_solution": "A3",
            },
        ],
    )

    tasks = dataset_module.load_tasks(
        "gaia",
        sample_offset=1,
        max_samples=2,
        skip_task_ids={"t3"},
    )

    assert len(tasks) == 1
    assert tasks[0]["task_id"] == "t2"
    assert tasks[0]["question"] == "Q2"
    assert tasks[0]["reference_solution"] == "A2"
    assert tasks[0]["data_source"] == "gaia"


def test_load_tasks_uses_resolved_path_for_normalization(monkeypatch) -> None:
    fake_dataset_path = Path("/tmp/data/gaia/metadata.parquet")

    monkeypatch.setattr(
        dataset_module,
        "resolve_dataset_argument",
        lambda dataset_name: fake_dataset_path,
    )
    monkeypatch.setattr(
        dataset_module,
        "load_local_dataset_rows",
        lambda dataset_path: [
            {
                "task_id": "gaia-1",
                "Question": "What is in file?",
                "Final answer": "cat",
                "file_name": "asset.png",
            }
        ],
    )

    tasks = dataset_module.load_tasks("gaia")

    assert len(tasks) == 1
    assert tasks[0]["file_name"] == str(fake_dataset_path.resolve().parent / "asset.png")


def test_load_tasks_empty_skip_ids_keeps_tasks(monkeypatch) -> None:
    fake_dataset_path = Path("/tmp/fake/kodcode.parquet")

    monkeypatch.setattr(
        dataset_module,
        "resolve_dataset_argument",
        lambda dataset_name: fake_dataset_path,
    )
    monkeypatch.setattr(
        dataset_module,
        "load_local_dataset_rows",
        lambda dataset_path: [
            {
                "task_id": "k1",
                "question": "Q",
                "reference_solution": "A",
            }
        ],
    )

    tasks = dataset_module.load_tasks("kodcode", skip_task_ids=set())

    assert [task["task_id"] for task in tasks] == ["k1"]
