import { useEffect, useRef, useState } from 'react';

import { issueApi } from '../../api/issues.js';
import Modal from '../../components/Modal.jsx';

const POLL_INTERVAL = 800;

function newJobKey() {
  return typeof crypto !== 'undefined' && crypto.randomUUID
    ? crypto.randomUUID()
    : `export-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

/** 去掉空字符串等无效值，避免把空筛选传给后端 */
function cleanFilters(filters) {
  return Object.fromEntries(
    Object.entries(filters).filter(([, value]) => value !== '' && value !== null && value !== undefined),
  );
}

/**
 * 导出入口：按当前筛选导出 / 导出勾选项。
 * 行数可能很多，统一走异步任务 + 轮询进度，完成后触发下载。
 */
export default function ExportMenu({ filters, selectedIds, onError }) {
  const [job, setJob] = useState(null);
  const timerRef = useRef(null);
  // 每次点击生成新的幂等键；同一次点击的轮询与重试共用一个任务
  const jobKeyRef = useRef(newJobKey());

  const stopPolling = () => {
    if (timerRef.current) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  };

  useEffect(() => stopPolling, []);

  const poll = (jobId) => {
    stopPolling();
    const tick = async () => {
      try {
        const latest = await issueApi.exportJob(jobId);
        setJob(latest);
        if (latest.status === 'success' || latest.status === 'failed') {
          if (latest.status === 'success' && latest.download_url) {
            // 完成后自动触发下载，用户也可再点按钮重新下载
            const anchor = document.createElement('a');
            anchor.href = issueApi.exportDownloadUrl(latest.job_id);
            anchor.rel = 'noopener';
            anchor.click();
          }
          return;
        }
        timerRef.current = setTimeout(tick, POLL_INTERVAL);
      } catch (err) {
        stopPolling();
        setJob(null);
        onError(err.message);
      }
    };
    timerRef.current = setTimeout(tick, POLL_INTERVAL);
  };

  const start = async (scope) => {
    if (job && (job.status === 'pending' || job.status === 'running')) return;
    try {
      const payload =
        scope === 'selected'
          ? { client_job_id: jobKeyRef.current, issue_ids: selectedIds }
          : { client_job_id: jobKeyRef.current, ...cleanFilters(filters) };
      const created = await issueApi.createExport(payload);
      jobKeyRef.current = newJobKey();
      setJob(created);
      poll(created.job_id);
    } catch (err) {
      onError(err.message);
    }
  };

  const close = () => {
    stopPolling();
    setJob(null);
  };

  const running = job && (job.status === 'pending' || job.status === 'running');
  const percent = job ? Math.round((job.progress || 0) * 100) : 0;

  return (
    <>
      <div className="action-group">
        <button type="button" className="btn btn-sm" onClick={() => start('filtered')}>
          导出筛选结果
        </button>
        <button
          type="button"
          className="btn btn-sm"
          disabled={!selectedIds.length}
          onClick={() => start('selected')}
        >
          导出选中（{selectedIds.length}）
        </button>
      </div>

      {job ? (
        <Modal title="导出进度" onClose={running ? () => {} : close} width={460}>
          {running ? (
            <div className="export-progress">
              <p>
                正在导出：已处理 {job.processed_rows} / {job.total_rows || '…'} 行（{percent}%）
              </p>
              <div className="progress-track">
                <div className="progress-fill" style={{ width: `${percent}%` }} />
              </div>
              <p className="muted">数据量较大时需要一些时间，可稍候在此查看进度。</p>
            </div>
          ) : null}
          {job.status === 'success' ? (
            <div className="export-progress">
              <div className="alert alert-success">
                导出完成，共 {job.total_rows} 行。文件已原子生成，不会存在半份文件。
              </div>
              <a
                className="btn btn-primary"
                href={issueApi.exportDownloadUrl(job.job_id)}
                download
              >
                再次下载 CSV
              </a>
            </div>
          ) : null}
          {job.status === 'failed' ? (
            <div className="export-progress">
              <div className="alert alert-error">导出失败：{job.error || '未知错误'}</div>
              <button type="button" className="btn" onClick={close}>
                关闭
              </button>
            </div>
          ) : null}
        </Modal>
      ) : null}
    </>
  );
}
