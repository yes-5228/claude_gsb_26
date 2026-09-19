"""批量派单 / 批量关闭 / 异步导出的接口测试。

覆盖：
- 批量派单成功并写流水；状态不允许时 strict 整批不生效、partial 返回成败清单；
- 批量关闭理由必填、逐条留痕；跨状态（待整改/整改中/已完成）关闭；
- succeeded + failed 与请求数（去重后）严格相等；跨页勾选按 ID 提交；
- 幂等键：重复提交回放首次结果、不产生重复流转；
- 异步导出全流程：创建（202）→ 进度 → 成功后文件存在、行数与勾选数一致；
- 导出失败时清理临时文件，不留下半份文件；幂等导出复用同一任务。
"""

import time

from app.core.config import settings
from app.services import export_service


def _create_issue(client, restroom, **overrides):
    payload = {
        "restroom_id": restroom["id"],
        "title": "批量测试问题",
        "category": "保洁不到位",
        "severity": "一般",
        "reporter": "测试员",
    }
    payload.update(overrides)
    response = client.post("/api/v1/issues", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _transition(client, issue_id, target, operator="值班长", remark=None):
    response = client.post(
        f"/api/v1/issues/{issue_id}/transitions",
        json={"to_status": target, "operator": operator, "remark": remark},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _wait_for_export(client, task_id, timeout=10.0):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = client.get(f"/api/v1/issues/exports/{task_id}").json()
        if last["status"] in ("succeeded", "failed"):
            return last
        time.sleep(0.05)
    raise AssertionError(f"导出任务未在 {timeout}s 内结束：{last}")


def test_batch_dispatch_success_and_audit_trail(client, restroom):
    issues = [_create_issue(client, restroom, title=f"派单问题{i}") for i in range(3)]
    ids = [item["id"] for item in issues]

    result = client.post(
        "/api/v1/issues/batch/dispatch",
        json={
            "issue_ids": ids,
            "assignee": "保洁班组李娜",
            "operator": "调度员",
            "remark": "今日内完成",
            "idempotency_key": "dispatch-ok-0001",
        },
    )
    assert result.status_code == 200, result.text
    data = result.json()
    assert data["requested"] == 3
    assert data["succeeded"] == ids
    assert data["failed"] == []
    # 数量严格对上
    assert len(data["succeeded"]) + len(data["failed"]) == 3

    # 每条都变成整改中、责任人为统一指派人，并各多一条「批量派单」流水
    for issue_id in ids:
        detail = client.get(f"/api/v1/issues/{issue_id}").json()
        assert detail["status"] == "整改中"
        assert detail["assignee"] == "保洁班组李娜"
        actions = [record["remark"] for record in detail["records"]]
        assert any("批量派单给" in item for item in actions)
        assert any("今日内完成" in item for item in actions)


def test_batch_dispatch_dedupes_ids(client, restroom):
    issue = _create_issue(client, restroom)
    result = client.post(
        "/api/v1/issues/batch/dispatch",
        json={
            "issue_ids": [issue["id"], issue["id"], issue["id"]],
            "assignee": "责任人",
            "operator": "调度员",
            "idempotency_key": "dispatch-dedupe-0001",
        },
    ).json()
    # 重复 ID 只处理一次，成败合计仍等于去重后的请求数
    assert result["requested"] == 1
    assert result["succeeded"] == [issue["id"]]
    assert len(result["succeeded"]) + len(result["failed"]) == 1
    detail = client.get(f"/api/v1/issues/{issue['id']}").json()
    # 只多一条流水（上报问题 + 批量派单共 2 条）
    assert len(detail["records"]) == 2


def test_batch_dispatch_strict_rejects_whole_batch(client, restroom):
    pending = _create_issue(client, restroom, title="待整改一条")
    processing = _create_issue(client, restroom, title="已在整改一条")
    _transition(client, processing["id"], "整改中", operator="张三")

    key = "dispatch-strict-0001"
    response = client.post(
        "/api/v1/issues/batch/dispatch",
        json={
            "issue_ids": [pending["id"], processing["id"]],
            "assignee": "新责任人",
            "operator": "调度员",
            "mode": "strict",
            "idempotency_key": key,
        },
    )
    # 整批不生效
    assert response.status_code == 400
    assert "整批不生效" in response.json()["detail"]

    # 待整改的那一条也不能被改
    assert client.get(f"/api/v1/issues/{pending['id']}").json()["status"] == "待整改"
    # strict 失败后释放幂等占位，用同一键修正后可以重试成功
    retry = client.post(
        "/api/v1/issues/batch/dispatch",
        json={
            "issue_ids": [pending["id"]],
            "assignee": "新责任人",
            "operator": "调度员",
            "idempotency_key": key,
        },
    )
    assert retry.status_code == 200, retry.text
    assert retry.json()["succeeded"] == [pending["id"]]


def test_batch_dispatch_partial_lists_success_and_failure(client, restroom):
    pending = _create_issue(client, restroom)
    closed = _create_issue(client, restroom)
    _transition(client, closed["id"], "整改中", operator="张三")
    _transition(client, closed["id"], "待验收", operator="张三")
    _transition(client, closed["id"], "已完成", operator="王巡查")
    _transition(client, closed["id"], "已关闭", operator="值班长")

    missing_id = 9_999_999

    response = client.post(
        "/api/v1/issues/batch/dispatch",
        json={
            "issue_ids": [pending["id"], closed["id"], missing_id],
            "assignee": "保洁班组赵六",
            "operator": "调度员",
            "mode": "partial",
            "idempotency_key": "dispatch-partial-0001",
        },
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["requested"] == 3
    assert data["succeeded"] == [pending["id"]]
    failed_map = {item["id"]: item["reason"] for item in data["failed"]}
    assert set(failed_map) == {closed["id"], missing_id}
    assert "不允许" in failed_map[closed["id"]]
    assert "不存在" in failed_map[missing_id]
    # 严格计数：成功 + 失败 = 请求数
    assert len(data["succeeded"]) + len(data["failed"]) == 3


def test_batch_close_requires_reason_and_writes_trail(client, restroom):
    issues = [_create_issue(client, restroom, title=f"关闭问题{i}") for i in range(2)]
    ids = [item["id"] for item in issues]

    # 理由必填（空白也不行）
    bad = client.post(
        "/api/v1/issues/batch/close",
        json={"issue_ids": ids, "reason": "   ", "operator": "值班长"},
    )
    assert bad.status_code in (400, 422)

    response = client.post(
        "/api/v1/issues/batch/close",
        json={
            "issue_ids": ids,
            "reason": "现场核实问题不成立，统一作废",
            "operator": "值班长",
            "idempotency_key": "close-ok-0001",
        },
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["succeeded"] == ids
    assert len(data["succeeded"]) + len(data["failed"]) == 2

    for issue_id in ids:
        detail = client.get(f"/api/v1/issues/{issue_id}").json()
        assert detail["status"] == "已关闭"
        assert detail["closed_at"] is not None
        last = detail["records"][-1]
        assert last["to_status"] == "已关闭"
        assert last["remark"] == "关闭理由：现场核实问题不成立，统一作废"
        assert last["operator"] == "值班长"


def test_batch_close_mixed_statuses_and_partial(client, restroom):
    pending = _create_issue(client, restroom)
    processing = _create_issue(client, restroom)
    _transition(client, processing["id"], "整改中", operator="张三")
    done = _create_issue(client, restroom)
    _transition(client, done["id"], "整改中", operator="张三")
    _transition(client, done["id"], "待验收", operator="张三")
    _transition(client, done["id"], "已完成", operator="王巡查")
    reviewing = _create_issue(client, restroom)
    _transition(client, reviewing["id"], "整改中", operator="张三")
    _transition(client, reviewing["id"], "待验收", operator="张三")

    # 待验收不允许关闭：strict 整批不生效
    strict = client.post(
        "/api/v1/issues/batch/close",
        json={
            "issue_ids": [pending["id"], processing["id"], done["id"], reviewing["id"]],
            "reason": "统一关闭理由 X",
            "operator": "值班长",
            "mode": "strict",
            "idempotency_key": "close-mixed-strict-0001",
        },
    )
    assert strict.status_code == 400
    for issue_id in (pending["id"], processing["id"], done["id"]):
        assert client.get(f"/api/v1/issues/{issue_id}").json()["status"] != "已关闭"

    # partial：三种合法状态关闭成功，待验收列入失败
    partial = client.post(
        "/api/v1/issues/batch/close",
        json={
            "issue_ids": [pending["id"], processing["id"], done["id"], reviewing["id"]],
            "reason": "统一关闭理由 X",
            "operator": "值班长",
            "mode": "partial",
            "idempotency_key": "close-mixed-partial-0001",
        },
    )
    data = partial.json()
    assert data["requested"] == 4
    assert sorted(data["succeeded"]) == sorted([pending["id"], processing["id"], done["id"]])
    assert [item["id"] for item in data["failed"]] == [reviewing["id"]]
    assert len(data["succeeded"]) + len(data["failed"]) == 4


def test_batch_idempotency_replays_without_duplicate_transitions(client, restroom):
    issue = _create_issue(client, restroom)
    payload = {
        "issue_ids": [issue["id"]],
        "assignee": "唯一责任人",
        "operator": "调度员",
        "idempotency_key": "dispatch-idem-0001",
    }
    first = client.post("/api/v1/issues/batch/dispatch", json=payload)
    second = client.post("/api/v1/issues/batch/dispatch", json=payload)
    assert first.status_code == 200 and second.status_code == 200
    assert second.json() == first.json()

    detail = client.get(f"/api/v1/issues/{issue['id']}").json()
    # 只有 上报 + 一次批量派单 两条流水，重复提交没有产生重复结果
    assert len(detail["records"]) == 2

    # 关闭也一样
    close_payload = {
        "issue_ids": [issue["id"]],
        "reason": "重复提交测试",
        "operator": "值班长",
        "idempotency_key": "close-idem-0001",
    }
    c1 = client.post("/api/v1/issues/batch/close", json=close_payload)
    c2 = client.post("/api/v1/issues/batch/close", json=close_payload)
    assert c1.status_code == 200 and c2.json() == c1.json()
    detail = client.get(f"/api/v1/issues/{issue['id']}").json()
    assert len(detail["records"]) == 3
    assert [record["to_status"] for record in detail["records"]][-1] == "已关闭"

    # 已关闭后再用「新批次」派单必然失败
    again = client.post(
        "/api/v1/issues/batch/dispatch",
        json={
            "issue_ids": [issue["id"]],
            "assignee": "另一个人",
            "operator": "调度员",
            "idempotency_key": "dispatch-after-close-0001",
        },
    )
    assert again.status_code == 400
    detail = client.get(f"/api/v1/issues/{issue['id']}").json()
    assert detail["assignee"] == "唯一责任人"


def test_export_selected_rows_full_lifecycle(client, restroom, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "export_dir", str(tmp_path))
    issues = [_create_issue(client, restroom, title=f"导出问题{i}") for i in range(5)]
    ids = [item["id"] for item in issues]

    created = client.post(
        "/api/v1/issues/exports",
        json={"scope": "selected", "issue_ids": ids, "idempotency_key": "export-ok-0001"},
    )
    assert created.status_code == 202, created.text
    job = created.json()
    assert job["total"] == 5
    assert job["download_url"] is None

    final = _wait_for_export(client, job["task_id"])
    assert final["status"] == "succeeded"
    assert final["processed"] == 5
    assert final["total"] == 5
    assert final["download_url"].endswith("/download")

    # 成功后才可下载，文件行数 = 数据行 + 表头
    download = client.get(final["download_url"])
    assert download.status_code == 200
    lines = download.text.strip().splitlines()
    assert len(lines) == 5 + 1
    assert "问题编号" in lines[0]
    codes = {item["code"] for item in issues}
    assert codes.issubset({line.split(",")[0] for line in lines[1:]})

    # 磁盘上只有成品文件，没有 .part 半成品
    files = list(tmp_path.iterdir())
    assert len(files) == 1
    assert files[0].name.endswith(".csv")


def test_export_all_filtered_and_idempotent_replay(client, restroom, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "export_dir", str(tmp_path))
    for i in range(3):
        _create_issue(client, restroom, title=f"筛选导出{i}", category="设施损坏")
    _create_issue(client, restroom, title="别的分类", category="其他")

    key = "export-filtered-0001"
    first = client.post(
        "/api/v1/issues/exports",
        json={"scope": "all_filtered", "category": "设施损坏", "idempotency_key": key},
    )
    assert first.status_code == 202
    task_id = first.json()["task_id"]
    final = _wait_for_export(client, task_id)
    assert final["status"] == "succeeded"
    assert final["processed"] == 3

    # 同一批次重复提交：复用既有任务，不产生第二个任务/第二份文件
    replay = client.post(
        "/api/v1/issues/exports",
        json={"scope": "all_filtered", "category": "设施损坏", "idempotency_key": key},
    )
    assert replay.status_code == 202
    assert replay.json()["task_id"] == task_id
    assert replay.json()["replayed"] is True

    csv_files = list(tmp_path.glob("*.csv"))
    assert len(csv_files) == 1


def test_export_failure_leaves_no_partial_file(client, restroom, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "export_dir", str(tmp_path))
    issue = _create_issue(client, restroom)

    def boom(*args, **kwargs):
        raise RuntimeError("模拟写盘失败")

    # 在写表头阶段让其失败
    monkeypatch.setattr(export_service.csv, "writer", boom)

    created = client.post(
        "/api/v1/issues/exports",
        json={"scope": "selected", "issue_ids": [issue["id"]], "idempotency_key": "export-fail-0001"},
    )
    job = created.json()
    final = _wait_for_export(client, job["task_id"])
    assert final["status"] == "failed"
    assert "模拟写盘失败" in (final["error"] or "")

    # 失败任务不允许下载
    download = client.get(final["download_url"] or f"/api/v1/issues/exports/{job['task_id']}/download")
    assert download.status_code == 409

    # 目录里既没有成品也没有半成品
    assert list(tmp_path.iterdir()) == []
