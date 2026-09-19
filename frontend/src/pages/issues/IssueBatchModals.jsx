import { useState } from 'react';

import { issueApi } from '../../api/issues.js';
import Field from '../../components/Field.jsx';
import Modal from '../../components/Modal.jsx';

function newBatchKey() {
  return typeof crypto !== 'undefined' && crypto.randomUUID
    ? crypto.randomUUID()
    : `batch-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function ModeSelector({ mode, onChange }) {
  return (
    <Field label="执行方式" full>
      <div className="mode-options">
        <label className="mode-option">
          <input
            type="radio"
            name="batch-mode"
            checked={mode === 'atomic'}
            onChange={() => onChange('atomic')}
          />
          <span>
            <strong>整批执行</strong>：任一条目不满足条件则整批不生效，并列出全部不满足项
          </span>
        </label>
        <label className="mode-option">
          <input
            type="radio"
            name="batch-mode"
            checked={mode === 'partial'}
            onChange={() => onChange('partial')}
          />
          <span>
            <strong>部分执行</strong>：仅执行满足条件的条目，逐条列出成功与失败清单
          </span>
        </label>
      </div>
    </Field>
  );
}

function BatchResultView({ result, expectedCount }) {
  const processed = result.success_count + result.fail_count + result.skipped_count;
  const consistent = processed === expectedCount && result.unique_count === expectedCount;
  return (
    <div className="batch-result">
      {result.replayed ? (
        <div className="alert alert-info">
          该批次已提交过（批次号 {result.batch_id}），本次为重复提交的相同结果，未重复执行。
        </div>
      ) : null}
      {!consistent ? (
        <div className="alert alert-error">
          勾选数（{expectedCount}）与实际处理数（{processed}）不一致，请刷新列表后核对。
        </div>
      ) : null}
      <div className={result.fail_count ? 'alert alert-info' : 'alert alert-success'}>
        批次号 {result.batch_id}：共 {result.unique_count} 条，成功 {result.success_count} 条，失败{' '}
        {result.fail_count} 条
        {result.skipped_count ? `，跳过 ${result.skipped_count} 条` : ''}
        {result.applied ? '' : '（整批未生效）'}
      </div>
      {result.succeeded.length ? (
        <details open={result.fail_count === 0}>
          <summary>成功清单（{result.succeeded.length}）</summary>
          <ul className="result-list">
            {result.succeeded.map((item) => (
              <li key={item.issue_id}>{item.code}</li>
            ))}
          </ul>
        </details>
      ) : null}
      {result.failed.length ? (
        <details open>
          <summary>失败清单（{result.failed.length}）</summary>
          <ul className="result-list">
            {result.failed.map((item) => (
              <li key={item.issue_id}>
                {item.code || `ID ${item.issue_id}`}：{item.reason}
              </li>
            ))}
          </ul>
        </details>
      ) : null}
      {result.skipped?.length ? (
        <details open>
          <summary>未处理清单（{result.skipped.length}，因整批未生效而跳过）</summary>
          <ul className="result-list">
            {result.skipped.map((item) => (
              <li key={item.issue_id}>{item.code}</li>
            ))}
          </ul>
        </details>
      ) : null}
    </div>
  );
}

function useBatchSubmit(request) {
  // batchKey 在弹窗生命周期内保持不变：提交中禁止重复点击，
  // 网络失败后的重试也沿用同一幂等键，不会产生重复结果
  const [batchKey] = useState(newBatchKey);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const [result, setResult] = useState(null);

  const submit = async (payload) => {
    if (saving || result) return;
    setSaving(true);
    setError(null);
    try {
      const body = await request({ ...payload, client_batch_id: batchKey });
      setResult(body);
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  };

  return { saving, error, result, submit };
}

export function BatchDispatchModal({ issueIds, onClose, onFinished }) {
  const [assignee, setAssignee] = useState('');
  const [operator, setOperator] = useState('');
  const [remark, setRemark] = useState('');
  const [mode, setMode] = useState('atomic');
  const { saving, error, result, submit } = useBatchSubmit(issueApi.batchDispatch);

  const handleSubmit = (event) => {
    event.preventDefault();
    if (!assignee.trim() || !operator.trim()) return;
    submit({
      issue_ids: issueIds,
      assignee: assignee.trim(),
      operator: operator.trim(),
      remark: remark.trim() || null,
      mode,
    });
  };

  return (
    <Modal
      title={`批量派单（已选 ${issueIds.length} 条）`}
      onClose={result ? onFinished : onClose}
      footer={
        result ? (
          <button type="button" className="btn btn-primary" onClick={onFinished}>
            完成
          </button>
        ) : (
          <>
            <button type="button" className="btn" onClick={onClose}>
              取消
            </button>
            <button
              type="submit"
              form="batch-dispatch"
              className="btn btn-primary"
              disabled={saving}
            >
              {saving ? '提交中...' : `确认派单 ${issueIds.length} 条`}
            </button>
          </>
        )
      }
    >
      {result ? (
        <BatchResultView result={result} expectedCount={issueIds.length} />
      ) : (
        <>
          <div className="alert alert-info">
            将对跨页勾选的 {issueIds.length} 条「待整改」问题统一派单，逐条写入整改流水。
          </div>
          {error ? <div className="alert alert-error">{error}</div> : null}
          <form id="batch-dispatch" className="form-grid" onSubmit={handleSubmit}>
            <Field label="整改责任人 *">
              <input
                value={assignee}
                onChange={(event) => setAssignee(event.target.value)}
                placeholder="如：保洁班组张伟"
              />
            </Field>
            <Field label="操作人 *">
              <input
                value={operator}
                onChange={(event) => setOperator(event.target.value)}
                placeholder="如：调度员小王"
              />
            </Field>
            <Field label="派单说明" full>
              <textarea
                rows="2"
                value={remark}
                onChange={(event) => setRemark(event.target.value)}
                placeholder="选填，将写入每条问题的整改流水"
              />
            </Field>
            <ModeSelector mode={mode} onChange={setMode} />
          </form>
        </>
      )}
    </Modal>
  );
}

export function BatchCloseModal({ issueIds, onClose, onFinished }) {
  const [reason, setReason] = useState('');
  const [operator, setOperator] = useState('');
  const [mode, setMode] = useState('atomic');
  const { saving, error, result, submit } = useBatchSubmit(issueApi.batchClose);

  const handleSubmit = (event) => {
    event.preventDefault();
    if (!reason.trim() || !operator.trim()) return;
    submit({
      issue_ids: issueIds,
      reason: reason.trim(),
      operator: operator.trim(),
      mode,
    });
  };

  return (
    <Modal
      title={`批量关闭（已选 ${issueIds.length} 条）`}
      onClose={result ? onFinished : onClose}
      footer={
        result ? (
          <button type="button" className="btn btn-primary" onClick={onFinished}>
            完成
          </button>
        ) : (
          <>
            <button type="button" className="btn" onClick={onClose}>
              取消
            </button>
            <button
              type="submit"
              form="batch-close"
              className="btn btn-danger"
              disabled={saving}
            >
              {saving ? '提交中...' : `确认关闭 ${issueIds.length} 条`}
            </button>
          </>
        )
      }
    >
      {result ? (
        <BatchResultView result={result} expectedCount={issueIds.length} />
      ) : (
        <>
          <div className="alert alert-info">
            将以统一理由关闭勾选的 {issueIds.length} 条问题，理由会逐条写入整改流水留痕。
          </div>
          {error ? <div className="alert alert-error">{error}</div> : null}
          <form id="batch-close" className="form-grid" onSubmit={handleSubmit}>
            <Field label="统一关闭理由 *" full>
              <textarea
                rows="3"
                value={reason}
                onChange={(event) => setReason(event.target.value)}
                placeholder="必填，如：重复上报，合并到主单处理"
              />
            </Field>
            <Field label="操作人 *">
              <input
                value={operator}
                onChange={(event) => setOperator(event.target.value)}
                placeholder="如：值班长"
              />
            </Field>
            <ModeSelector mode={mode} onChange={setMode} />
          </form>
        </>
      )}
    </Modal>
  );
}
