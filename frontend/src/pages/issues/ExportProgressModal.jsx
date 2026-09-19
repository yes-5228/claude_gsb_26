import { useEffect, useRef, useState } from 'react';

import { issueApi } from '../../api/issues.js';
import Modal from '../../components/Modal.jsx';

/**
 * 批量导出进度：轮询任务状态，展示 processed/total；
 * 成功后给下载链接；失败时展示原因（后端不会留下半份文件）。
 */
export default function ExportProgressModal({ taskId, onClose, onFinished }) {
  const [job, setJob] = useState(null);
  const [error, setError] = useState(null);
  const finishedRef = useRef(false);
  const timerRef = useRef(null);

  useEffect(() => {
    let cancelled = false;

    const poll = async () => {
      try {
        const data = await issueApi.exportStatus(taskId);
        if (cancelled) return;
        setJob(data);
        if (data.status === 'succeeded' || data.status === 'failed') {
          if (!finishedRef.current) {
            finishedRef.current = true;
            onFinished?.(data);
          }
          return;
        }
        timerRef.current = setTimeout(poll, 800);
      } catch (err) {
        if (!cancelled) setError(err.message);
      }
    };

    poll();
    return () => {
      cancelled = true;
      if (timerRef.current) clearTimeout(timerRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [taskId]);

  const status = job?.status || 'pending';
  const total = job?.total || 0;
  const processed = job?.processed || 0;
  const percent = total ? Math.min(100, Math.round((processed / total) * 100)) : 0;
  const statusText = {
    pending: '排队中…',
    running: '正在导出…',
    succeeded: '导出完成',
    failed: '导出失败',
  }[status];

  return (
    <Modal
      title="批量导出"
      onClose={onClose}
      width={480}
      footer={
        <button type="button" className="btn" onClick={onClose}>
          {status === 'succeeded' || status === 'failed' ? '关闭' : '后台执行，稍后回来'}
        </button>
      }
    >
      {error ? <div className="alert alert-error">{error}</div> : null}
      <div className="export-progress">
        <div className="export-progress-head">
          <span>{statusText}</span>
          <span>
            {status === 'running' || status === 'succeeded'
              ? `${processed} / ${total || processed} 行`
              : total
                ? `共 ${total} 行`
                : '正在统计行数…'}
          </span>
        </div>
        <div className="progress-bar">
          <div
            className={`progress-bar-inner ${status === 'failed' ? 'failed' : ''}`}
            style={{ width: `${status === 'succeeded' ? 100 : percent}%` }}
          />
        </div>
        {status === 'failed' ? (
          <div className="alert alert-error">
            导出中断：{job?.error || '未知错误'}。系统已清理临时文件，不会留下半份文件，请重试。
          </div>
        ) : null}
        {status === 'succeeded' ? (
          <div className="alert alert-success">
            文件已生成，
            <a href={issueApi.exportDownloadUrl(taskId)} download>
              点此下载 CSV
            </a>
          </div>
        ) : null}
        <p className="export-tip">
          导出在服务端异步执行，行数较多时请耐心等待；关闭弹窗不会取消任务。
        </p>
      </div>
    </Modal>
  );
}
