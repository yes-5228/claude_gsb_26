import { useEffect, useRef, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';

import { issueApi } from '../../api/issues.js';
import { restroomApi } from '../../api/restrooms.js';
import DataTable from '../../components/DataTable.jsx';
import Field from '../../components/Field.jsx';
import PageHeader from '../../components/PageHeader.jsx';
import Pagination from '../../components/Pagination.jsx';
import { OverdueTag, SeverityTag, StatusTag } from '../../components/Tags.jsx';
import { useToast } from '../../components/Toast.jsx';
import { useAsync } from '../../hooks/useAsync.js';
import { useDictionaries } from '../../hooks/useDictionaries.js';
import { useListQuery } from '../../hooks/useListQuery.js';
import { useRowSelection } from '../../hooks/useRowSelection.js';
import { formatDateTime } from '../../utils/format.js';
import { newIdempotencyKey } from '../../utils/idempotency.js';
import BatchActionModal from './BatchActionModal.jsx';
import BatchResultModal from './BatchResultModal.jsx';
import ExportProgressModal from './ExportProgressModal.jsx';
import IssueFormModal from './IssueFormModal.jsx';

const DEFAULT_FILTERS = {
  keyword: '',
  district: '',
  status: '',
  category: '',
  severity: '',
  overdue: '',
  open_only: '',
};

export default function IssueListPage() {
  const { dictionaries } = useDictionaries();
  const toast = useToast();
  const [searchParams, setSearchParams] = useSearchParams();
  const [showForm, setShowForm] = useState(false);
  const [preset, setPreset] = useState({});

  // 批量操作弹窗状态：dispatch / close / null
  const [batchType, setBatchType] = useState(null);
  const [batchSaving, setBatchSaving] = useState(false);
  const [batchResult, setBatchResult] = useState(null);
  const [exportTaskId, setExportTaskId] = useState(null);
  const [exporting, setExporting] = useState(false);
  // 每次打开批量弹窗生成一次幂等键，提交/超时重试都复用
  const idempotencyKeyRef = useRef(null);

  const list = useListQuery((params) => issueApi.list(params), DEFAULT_FILTERS, 10);
  const { data: districts } = useAsync(() => restroomApi.districts(), []);
  const selection = useRowSelection();

  // 支持从巡查记录跳转过来直接上报问题
  useEffect(() => {
    const inspectionId = searchParams.get('createFromInspection');
    if (!inspectionId) return;
    setPreset({
      defaultInspectionId: Number(inspectionId),
      defaultRestroomId: searchParams.get('restroomId'),
    });
    setShowForm(true);
    setSearchParams({}, { replace: true });
  }, [searchParams, setSearchParams]);

  const remove = async (row) => {
    if (!window.confirm(`确认删除问题「${row.title}」及其整改记录？`)) return;
    try {
      await issueApi.remove(row.id);
      toast.success('删除成功');
      selection.pruneMissing(list.items.map((item) => item.id).filter((id) => id !== row.id));
      list.reload();
    } catch (err) {
      toast.error(err.message);
    }
  };

  const openBatch = (type) => {
    if (!selection.count) {
      toast.error('请先勾选要处理的问题');
      return;
    }
    idempotencyKeyRef.current = newIdempotencyKey();
    setBatchType(type);
  };

  const submitBatch = async (form) => {
    const ids = selection.selectedIds;
    const selectedCount = ids.length;
    const payload = {
      issue_ids: ids,
      ...form,
      idempotency_key: idempotencyKeyRef.current,
    };
    setBatchSaving(true);
    try {
      const result =
        batchType === 'close'
          ? await issueApi.batchClose(payload)
          : await issueApi.batchDispatch(payload);
      // 数量硬核对：后端去重后请求数必须等于勾选数
      if (result.requested !== selectedCount) {
        toast.error(`数量不一致：勾选 ${selectedCount} 条，后端受理 ${result.requested} 条`);
      }
      setBatchResult({ result, selectedCount });
      await list.reload();
    } catch (err) {
      if (err.status === 409 && err.message.includes('处理中')) {
        toast.error('该批次正在处理中，已阻止重复提交');
      } else {
        // strict 模式整批不生效会走到这里（400），不留半成品，可修正后重试
        toast.error(err.message);
      }
    } finally {
      setBatchSaving(false);
    }
  };

  const closeBatchFlow = () => {
    setBatchResult(null);
    setBatchType(null);
    selection.clear();
  };

  const startExport = async (scope) => {
    if (scope === 'selected' && !selection.count) {
      toast.error('请先勾选要导出的问题');
      return;
    }
    setExporting(true);
    try {
      const payload =
        scope === 'selected'
          ? { scope: 'selected', issue_ids: selection.selectedIds, idempotency_key: newIdempotencyKey() }
          : {
              scope: 'all_filtered',
              ...Object.fromEntries(
                Object.entries(list.filters).filter(([, value]) => value !== ''),
              ),
              idempotency_key: newIdempotencyKey(),
            };
      const job = await issueApi.createExport(payload);
      setExportTaskId(job.task_id);
    } catch (err) {
      toast.error(err.message);
    } finally {
      setExporting(false);
    }
  };

  return (
    <>
      <PageHeader
        title="问题上报与整改跟踪"
        description="问题从上报到验收关闭的全流程跟踪，支持超期预警、批量派单 / 关闭 / 导出"
        actions={
          <button
            type="button"
            className="btn btn-primary"
            onClick={() => {
              setPreset({});
              setShowForm(true);
            }}
          >
            + 上报问题
          </button>
        }
      />
      <div className="content">
        <section className="card">
          <div className="filter-bar">
            <Field label="关键字" full>
              <input
                value={list.filters.keyword}
                placeholder="标题 / 描述 / 编号 / 责任人"
                onChange={(event) => list.updateFilter('keyword', event.target.value)}
              />
            </Field>
            <Field label="整改状态">
              <select
                value={list.filters.status}
                onChange={(event) => list.updateFilter('status', event.target.value)}
              >
                <option value="">全部</option>
                {(dictionaries?.issue_status || []).map((item) => (
                  <option key={item}>{item}</option>
                ))}
              </select>
            </Field>
            <Field label="问题分类">
              <select
                value={list.filters.category}
                onChange={(event) => list.updateFilter('category', event.target.value)}
              >
                <option value="">全部</option>
                {(dictionaries?.issue_category || []).map((item) => (
                  <option key={item}>{item}</option>
                ))}
              </select>
            </Field>
            <Field label="严重程度">
              <select
                value={list.filters.severity}
                onChange={(event) => list.updateFilter('severity', event.target.value)}
              >
                <option value="">全部</option>
                {(dictionaries?.issue_severity || []).map((item) => (
                  <option key={item}>{item}</option>
                ))}
              </select>
            </Field>
            <Field label="所属区域">
              <select
                value={list.filters.district}
                onChange={(event) => list.updateFilter('district', event.target.value)}
              >
                <option value="">全部</option>
                {(districts || []).map((item) => (
                  <option key={item}>{item}</option>
                ))}
              </select>
            </Field>
            <Field label="超期情况">
              <select
                value={list.filters.overdue}
                onChange={(event) => list.updateFilter('overdue', event.target.value)}
              >
                <option value="">全部</option>
                <option value="true">仅看超期</option>
              </select>
            </Field>
            <Field label="闭环情况">
              <select
                value={list.filters.open_only}
                onChange={(event) => list.updateFilter('open_only', event.target.value)}
              >
                <option value="">全部</option>
                <option value="true">仅看未闭环</option>
              </select>
            </Field>
            <button type="button" className="btn" onClick={list.resetFilters}>
              重置
            </button>
          </div>
        </section>

        <section className="card">
          <DataTable
            loading={list.loading}
            error={list.error}
            rows={list.items}
            emptyText="暂无问题记录"
            selectable
            selectedIds={selection.selectedIds}
            onToggleRow={(id) => {
              const row = list.items.find((item) => item.id === id);
              selection.toggleRowById(id, row);
            }}
            onTogglePage={(checked, pageKeys) =>
              selection.togglePage(checked, list.items.filter((row) => pageKeys.includes(row.id)))
            }
            columns={[
              { key: 'code', title: '编号' },
              {
                key: 'title',
                title: '问题',
                wrap: true,
                render: (row) => <Link to={`/issues/${row.id}`}>{row.title}</Link>,
              },
              {
                key: 'restroom',
                title: '公厕',
                render: (row) =>
                  row.restroom ? (
                    <Link to={`/restrooms/${row.restroom.id}`}>{row.restroom.name}</Link>
                  ) : (
                    '-'
                  ),
              },
              { key: 'category', title: '分类' },
              {
                key: 'severity',
                title: '程度',
                render: (row) => <SeverityTag severity={row.severity} />,
              },
              {
                key: 'status',
                title: '状态',
                render: (row) => (
                  <span className="inline">
                    <StatusTag status={row.status} />
                    <OverdueTag deadline={row.deadline} status={row.status} />
                  </span>
                ),
              },
              { key: 'assignee', title: '责任人' },
              { key: 'reporter', title: '上报人' },
              {
                key: 'report_time',
                title: '上报时间',
                render: (row) => formatDateTime(row.report_time),
              },
              { key: 'deadline', title: '整改期限', render: (row) => formatDateTime(row.deadline) },
              {
                key: 'actions',
                title: '操作',
                render: (row) => (
                  <div className="inline">
                    <Link className="btn-link" to={`/issues/${row.id}`}>
                      详情 / 整改
                    </Link>
                    <button type="button" className="btn-link danger" onClick={() => remove(row)}>
                      删除
                    </button>
                  </div>
                ),
              },
            ]}
          />
          <Pagination meta={list.meta} onPageChange={list.setPage} />
        </section>
      </div>

      {selection.count > 0 ? (
        <div className="batch-bar">
          <div className="batch-bar-info">
            已跨页选择 <strong>{selection.count}</strong> 条
            <button
              type="button"
              className="btn-link"
              onClick={selection.clear}
              style={{ marginLeft: 8 }}
            >
              清空选择
            </button>
          </div>
          <div className="batch-bar-actions">
            <button type="button" className="btn btn-sm" onClick={() => openBatch('dispatch')}>
              批量派单
            </button>
            <button type="button" className="btn btn-sm btn-danger" onClick={() => openBatch('close')}>
              批量关闭
            </button>
            <button
              type="button"
              className="btn btn-sm"
              disabled={exporting}
              onClick={() => startExport('selected')}
            >
              {exporting ? '创建任务中...' : '导出所选'}
            </button>
          </div>
        </div>
      ) : (
        <div className="batch-bar batch-bar-collapsed">
          <div className="batch-bar-info">未勾选时可按当前筛选条件导出</div>
          <div className="batch-bar-actions">
            <button
              type="button"
              className="btn btn-sm"
              disabled={exporting}
              onClick={() => startExport('all_filtered')}
            >
              {exporting ? '创建任务中...' : `导出筛选结果（共 ${list.meta.total} 条）`}
            </button>
          </div>
        </div>
      )}

      {showForm ? (
        <IssueFormModal {...preset} onClose={() => setShowForm(false)} onSaved={list.reload} />
      ) : null}

      {batchType && !batchResult ? (
        <BatchActionModal
          type={batchType}
          issues={selection.selectedRows}
          saving={batchSaving}
          onClose={() => setBatchType(null)}
          onSubmit={submitBatch}
        />
      ) : null}

      {batchResult ? (
        <BatchResultModal
          actionName={batchType === 'close' ? '批量关闭' : '批量派单'}
          result={batchResult.result}
          selectedCount={batchResult.selectedCount}
          replayed={batchResult.result.replayed || false}
          onClose={closeBatchFlow}
        />
      ) : null}

      {exportTaskId ? (
        <ExportProgressModal
          taskId={exportTaskId}
          onClose={() => setExportTaskId(null)}
        />
      ) : null}
    </>
  );
}
