import Modal from '../../components/Modal.jsx';

/**
 * 批量操作结果：明确列出成功和失败清单，并核对
 * 成功 + 失败 是否与本次勾选数量严格一致。
 */
export default function BatchResultModal({ actionName, result, selectedCount, replayed, onClose }) {
  const total = (result?.succeeded?.length || 0) + (result?.failed?.length || 0);
  const consistent = total === selectedCount && result?.requested === selectedCount;

  return (
    <Modal
      title={`${actionName}结果`}
      onClose={onClose}
      width={640}
      footer={
        <button type="button" className="btn btn-primary" onClick={onClose}>
          知道了
        </button>
      }
    >
      <div className={`alert ${result.failed?.length ? 'alert-error' : 'alert-success'}`}>
        {replayed ? '（重复提交已拦截，展示的是首次执行结果）' : null}
        {result.message}
      </div>
      {!consistent ? (
        <div className="alert alert-error">
          数量核对异常：成功 {result.succeeded?.length || 0} + 失败 {result.failed?.length || 0}
          = {total}，本次勾选 {selectedCount} 条（后端去重后 {result.requested} 条），
          三者不一致，请刷新列表后核对。
        </div>
      ) : null}

      <div className="batch-result-columns">
        <div>
          <h4 className="batch-result-title success">
            成功 {result.succeeded?.length || 0} 条
          </h4>
          <ul className="batch-id-list success">
            {(result.succeeded || []).map((id) => (
              <li key={id}>#{id}</li>
            ))}
          </ul>
        </div>
        <div>
          <h4 className="batch-result-title danger">失败 {result.failed?.length || 0} 条</h4>
          {result.failed?.length ? (
            <ul className="batch-fail-list">
              {result.failed.map((item) => (
                <li key={item.id}>
                  <span className="batch-fail-id">
                    {item.code ? `${item.code}（#${item.id}）` : `#${item.id}`}
                  </span>
                  <span className="batch-fail-reason">{item.reason}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="batch-no-fail">无</p>
          )}
        </div>
      </div>
    </Modal>
  );
}
