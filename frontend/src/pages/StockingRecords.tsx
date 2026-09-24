import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Plus, X, History, Ban, Wrench, Stethoscope, RefreshCw } from 'lucide-react';
import axios from 'axios';
import { stockingRecordApi, batchApi } from '../services/api';
import type {
  StockingRecord,
  Batch,
  StockingRecordRevision,
  StockingDiagnosticsReport,
} from '../types';
import {
  POLICY_VERSION,
  measureInputs,
  previewTotalWeight,
  newClientToken,
  formatWeightKg,
  MeasurementError,
} from '../utils/stockingPolicy';

type FormState = {
  batch_id: string;
  species: string;
  quantity: string;
  source: string;
  batch_number: string;
  weight_per_unit: string;
  notes: string;
  record_type: 'initial' | 'supplement';
  reason: string;
};

const EMPTY_FORM: FormState = {
  batch_id: '',
  species: '',
  quantity: '',
  source: '',
  batch_number: '',
  weight_per_unit: '',
  notes: '',
  record_type: 'initial',
  reason: '',
};

const StockingRecords: React.FC = () => {
  const [records, setRecords] = useState<StockingRecord[]>([]);
  const [batches, setBatches] = useState<Batch[]>([]);
  const [loading, setLoading] = useState(true);
  const [showModal, setShowModal] = useState(false);
  const [editingRecord, setEditingRecord] = useState<StockingRecord | null>(null);
  const [formData, setFormData] = useState<FormState>(EMPTY_FORM);
  const [formError, setFormError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const clientTokenRef = useRef<string | null>(null);
  const [historyRecord, setHistoryRecord] = useState<StockingRecord | null>(null);
  const [revisions, setRevisions] = useState<StockingRecordRevision[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [diagnostics, setDiagnostics] = useState<StockingDiagnosticsReport | null>(null);
  const [diagnosticsLoading, setDiagnosticsLoading] = useState(false);

  const fetchData = async () => {
    try {
      const [recordsRes, batchesRes] = await Promise.all([
        stockingRecordApi.getAll(),
        batchApi.getAll(),
      ]);
      setRecords(recordsRes.data);
      setBatches(batchesRes.data);
    } catch (error) {
      console.error('Error fetching data:', error);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchData();
  }, []);

  // 总重量由明细实时派生，只读展示（单位：公斤，1 克精度）
  const derivedTotal = useMemo(
    () => previewTotalWeight(formData.quantity, formData.weight_per_unit),
    [formData.quantity, formData.weight_per_unit],
  );

  const openCreate = () => {
    setEditingRecord(null);
    setFormData(EMPTY_FORM);
    setFormError(null);
    clientTokenRef.current = newClientToken();
    setShowModal(true);
  };

  const handleCorrect = (record: StockingRecord) => {
    setEditingRecord(record);
    setFormData({
      batch_id: record.batch_id.toString(),
      species: record.species,
      quantity: record.quantity.toString(),
      source: record.source || '',
      batch_number: record.batch_number || '',
      weight_per_unit: record.weight_per_unit?.toString() || '',
      notes: record.notes || '',
      record_type: record.record_type,
      reason: '',
    });
    setFormError(null);
    setShowModal(true);
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (submitting) return; // 防重复提交
    setFormError(null);

    let measured: ReturnType<typeof measureInputs>;
    try {
      measured = measureInputs({
        quantity: formData.quantity,
        weight_per_unit: formData.weight_per_unit,
      });
    } catch (err) {
      setFormError(err instanceof MeasurementError ? err.message : '录入数据不合法');
      return;
    }
    if (editingRecord && !formData.reason.trim()) {
      setFormError('更正记录必须填写原因，原值与原因将一并留痕');
      return;
    }

    const payload = {
      batch_id: parseInt(formData.batch_id, 10),
      species: formData.species.trim(),
      quantity: measured.quantity,
      source: formData.source || undefined,
      batch_number: formData.batch_number || undefined,
      weight_per_unit: measured.weightPerUnit ?? undefined,
      // 总重量不接受手填：由后端依据同一口径再次派生，前端只作展示
      notes: formData.notes || undefined,
      record_type: formData.record_type,
    };

    setSubmitting(true);
    try {
      if (editingRecord) {
        await stockingRecordApi.correct(editingRecord.id, {
          ...payload,
          reason: formData.reason.trim(),
          expected_revision: editingRecord.revision,
        });
      } else {
        await stockingRecordApi.create({
          ...payload,
          client_token: clientTokenRef.current ?? undefined,
        });
      }
      setShowModal(false);
      setEditingRecord(null);
      clientTokenRef.current = null;
      fetchData();
    } catch (err) {
      if (axios.isAxiosError(err)) {
        const detail = err.response?.data?.detail;
        setFormError(typeof detail === 'string' ? detail : '保存失败，请检查输入或刷新后重试');
      } else {
        setFormError('保存失败，请重试');
      }
    } finally {
      setSubmitting(false);
    }
  };

  const handleVoid = async (record: StockingRecord) => {
    const reason = window.prompt(
      `撤销投苗记录 #${record.id}（${record.quantity.toLocaleString()} 尾）。\n` +
        '原值将保留留痕且不再参与统计，请填写撤销原因：',
    );
    if (reason === null) return;
    if (!reason.trim()) {
      window.alert('撤销原因不能为空');
      return;
    }
    try {
      await stockingRecordApi.void(record.id, reason.trim());
      fetchData();
    } catch (err) {
      if (axios.isAxiosError(err)) {
        const detail = err.response?.data?.detail;
        window.alert(typeof detail === 'string' ? detail : '撤销失败，请刷新后重试');
      }
    }
  };

  const handleShowHistory = async (record: StockingRecord) => {
    setHistoryRecord(record);
    setHistoryLoading(true);
    setRevisions([]);
    try {
      const res = await stockingRecordApi.revisions(record.id);
      setRevisions(res.data);
    } catch (err) {
      console.error('Error fetching revisions:', err);
    } finally {
      setHistoryLoading(false);
    }
  };

  const runDiagnostics = async () => {
    setDiagnosticsLoading(true);
    try {
      const res = await stockingRecordApi.diagnostics();
      setDiagnostics(res.data);
    } finally {
      setDiagnosticsLoading(false);
    }
  };

  const runRepair = async () => {
    if (
      !window.confirm(
        '将按"尾数 × 每尾克重 ÷ 1000"重算总重量（公斤），原值与修复原因会写入修订台账。\n' +
          '该操作可重复执行且只改派生字段，是否继续？',
      )
    ) {
      return;
    }
    setDiagnosticsLoading(true);
    try {
      const res = await stockingRecordApi.repair();
      setDiagnostics(res.data);
      fetchData();
    } finally {
      setDiagnosticsLoading(false);
    }
  };

  const getBatchNumber = (batchId: number) => {
    const batch = batches.find((b) => b.id === batchId);
    return batch ? batch.batch_number : '未知批次';
  };

  const actionLabels: Record<string, string> = {
    create: '创建',
    correct: '更正',
    void: '撤销',
    repair: '修复',
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="text-gray-500">加载中...</div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">投苗记录</h1>
          <p className="text-gray-600 mt-1">
            记录水产养殖投苗信息 · 计量口径 {POLICY_VERSION}（总重量由明细派生，仅当前有效版本参与分析）
          </p>
        </div>
        <button onClick={openCreate} className="btn-primary flex items-center space-x-2">
          <Plus size={20} />
          <span>新增记录</span>
        </button>
      </div>

      <div className="card">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold text-gray-900 flex items-center space-x-2">
            <Stethoscope size={18} />
            <span>历史数据诊断与修复</span>
          </h2>
          <div className="flex items-center space-x-2">
            <button
              onClick={runDiagnostics}
              disabled={diagnosticsLoading}
              className="btn-secondary text-sm py-2 flex items-center space-x-1"
            >
              <RefreshCw size={14} className={diagnosticsLoading ? 'animate-spin' : ''} />
              <span>诊断</span>
            </button>
            <button
              onClick={runRepair}
              disabled={diagnosticsLoading}
              className="btn-secondary text-sm py-2 flex items-center space-x-1"
            >
              <Wrench size={14} />
              <span>按明细修复总重量</span>
            </button>
          </div>
        </div>
        {diagnostics && (
          <div className="text-sm space-y-2">
            <p className="text-gray-600">
              扫描 {diagnostics.scanned} 条版本记录：异常 {diagnostics.summary.issue_count} 项，
              本次修复 {diagnostics.summary.fixed_count} 项，
              需人工处理 {diagnostics.summary.needs_manual_count} 项（口径 {diagnostics.policy_version}）。
            </p>
            {diagnostics.issues.length > 0 && (
              <ul className="list-disc list-inside text-amber-700 max-h-40 overflow-y-auto">
                {diagnostics.issues.map((issue, idx) => (
                  <li key={idx}>
                    记录 #{issue.record_id}（批次 {issue.batch_id}）[{issue.code}] {issue.message}
                  </li>
                ))}
              </ul>
            )}
            {diagnostics.needs_manual.length > 0 && (
              <p className="text-red-600">负数/非有限数/超范围等问题无法自动修复，需人工核实后通过"更正"处理。</p>
            )}
            {diagnostics.summary.clean && <p className="text-green-600">数据与口径一致，无需处理。</p>}
          </div>
        )}
      </div>

      <div className="card">
        <div className="overflow-x-auto">
          <table className="table">
            <thead>
              <tr>
                <th>批次号</th>
                <th>类型</th>
                <th>品种</th>
                <th>数量(尾)</th>
                <th>单重(克/尾)</th>
                <th>总重量(公斤)</th>
                <th>版本</th>
                <th>来源</th>
                <th>苗种批次</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {records.map((record) => (
                <tr key={record.id}>
                  <td className="font-medium text-ocean-700">{getBatchNumber(record.batch_id)}</td>
                  <td>
                    <span className={`badge ${record.record_type === 'supplement' ? 'badge-warning' : 'badge-success'}`}>
                      {record.record_type === 'supplement' ? '分批补苗' : '首次投苗'}
                    </span>
                  </td>
                  <td>{record.species}</td>
                  <td>{record.quantity.toLocaleString()}</td>
                  <td>{record.weight_per_unit != null ? record.weight_per_unit.toFixed(2) : '-'}</td>
                  <td>{formatWeightKg(record.total_weight)}</td>
                  <td className="text-xs text-gray-500">v{record.revision}</td>
                  <td>{record.source || '-'}</td>
                  <td>{record.batch_number || '-'}</td>
                  <td>
                    <div className="flex items-center space-x-2">
                      <button
                        title="更正（旧值留痕，生成新版本）"
                        onClick={() => handleCorrect(record)}
                        className="p-2 text-ocean-600 hover:bg-ocean-50 rounded-lg transition-colors"
                      >
                        <RefreshCw size={18} />
                      </button>
                      <button
                        title="撤销（软删，需填原因）"
                        onClick={() => handleVoid(record)}
                        className="p-2 text-amber-600 hover:bg-amber-50 rounded-lg transition-colors"
                      >
                        <Ban size={18} />
                      </button>
                      <button
                        title="修订历史"
                        onClick={() => handleShowHistory(record)}
                        className="p-2 text-gray-600 hover:bg-gray-100 rounded-lg transition-colors"
                      >
                        <History size={18} />
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
              {records.length === 0 && (
                <tr>
                  <td colSpan={10} className="text-center py-8 text-gray-500">
                    暂无投苗记录
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {showModal && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
          <div className="bg-white rounded-xl p-6 w-full max-w-lg mx-4 max-h-[90vh] overflow-y-auto">
            <div className="flex items-center justify-between mb-6">
              <h2 className="text-xl font-bold text-gray-900">
                {editingRecord ? `更正投苗记录 #${editingRecord.id}（当前 v${editingRecord.revision}）` : '新增投苗记录'}
              </h2>
              <button onClick={() => setShowModal(false)} className="p-2 text-gray-400 hover:text-gray-600">
                <X size={20} />
              </button>
            </div>

            {editingRecord && (
              <div className="mb-4 p-3 bg-amber-50 border border-amber-200 rounded-lg text-sm text-amber-800">
                更正不会覆盖原值：当前版本将标记为"已被替代"并完整留痕，分析自动改用新版本。
              </div>
            )}

            <form onSubmit={handleSubmit} className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  养殖批次 <span className="text-red-500">*</span>
                </label>
                <select
                  required
                  value={formData.batch_id}
                  onChange={(e) => setFormData({ ...formData, batch_id: e.target.value })}
                  className="select-field"
                >
                  <option value="">请选择批次</option>
                  {batches.map((batch) => (
                    <option key={batch.id} value={batch.id}>
                      {batch.batch_number} - {batch.species}
                    </option>
                  ))}
                </select>
              </div>

              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">
                    品种 <span className="text-red-500">*</span>
                  </label>
                  <input
                    type="text"
                    required
                    value={formData.species}
                    onChange={(e) => setFormData({ ...formData, species: e.target.value })}
                    className="input-field"
                    placeholder="如: 草鱼"
                  />
                </div>

                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">
                    投苗类型
                  </label>
                  <select
                    value={formData.record_type}
                    onChange={(e) =>
                      setFormData({ ...formData, record_type: e.target.value as FormState['record_type'] })
                    }
                    className="select-field"
                  >
                    <option value="initial">首次投苗</option>
                    <option value="supplement">分批补苗</option>
                  </select>
                </div>
              </div>

              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">
                    数量(尾) <span className="text-red-500">*</span>
                  </label>
                  <input
                    type="number"
                    min={1}
                    step={1}
                    required
                    value={formData.quantity}
                    onChange={(e) => setFormData({ ...formData, quantity: e.target.value })}
                    className="input-field"
                    placeholder="正整数尾数"
                  />
                </div>

                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">
                    单重(克/尾) <span className="text-red-500">*</span>
                  </label>
                  <input
                    type="number"
                    min={0.01}
                    max={10000}
                    step={0.01}
                    required
                    value={formData.weight_per_unit}
                    onChange={(e) => setFormData({ ...formData, weight_per_unit: e.target.value })}
                    className="input-field"
                    placeholder="0.01 ~ 10000 克"
                  />
                </div>
              </div>

              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">
                    来源
                  </label>
                  <input
                    type="text"
                    value={formData.source}
                    onChange={(e) => setFormData({ ...formData, source: e.target.value })}
                    className="input-field"
                    placeholder="苗种来源"
                  />
                </div>

                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">
                    苗种批次号
                  </label>
                  <input
                    type="text"
                    value={formData.batch_number}
                    onChange={(e) => setFormData({ ...formData, batch_number: e.target.value })}
                    className="input-field"
                    placeholder="苗种批次号"
                  />
                </div>
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  总重量(公斤) <span className="text-gray-400 font-normal">· 由明细派生（1克精度，不可手填）</span>
                </label>
                <input
                  type="number"
                  readOnly
                  value={derivedTotal === null ? '' : derivedTotal.toFixed(3)}
                  className="input-field bg-gray-50 text-gray-700"
                  placeholder="填写尾数与单重后自动计算"
                />
                <p className="text-xs text-gray-500 mt-1">
                  总重量(公斤) = 尾数 × 单重(克/尾) ÷ 1000，四舍五入到 0.001 公斤
                </p>
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">备注</label>
                <textarea
                  value={formData.notes}
                  onChange={(e) => setFormData({ ...formData, notes: e.target.value })}
                  className="input-field"
                  rows={2}
                  placeholder="备注信息"
                />
              </div>

              {editingRecord && (
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">
                    更正原因 <span className="text-red-500">*</span>
                  </label>
                  <textarea
                    required
                    value={formData.reason}
                    onChange={(e) => setFormData({ ...formData, reason: e.target.value })}
                    className="input-field"
                    rows={2}
                    placeholder="如：采购单复核，单重由 0.5 克更正为 0.6 克"
                  />
                </div>
              )}

              {formError && (
                <div className="p-3 bg-red-100 text-red-700 rounded-lg text-sm whitespace-pre-line">
                  {formError}
                </div>
              )}

              <div className="flex justify-end space-x-3 pt-4">
                <button type="button" onClick={() => setShowModal(false)} className="btn-secondary">
                  取消
                </button>
                <button type="submit" disabled={submitting} className="btn-primary disabled:opacity-60">
                  {submitting ? '提交中…' : editingRecord ? '提交更正（生成新版本）' : '创建'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {historyRecord && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
          <div className="bg-white rounded-xl p-6 w-full max-w-2xl mx-4 max-h-[90vh] overflow-y-auto">
            <div className="flex items-center justify-between mb-6">
              <h2 className="text-xl font-bold text-gray-900">
                修订历史 · 投苗记录 #{historyRecord.id}
              </h2>
              <button
                onClick={() => setHistoryRecord(null)}
                className="p-2 text-gray-400 hover:text-gray-600"
              >
                <X size={20} />
              </button>
            </div>
            {historyLoading ? (
              <p className="text-gray-500">加载中...</p>
            ) : (
              <ol className="relative border-l border-gray-200 ml-2 space-y-4">
                {revisions.map((rev) => (
                  <li key={rev.id} className="ml-4">
                    <div className="flex items-center space-x-2">
                      <span
                        className={`badge ${
                          rev.action === 'void'
                            ? 'badge-warning'
                            : rev.action === 'repair'
                              ? 'badge-info'
                              : 'badge-success'
                        }`}
                      >
                        {actionLabels[rev.action] || rev.action}
                      </span>
                      <span className="text-xs text-gray-400">
                        序号 {rev.sequence} · {new Date(rev.created_at).toLocaleString()}
                      </span>
                    </div>
                    {rev.reason && <p className="text-sm text-gray-700 mt-1">原因：{rev.reason}</p>}
                    <RevisionDiff before={rev.before_data} after={rev.after_data} />
                  </li>
                ))}
                {revisions.length === 0 && <p className="text-gray-500">暂无修订记录</p>}
              </ol>
            )}
          </div>
        </div>
      )}
    </div>
  );
};

const RevisionDiff: React.FC<{ before?: string; after?: string }> = ({ before, after }) => {
  const parse = (raw?: string) => {
    if (!raw) return null;
    try {
      return JSON.parse(raw) as Record<string, unknown>;
    } catch {
      return null;
    }
  };
  const beforeObj = parse(before);
  const afterObj = parse(after);
  const fields: Array<[string, string]> = [
    ['quantity', '尾数'],
    ['weight_per_unit', '单重(克/尾)'],
    ['total_weight', '总重(公斤)'],
    ['species', '品种'],
    ['record_type', '类型'],
    ['status', '状态'],
  ];
  return (
    <div className="mt-2 text-xs">
      <table className="table">
        <thead>
          <tr>
            <th>字段</th>
            <th>原值</th>
            <th>新值</th>
          </tr>
        </thead>
        <tbody>
          {fields.map(([key, label]) => {
            const b = beforeObj?.[key];
            const a = afterObj?.[key];
            if (b === a) return null;
            return (
              <tr key={key}>
                <td>{label}</td>
                <td className="text-red-600">{b === undefined ? '—' : String(b)}</td>
                <td className="text-green-700">{a === undefined ? '—' : String(a)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
};

export default StockingRecords;
