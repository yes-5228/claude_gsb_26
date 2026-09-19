"""批量派单/关闭与异步导出的接口级测试。

覆盖需求约束：
- 批量关闭统一理由必填、逐条留痕；
- atomic 整批不生效 / partial 明确的成功失败清单，不存在"改了一半"；
- 跨页勾选数量与实际处理数严格对账（含重复 ID、不存在 ID）；
- client_batch_id 幂等，重复提交不产生重复结果；
- 导出走异步任务与进度，失败不留半份文件。
"""

import csv
import io
import time
import uuid
from pathlib import Path

from app.core.config import settings


def _key() -> str:
    return uuid.uuid4().hex


def _make_issue(client, restroom, title="测试问题", **overrides) -> dict:
    payload = {"restroom_id": restroom["id"], "title": title, "reporter": "测试员"}
    payload.update(overrides)
    response = client.post("/api/v1/issues", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _transition(client, issue_id, to_status, operator="值班长"):
    response = client.post(
        f"/api/v1/issues/{issue_id}/transitions",
        json={"to_status": to_status, "operator": operator},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _wait_export(client, job_id, timeout=10.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/api/v1/issues/exports/{job_id}").json()
        if job["status"] in ("success", "failed"):
            return job
        time.sleep(0.05)
    raise AssertionError(f"导出任务 {job_id} 超时未完成")


def test_batch_dispatch_atomic_success(client, restroom):
    issues = [_make_issue(client, restroom, title=f"派单-{i}") for i in range(3)]
    ids = [issue["id"] for issue in issues]

    result = client.post(
        "/api/v1/issues/batch-dispatch",
        json={
            "client_batch_id": _key(),
            "issue_ids": ids,
            "assignee": "保洁班组A",
            "operator": "调度员小王",
            "remark": "统一派单",
            "mode": "atomic",
        },
    )
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["applied"] is True
    assert body["replayed"] is False
    assert (body["requested_count"], body["unique_count"]) == (3, 3)
    assert (body["success_count"], body["fail_count"]) == (3, 0)
    assert body["success_count"] + body["fail_count"] == body["unique_count"]

    for issue_id in ids:
        detail = client.get(f"/api/v1/issues/{issue_id}").json()
        assert detail["status"] == "整改中"
        assert detail["assignee"] == "保洁班组A"
        last = detail["records"][-1]
        assert last["action"] == "批量派单"
        assert last["operator"] == "调度员小王"
        assert body["batch_id"] in last["remark"]
        assert "保洁班组A" in last["remark"]


def test_batch_dispatch_atomic_rolls_back_everything(client, restroom):
    ok1 = _make_issue(client, restroom, title="可派单1")
    ok2 = _make_issue(client, restroom, title="可派单2")
    busy = _make_issue(client, restroom, title="已在整改")
    _transition(client, busy["id"], "整改中")

    result = client.post(
        "/api/v1/issues/batch-dispatch",
        json={
            "client_batch_id": _key(),
            "issue_ids": [ok1["id"], busy["id"], ok2["id"]],
            "assignee": "保洁班组B",
            "operator": "调度员",
            "mode": "atomic",
        },
    ).json()
    assert result["applied"] is False
    assert result["success_count"] == 0
    assert result["fail_count"] == 1
    assert result["skipped_count"] == 2
    assert (
        result["success_count"] + result["fail_count"] + result["skipped_count"]
        == result["unique_count"]
    )
    assert result["failed"][0]["issue_id"] == busy["id"]
    assert "不允许派单" in result["failed"][0]["reason"]
    assert {item["issue_id"] for item in result["skipped"]} == {ok1["id"], ok2["id"]}

    # 整批不生效：其余两条必须保持原状，不能"改了一半"
    for issue in (ok1, ok2):
        detail = client.get(f"/api/v1/issues/{issue['id']}").json()
        assert detail["status"] == "待整改"
        assert detail["assignee"] == ""
        assert len(detail["records"]) == 1


def test_batch_dispatch_partial_reports_each_item(client, restroom):
    ok = _make_issue(client, restroom, title="可派单")
    closed = _make_issue(client, restroom, title="已关闭")
    _transition(client, closed["id"], "已关闭")

    result = client.post(
        "/api/v1/issues/batch-dispatch",
        json={
            "client_batch_id": _key(),
            "issue_ids": [ok["id"], closed["id"]],
            "assignee": "保洁班组C",
            "operator": "调度员",
            "mode": "partial",
        },
    ).json()
    assert result["applied"] is True
    assert (result["success_count"], result["fail_count"]) == (1, 1)
    assert result["succeeded"][0]["issue_id"] == ok["id"]
    assert result["failed"][0]["issue_id"] == closed["id"]

    detail = client.get(f"/api/v1/issues/{ok['id']}").json()
    assert detail["status"] == "整改中"
    assert detail["assignee"] == "保洁班组C"


def test_batch_close_requires_reason(client, restroom):
    issue = _make_issue(client, restroom, title="待关闭")
    base = {
        "client_batch_id": _key(),
        "issue_ids": [issue["id"]],
        "operator": "值班长",
    }
    missing = client.post("/api/v1/issues/batch-close", json=base)
    assert missing.status_code == 422
    empty = client.post("/api/v1/issues/batch-close", json={**base, "reason": ""})
    assert empty.status_code == 422


def test_batch_close_writes_reason_to_every_record(client, restroom):
    pending = _make_issue(client, restroom, title="待整改关闭")
    processing = _make_issue(client, restroom, title="整改中关闭")
    _transition(client, processing["id"], "整改中")
    done = _make_issue(client, restroom, title="已完成关闭")
    for target in ("整改中", "待验收", "已完成"):
        _transition(client, done["id"], target)

    reason = "重复上报，合并到主单处理"
    result = client.post(
        "/api/v1/issues/batch-close",
        json={
            "client_batch_id": _key(),
            "issue_ids": [pending["id"], processing["id"], done["id"]],
            "reason": reason,
            "operator": "值班长",
            "mode": "atomic",
        },
    ).json()
    assert result["applied"] is True
    assert result["success_count"] == 3

    for issue in (pending, processing, done):
        detail = client.get(f"/api/v1/issues/{issue['id']}").json()
        assert detail["status"] == "已关闭"
        assert detail["closed_at"] is not None
        last = detail["records"][-1]
        assert last["action"] == "批量关闭"
        assert reason in last["remark"]
        assert result["batch_id"] in last["remark"]


def test_batch_close_atomic_rejects_when_status_not_allowed(client, restroom):
    reviewing = _make_issue(client, restroom, title="待验收不能直接关闭")
    _transition(client, reviewing["id"], "整改中")
    _transition(client, reviewing["id"], "待验收")
    pending = _make_issue(client, restroom, title="本可关闭")

    result = client.post(
        "/api/v1/issues/batch-close",
        json={
            "client_batch_id": _key(),
            "issue_ids": [reviewing["id"], pending["id"]],
            "reason": "测试整批拒绝",
            "operator": "值班长",
            "mode": "atomic",
        },
    ).json()
    assert result["applied"] is False
    assert result["failed"][0]["issue_id"] == reviewing["id"]
    assert "不允许关闭" in result["failed"][0]["reason"]
    assert result["skipped_count"] == 1
    assert result["skipped"][0]["issue_id"] == pending["id"]

    # 整批不生效：本可关闭的那条也必须保持原状
    detail = client.get(f"/api/v1/issues/{pending['id']}").json()
    assert detail["status"] == "待整改"
    assert len(detail["records"]) == 1


def test_batch_idempotent_replay_no_duplicate_effects(client, restroom):
    issue = _make_issue(client, restroom, title="幂等派单")
    payload = {
        "client_batch_id": _key(),
        "issue_ids": [issue["id"]],
        "assignee": "保洁班组D",
        "operator": "调度员",
        "mode": "atomic",
    }
    first = client.post("/api/v1/issues/batch-dispatch", json=payload).json()
    assert first["applied"] is True

    # 同一 client_batch_id 重复提交（双击/重试/网络重发）
    second = client.post("/api/v1/issues/batch-dispatch", json=payload).json()
    assert second["replayed"] is True
    assert second["batch_id"] == first["batch_id"]
    assert second["success_count"] == 1

    # 不重复生效：整改流水里只有一条批量派单记录
    detail = client.get(f"/api/v1/issues/{issue['id']}").json()
    actions = [record["action"] for record in detail["records"]]
    assert actions.count("批量派单") == 1


def test_batch_count_reconciliation_with_duplicates_and_missing(client, restroom):
    ok1 = _make_issue(client, restroom, title="对账1")
    ok2 = _make_issue(client, restroom, title="对账2")
    missing_id = 999999
    # 跨页勾选可能带入重复 ID；不存在的 ID 必须计入失败清单
    ids = [ok1["id"], ok2["id"], ok1["id"], missing_id]

    result = client.post(
        "/api/v1/issues/batch-close",
        json={
            "client_batch_id": _key(),
            "issue_ids": ids,
            "reason": "对账测试",
            "operator": "值班长",
            "mode": "partial",
        },
    ).json()
    assert result["requested_count"] == 4
    assert result["unique_count"] == 3
    assert (
        result["success_count"] + result["fail_count"] + result["skipped_count"]
        == result["unique_count"]
    )
    assert (result["success_count"], result["fail_count"], result["skipped_count"]) == (2, 1, 0)
    assert result["failed"][0]["issue_id"] == missing_id
    assert "不存在" in result["failed"][0]["reason"]


def test_export_async_progress_and_download(client, restroom):
    for i in range(3):
        _make_issue(client, restroom, title=f"导出-{i}")
    # 干扰数据：其他公厕的问题不应出现在按公厕过滤的导出里
    other = client.post(
        "/api/v1/restrooms",
        json={"name": "导出干扰公厕", "district": "干扰区", "address": "干扰路 1 号"},
    ).json()
    _make_issue(client, other, title="不应导出")

    created = client.post(
        "/api/v1/issues/exports",
        json={"client_job_id": _key(), "restroom_id": restroom["id"]},
    )
    assert created.status_code == 201, created.text
    job = _wait_export(client, created.json()["job_id"])
    assert job["status"] == "success"
    assert job["total_rows"] == 3
    assert job["processed_rows"] == 3
    assert job["progress"] == 1.0
    assert job["download_url"]

    download = client.get(job["download_url"])
    assert download.status_code == 200
    rows = list(csv.reader(io.StringIO(download.content.decode("utf-8-sig"))))
    assert rows[0][0] == "问题编号"
    assert len(rows) == 1 + 3
    titles = {row[1] for row in rows[1:]}
    assert titles == {"导出-0", "导出-1", "导出-2"}


def test_export_selected_ids_only(client, restroom):
    keep = _make_issue(client, restroom, title="勾选导出")
    _make_issue(client, restroom, title="未勾选")

    created = client.post(
        "/api/v1/issues/exports",
        json={"client_job_id": _key(), "issue_ids": [keep["id"], keep["id"]]},
    ).json()
    job = _wait_export(client, created["job_id"])
    assert job["status"] == "success"
    assert job["total_rows"] == 1  # 重复 ID 去重

    download = client.get(job["download_url"])
    rows = list(csv.reader(io.StringIO(download.content.decode("utf-8-sig"))))
    assert len(rows) == 2
    assert rows[1][1] == "勾选导出"


def test_export_idempotent_creation(client, restroom):
    _make_issue(client, restroom, title="幂等导出")
    payload = {"client_job_id": _key()}
    first = client.post("/api/v1/issues/exports", json=payload).json()
    second = client.post("/api/v1/issues/exports", json=payload).json()
    assert first["job_id"] == second["job_id"]
    job = _wait_export(client, first["job_id"])
    assert job["status"] == "success"


def test_export_failure_leaves_no_partial_file(client, restroom, monkeypatch):
    _make_issue(client, restroom, title="导出失败")

    def boom(*args, **kwargs):
        raise RuntimeError("模拟磁盘写失败")

    monkeypatch.setattr("app.services.export_service._iter_chunks", boom)

    created = client.post("/api/v1/issues/exports", json={"client_job_id": _key()}).json()
    job = _wait_export(client, created["job_id"])
    assert job["status"] == "failed"
    assert "模拟磁盘写失败" in job["error"]

    # 中途失败不能留下半份文件：目录里既没有 .part 也没有该任务的 csv
    export_dir = Path(settings.export_dir)
    leftovers = [p.name for p in export_dir.glob("*") if created["job_id"] in p.name]
    assert leftovers == []
    assert list(export_dir.glob("*.part")) == []

    # 失败任务不允许下载
    blocked = client.get(f"/api/v1/issues/exports/{created['job_id']}/download")
    assert blocked.status_code == 409


def test_export_unknown_job(client):
    assert client.get("/api/v1/issues/exports/EX-不存在").status_code == 404
