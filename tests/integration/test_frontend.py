from fastapi.testclient import TestClient

from app.main import app
from app.workers.wakeup import PostgresWakeup

client = TestClient(app)


def test_dashboard_and_static_assets_are_served() -> None:
    page = client.get("/")
    assert page.status_code == 200
    assert "动画任务观测平台" in page.text
    assert "/static/js/dashboard.js" in page.text

    assert client.get("/static/css/app.css").status_code == 200
    assert client.get("/static/js/api.js").status_code == 200

    detail = client.get("/tasks/00000000-0000-0000-0000-000000000000")
    assert detail.status_code == 200
    assert "六步骤执行流程" in detail.text
    assert "重试任务" in detail.text
    assert "取消任务" in detail.text
    assert "/static/js/task-detail.js" in detail.text


def test_task_list_is_paginated_filtered_and_uses_real_tasks() -> None:
    first = client.post("/api/tasks", json={"input": {"source": "mock://frontend-one"}})
    second = client.post("/api/tasks", json={"input": {"source": "mock://frontend-two"}})
    assert first.status_code == 201
    assert second.status_code == 201

    response = client.get("/api/tasks", params={"status": "QUEUED", "limit": 1, "offset": 0})
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2
    assert data["limit"] == 1
    assert data["offset"] == 0
    assert len(data["items"]) == 1
    assert data["items"][0]["status"] == "QUEUED"
    assert data["status_counts"]["QUEUED"] == 2


def test_task_list_rejects_unknown_status() -> None:
    response = client.get("/api/tasks", params={"status": "NOT_A_STATUS"})
    assert response.status_code == 422


def test_steps_follow_workflow_order() -> None:
    created = client.post("/api/tasks", json={"input": {"source": "mock://ordered"}})
    task_id = created.json()["id"]
    steps = client.get(f"/api/tasks/{task_id}/steps")
    assert steps.status_code == 200
    assert [step["step_key"] for step in steps.json()] == [
        "validate_asset",
        "transcode_video",
        "extract_keyframes",
        "model_inference",
        "generate_report",
        "package_result",
    ]


def test_events_support_incremental_query_and_limit() -> None:
    created = client.post("/api/tasks", json={"input": {"source": "mock://events"}})
    task_id = created.json()["id"]

    first = client.get(f"/api/tasks/{task_id}/events", params={"limit": 1})
    assert first.status_code == 200
    assert len(first.json()) == 1

    event_id = first.json()[0]["id"]
    assert (
        client.get(
            f"/api/tasks/{task_id}/events",
            params={"after_id": event_id, "limit": 10},
        ).json()
        == []
    )

    assert (
        client.get(
            f"/api/tasks/{task_id}/events",
            params={"after_id": -1},
        ).status_code
        == 422
    )


def test_task_creation_wakes_postgresql_listener() -> None:
    wakeup = PostgresWakeup()
    wakeup.open()
    try:
        created = client.post("/api/tasks", json={"input": {"source": "mock://notify"}})
        assert created.status_code == 201
        assert wakeup.wait(1.0)
    finally:
        wakeup.close()
