import { useMemo, useState } from 'react';

import Field from '../../components/Field.jsx';
import Modal from '../../components/Modal.jsx';

/**
 * 批量派单 / 批量关闭弹窗。
 * type='dispatch' 时责任人必填；type='close' 时统一关闭理由必填。
 * mode：strict（默认，任一不允许则整批不生效）/ partial（逐条处理并返回成败清单）。
 */
export default function BatchActionModal({ type, issues, onClose, onSubmit, saving }) {
  const isClose = type === 'close';
  const title = isClose ? '批量关闭问题' : '批量派单';
  const [operator, setOperator] = useState('');
  const [assignee, setAssignee] = useState('');
  const [reason, setReason] = useState('');
  const [remark, setRemark] = useState('');
  const [mode, setMode] = useState('strict');
  const [error, setError] = useState(null);

  const count = issues.length;
  const statusSummary = useMemo(() => {
    const map = new Map();
    issues.forEach((item) => map.set(item.status, (map.get(item.status) || 0) + 1));
    return [...map.entries()].map(([status, n]) => `${status} ${n} 条`).join('，');
  }, [issues]);

  const submit = (event) => {
    event.preventDefault();
    if (!operator.trim()) {
      setError('请填写操作人');
      return;
    }
    if (isClose && !reason.trim()) {
      setError('批量关闭必须填写统一关闭理由');
      return;
    }
    if (!isClose && !assignee.trim()) {
      setError('请填写整改责任人');
      return;
    }
    setError(null);
    onSubmit({
      operator: operator.trim(),
      mode,
      ...(isClose
        ? { reason: reason.trim() }
        : { assignee: assignee.trim(), remark: remark.trim() || null }),
    });
  };

  return (
    <Modal
      title={title}
      onClose={onClose}
      width={560}
      footer={
        <>
          <button type="button" className="btn" onClick={onClose} disabled={saving}>
            取消
          </button>
          <button type="submit" form="batch-action" className="btn btn-primary" disabled={saving}>
            {saving ? '提交中...' : `确认处理 ${count} 条`}
          </button>
        </>
      }
    >
      <div className="alert alert-info">
        本次共选择 <strong>{count}</strong> 条问题（{statusSummary}）
      </div>
      {error ? <div className="alert alert-error">{error}</div> : null}
      <form id="batch-action" className="form-grid" onSubmit={submit}>
        <Field label="操作人 *">
          <input value={operator} onChange={(e) => setOperator(e.target.value)} placeholder="如：值班长" />
        </Field>
        {isClose ? (
          <Field label="统一关闭理由 *" full>
            <textarea
              rows="3"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="该理由会逐条写入每条问题的整改流水留痕"
            />
          </Field>
        ) : (
          <>
            <Field label="统一指派责任人 *">
              <input
                value={assignee}
                onChange={(e) => setAssignee(e.target.value)}
                placeholder="如：保洁班组张伟"
              />
            </Field>
            <Field label="派单说明" full>
              <textarea
                rows="2"
                value={remark}
                onChange={(e) => setRemark(e.target.value)}
                placeholder="可选，将写入每条问题的整改流水"
              />
            </Field>
          </>
        )}
        <Field label="部分条目状态不允许时" full>
          <div className="radio-row">
            <label>
              <input
                type="radio"
                name="batch-mode"
                value="strict"
                checked={mode === 'strict'}
                onChange={(e) => setMode(e.target.value)}
              />
              整批不生效（全部回滚，推荐）
            </label>
            <label>
              <input
                type="radio"
                name="batch-mode"
                value="partial"
                checked={mode === 'partial'}
                onChange={(e) => setMode(e.target.value)}
              />
              逐条处理，完成后列出成功 / 失败清单
            </label>
          </div>
        </Field>
      </form>
    </Modal>
  );
}
