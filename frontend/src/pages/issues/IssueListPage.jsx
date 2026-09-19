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
import { formatDateTime } from '../../utils/format.js';
import ExportMenu from './ExportMenu.jsx';
import { BatchCloseModal, BatchDispatchModal } from './IssueBatchModals.jsx';
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

function PageCheckAll({ checked, indeterminate, onChange }) {
  const ref = useRef(null);
  useEffect(() => {
    if (ref.current) ref.current.indeterminate = indeterminate;
  }, [indeterminate]);
  return (
    <input
      ref={ref}
      type="checkbox"
      checked={checked}
      onChange={onChange}
      aria-label="全选本页"
    />
  );
}

export default function IssueListPage() {
  const { dictionaries } = useDictionaries();
  const toast = useToast();
  const [searchParams, setSearchParams] = useSearchParams();
  const [showForm, setShowForm] = useState(false);
  const [preset, setPreset] = useState({});
  const [showDispatch, setShowDispatch] = useState(false);
  const [showClose, setShowClose] = useState(false);
  // 跨页勾选：key 为问题 ID，翻页不清空；筛选条件变化时清空，避免带着不可见的选择操作
  const [selected, setSelected] = useState(() => new Map());

  const list = useListQuery((params) => issueApi.list(params), DEFAULT_FILTERS, 10);
  const { data: districts } = useAsync(() => restroomApi.districts(), []);

  useEffect(() => {
    setSelected(new Map());
  }, [list.filters]);

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

  const selectedIds = [...selected.keys()];
  const pageIds = list.items.map((row) => row.id);
  const pageCheckedCount = pageIds.filter((id) => selected.has(id)).length;
  const allPageChecked = pageIds.length > 0 && pageCheckedCount === pageIds.length;

  const toggleRow = (row) => {
    setSelected((prev) => {
      const next = new Map(prev);
      if (next.has(row.id)) next.delete(row.id);
      else next.set(row.id, row);
      return next;
    });
  };

  const togglePage = () => {
    setSelected((prev) => {
      const next = new Map(prev);
      if (allPageChecked) {
        pageIds.forEach((id) => next.delete(id));
      } else {
        list.items.forEach((row) => next.set(row.id, row));
      }
      return next;
    });
  };

  const clearSelection = () => setSelected(new Map());

  const afterBatch = () => {
    setShowDispatch(false);
    setShowClose(false);
    clearSelection();
    list.reload();
  };

  const remove = async (row) => {
    if (!window.confirm(`确认删除问题「${row.title}」及其整改记录？`)) return;
    try {
      await issueApi.remove(row.id);
      toast.success('删除成功');
      list.reload();
    } catch (err) {
      toast.error(err.message);
    }
  };

  return (
    <>
      <PageHeader
        title="问题上报与整改跟踪"
        description="问题从上报到验收关闭的全流程跟踪，支持超期预警"
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
          <div className="selection-bar">
            <span className={selected.size ? 'selection-count active' : 'selection-count'}>
              已跨页勾选 <strong>{selected.size}</strong> 项
            </span>
            <div className="action-group">
              <button
                type="button"
                className="btn btn-sm btn-primary"
                disabled={!selected.size}
                onClick={() => setShowDispatch(true)}
              >
                批量派单
              </button>
              <button
                type="button"
                className="btn btn-sm btn-danger"
                disabled={!selected.size}
                onClick={() => setShowClose(true)}
              >
                批量关闭
              </button>
              <button
                type="button"
                className="btn-link"
                disabled={!selected.size}
                onClick={clearSelection}
              >
                清空选择
              </button>
            </div>
            <ExportMenu
              filters={list.filters}
              selectedIds={selectedIds}
              onError={(message) => toast.error(message)}
            />
          </div>
          <DataTable
            loading={list.loading}
            error={list.error}
            rows={list.items}
            emptyText="暂无问题记录"
            columns={[
              {
                key: '_select',
                title: (
                  <PageCheckAll
                    checked={allPageChecked}
                    indeterminate={pageCheckedCount > 0 && !allPageChecked}
                    onChange={togglePage}
                  />
                ),
                render: (row) => (
                  <input
                    type="checkbox"
                    checked={selected.has(row.id)}
                    onChange={() => toggleRow(row)}
                    aria-label={`选择 ${row.code}`}
                  />
                ),
              },
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

      {showForm ? (
        <IssueFormModal {...preset} onClose={() => setShowForm(false)} onSaved={list.reload} />
      ) : null}
      {showDispatch ? (
        <BatchDispatchModal
          issueIds={selectedIds}
          onClose={() => setShowDispatch(false)}
          onFinished={afterBatch}
        />
      ) : null}
      {showClose ? (
        <BatchCloseModal
          issueIds={selectedIds}
          onClose={() => setShowClose(false)}
          onFinished={afterBatch}
        />
      ) : null}
    </>
  );
}
