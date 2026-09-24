import React, { useEffect, useMemo, useState } from 'react';
import { Plus, Edit2, Ban, History, X, AlertCircle, Lock } from 'lucide-react';
import axios from 'axios';
import { stockingRecordApi, batchApi, extractApiError } from '../services/api';
import type { StockingRecord, Batch, StockingRecordEvent } from '../types';
import {
  deriveTotalWeightKg, formatGrams, formatKg, getCachedMetrics,
  loadMetrics, newIdempotencyKey, validateQuantity, validateWeightPerUnit,
} from '../utils/metrics';

type FormState = {
  batch_id: string;
  species: string;
  quantity: string;
  source: string;
  batch_number: string;
  weight_per_unit: string;
  notes: string;
};

const EMPTY_FORM: FormState = {
  batch_id: '',
  species: '',
  quantity: '',
  source: '',
  batch_number: '',
  weight_per_unit: '',
  notes: '',
};

const StockingRecords: React.FC = () => {
  const [records, setRecords] = useState<StockingRecord[]>([]);
  const [batches, setBatches] = useState<Batch[]>([]);
  const [loading, setLoading] = useState(true);
  const [showModal, setShowModal] = useState(false);
  const [editingRecord, setEditingRecord] = useState<StockingRecord | null>(null);
  const [formData, setFormData] = useState<FormState>(EMPTY_FORM);
  const [reason, setReason] = useState('');
  const [voidTarget, setVoidTarget] = useState<StockingRecord | null>(null);
  const [voidReason, setVoidReason] = useState('');
  const [historyTarget, setHistoryTarget] = useState<StockingRecord | null>(null);
  const [events, setEvents] = useState<StockingRecordEvent[]>([]);
  const [formError, setFormError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [includeVoided, setIncludeVoided] = useState(true);
  // 每次打开录入弹窗生成一次幂等键，重复点击/网络重试不会产生两次增量
  const [idempotencyKey, setIdempotencyKey] = useState('');

  const fetchData = async () => {
    try {
      const [recordsRes, batchesRes] = await Promise.all([
        stockingRecordApi.getAll(undefined, includeVoided),
        batchApi.getAll(),
      ]);
      setRecords(recordsRes.data);
      setBatches(batchesRes.data);
    } catch (error) {
      setFormError(extractApiError(error));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadMetrics().finally(() => {
      fetchData();
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    setLoading(true);
    fetchData();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [includeVoided]);

  const parsedQuantity = useMemo(() => {
    const t = formData.quantity.trim();
    return /^\d+$/.test(t) ? parseInt(t, 10) : NaN;
  }, [formData.quantity]);
  const parsedWpu = useMemo(() => parseFloat(formData.weight_per_unit), [formData.weight_per_unit]);

  const derivedTotalWeight = useMemo(
    () => deriveTotalWeightKg(parsedQuantity, parsedWpu),
    [parsedQuantity, parsedWpu],
  );

  const openCreate = () => {
    setEditingRecord(null);
    setFormData(EMPTY_FORM);
    setReason('');
    setFormError(null);
    setIdempotencyKey(newIdempotencyKey());
    setShowModal(true);
  };

  const openEdit = (record: StockingRecord) => {
    setEditingRecord(record);
    setFormData({
      batch_id: record.batch_id.toString(),
      species: record.species,
      quantity: record.quantity.toString(),
      source: record.source || '',
      batch_number: record.batch_number || '',
      weight_per_unit: record.weight_per_unit?.toString() || '',
      notes: record.notes || '',
    });
    setReason('');
    setFormError(null);
    setShowModal(true);
  };

  const openHistory = async (record: StockingRecord) => {
    setHistoryTarget(record);
    setEvents([]);
    try {
      const res = await stockingRecordApi.getEvents(record.id);
      setEvents(res.data);
    } catch (error) {
      setFormError(extractApiError(error));
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (submitting) return;
    setFormError(null);

    if (!formData.batch_id) {
      setFormError('请选择养殖批次');
      return;
    }
    if (!formData.species.trim()) {
      setFormError('品种不能为空');
      return;
    }
    const qtyCheck = validateQuantity(formData.quantity);
    if (!qtyCheck.ok) {
      setFormError(qtyCheck.message);
      return;
    }
    const wpuCheck = validateWeightPerUnit(formData.weight_per_unit);
    if (!wpuCheck.ok) {
      setFormError(wpuCheck.message);
      return;
    }

    setSubmitting(true);
    try {
      if (editingRecord) {
        if (!reason.trim()) {
          setFormError('更正必须填写原因，原值将保留在审计记录中');
          setSubmitting(false);
          return;
        }
        await stockingRecordApi.correct(editingRecord.id, {
          quantity: parsedQuantity,
          species: formData.species.trim(),
          weight_per_unit: parseFloat(formData.weight_per_unit),
          source: formData.source.trim() || undefined,
          batch_number: formData.batch_number.trim() || undefined,
          notes: formData.notes.trim() || undefined,
          expected_version: editingRecord.version,
          reason: reason.trim(),
        });
      } else {
        await stockingRecordApi.create(
          {
            batch_id: parseInt(formData.batch_id, 10),
            species: formData.species.trim(),
            quantity: parsedQuantity,
            weight_per_unit: parseFloat(formData.weight_per_unit),
            source: formData.source.trim() || undefined,
            batch_number: formData.batch_number.trim() || undefined,
            notes: formData.notes.trim() || undefined,
          },
          idempotencyKey,
        );
      }
      setShowModal(false);
      setEditingRecord(null);
      await fetchData();
    } catch (error) {
      if (axios.isAxiosError(error) && error.response?.status === 409) {
        setFormError(`${extractApiError(error)}（请关闭弹窗后刷新列表，基于最新版本重试）`);
        await fetchData();
      } else {
        setFormError(extractApiError(error));
      }
    } finally {
      setSubmitting(false);
    }
  };

  const handleVoid = async () => {
    if (!voidTarget || submitting) return;
    if (!voidReason.trim()) {
      setFormError('撤销必须填写原因');
      return;
    }
    setSubmitting(true);
    try {
      await stockingRecordApi.void(voidTarget.id, voidReason.trim());
      setVoidTarget(null);
      setVoidReason('');
      await fetchData();
    } catch (error) {
      setFormError(extractApiError(error));
    } finally {
      setSubmitting(false);
    }
  };

  const getBatchNumber = (batchId: number) =>
    batches.find((b) => b.id === batchId)?.batch_number ?? '未知批次';

  const isLockedRecord = (record: StockingRecord) =>
    record.status === 'voided'; // 已参与分析的锁定由后端 409 返回，前端据错误提示

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
            记录水产养殖投苗信息（含分批补苗）· 总重量按
            「尾数 × 每尾克重 ÷ 1000」自动派生，口径版本 {getCachedMetrics().version}
          </p>
        </div>
        <button onClick={openCreate} className="btn-primary flex items-center space-x-2">
          <Plus size={20} />
          <span>新增记录 / 分批补苗</span>
        </button>
      </div>

      {formError && (
        <div className="p-3 bg-red-100 text-red-700 rounded-lg flex items-start space-x-2">
          <AlertCircle size={18} className="mt-0.5 shrink-0" />
          <span>{formError}</span>
        </div>
      )}

      <div className="flex items-center space-x-2 text-sm text-gray-600">
        <label className="flex items-center space-x-1 cursor-pointer">
          <input
            type="checkbox"
            checked={includeVoided}
            onChange={(e) => setIncludeVoided(e.target.checked)}
          />
          <span>显示已撤销记录</span>
        </label>
      </div>

      <div className="card">
        <div className="overflow-x-auto">
          <table className="table">
            <thead>
              <tr>
                <th>批次号</th>
                <th>品种</th>
                <th>数量(尾)</th>
                <th>每尾克重(克/尾)</th>
                <th>总重量(公斤)</th>
                <th>来源</th>
                <th>苗种批次</th>
                <th>状态</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {records.map((record) => (
                <tr
                  key={record.id}
                  className={record.status === 'voided' ? 'text-gray-400 line-through' : ''}
                >
                  <td className="font-medium text-ocean-700">{getBatchNumber(record.batch_id)}</td>
                  <td>{record.species}</td>
                  <td>{record.quantity.toLocaleString()}</td>
                  <td>{formatGrams(record.weight_per_unit)}</td>
                  <td>{formatKg(record.total_weight)}</td>
                  <td>{record.source || '-'}</td>
                  <td>{record.batch_number || '-'}</td>
                  <td>
                    {record.status === 'voided' ? (
                      <span className="badge badge-warning">已撤销</span>
                    ) : (
                      <span className="badge badge-success">有效 v{record.version}</span>
                    )}
                  </td>
                  <td>
                    <div className="flex items-center space-x-2">
                      <button
                        title="更正（保留原值与原因）"
                        onClick={() => openEdit(record)}
                        disabled={isLockedRecord(record)}
                        className="p-2 text-ocean-600 hover:bg-ocean-50 rounded-lg transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
                      >
                        <Edit2 size={18} />
                      </button>
                      <button
                        title="撤销（软删除并留痕）"
                        onClick={() => {
                          setVoidTarget(record);
                          setVoidReason('');
                          setFormError(null);
                        }}
                        disabled={isLockedRecord(record)}
                        className="p-2 text-amber-600 hover:bg-amber-50 rounded-lg transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
                      >
                        <Ban size={18} />
                      </button>
                      <button
                        title="更正/撤销历史"
                        onClick={() => openHistory(record)}
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
                  <td colSpan={9} className="text-center py-8 text-gray-500">
                    暂无投苗记录
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* 新增 / 更正弹窗 */}
      {showModal && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
          <div className="bg-white rounded-xl p-6 w-full max-w-lg mx-4 max-h-[90vh] overflow-y-auto">
            <div className="flex items-center justify-between mb-6">
              <h2 className="text-xl font-bold text-gray-900">
                {editingRecord ? `更正投苗记录 #${editingRecord.id}` : '新增投苗记录 / 分批补苗'}
              </h2>
              <button
                onClick={() => setShowModal(false)}
                className="p-2 text-gray-400 hover:text-gray-600"
              >
                <X size={20} />
              </button>
            </div>

            {editingRecord && (
              <div className="mb-4 p-3 bg-amber-50 border border-amber-200 rounded-lg text-sm text-amber-800 flex items-start space-x-2">
                <Lock size={16} className="mt-0.5 shrink-0" />
                <div>
                  更正基于当前版本 <b>v{editingRecord.version}</b>，原值与原因将写入审计记录。
                  已参与周期分析（批次已出塘/已有销售）的记录会被服务端锁定，锁定后请改用
                  「新增记录 / 分批补苗」冲销。
                </div>
              </div>
            )}

            <form onSubmit={handleSubmit} className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  养殖批次 <span className="text-red-500">*</span>
                </label>
                <select
                  required
                  disabled={!!editingRecord}
                  value={formData.batch_id}
                  onChange={(e) => setFormData({ ...formData, batch_id: e.target.value })}
                  className="select-field disabled:bg-gray-100"
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
                    数量(尾) <span className="text-red-500">*</span>
                  </label>
                  <input
                    type="number"
                    required
                    min={1}
                    step={1}
                    value={formData.quantity}
                    onChange={(e) => setFormData({ ...formData, quantity: e.target.value })}
                    className="input-field"
                    placeholder="正整数尾数"
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

              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">
                    每尾克重(克/尾) <span className="text-red-500">*</span>
                  </label>
                  <input
                    type="number"
                    required
                    min={0.01}
                    max={5000}
                    step="0.01"
                    value={formData.weight_per_unit}
                    onChange={(e) => setFormData({ ...formData, weight_per_unit: e.target.value })}
                    className="input-field"
                    placeholder="0.01 ~ 5000 克"
                  />
                </div>

                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">
                    总重量(公斤，自动派生)
                  </label>
                  <input
                    type="text"
                    readOnly
                    value={derivedTotalWeight === null ? '—' : formatKg(derivedTotalWeight)}
                    className="input-field bg-gray-100 text-gray-700 font-medium"
                    title="总重量由尾数与每尾克重按统一口径派生，不可手工填写"
                  />
                  <p className="text-xs text-gray-400 mt-1">
                    = 尾数 × 克/尾 ÷ 1000，四舍五入到克（0.001 公斤）
                  </p>
                </div>
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  备注
                </label>
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
                    value={reason}
                    onChange={(e) => setReason(e.target.value)}
                    className="input-field"
                    rows={2}
                    placeholder="例如：到场复核实际尾数为 1200 尾"
                  />
                </div>
              )}

              <div className="flex justify-end space-x-3 pt-4">
                <button
                  type="button"
                  onClick={() => setShowModal(false)}
                  className="btn-secondary"
                >
                  取消
                </button>
                <button type="submit" disabled={submitting} className="btn-primary disabled:opacity-60">
                  {submitting ? '提交中…' : editingRecord ? '提交更正' : '创建'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* 撤销弹窗 */}
      {voidTarget && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
          <div className="bg-white rounded-xl p-6 w-full max-w-md mx-4">
            <h2 className="text-lg font-bold text-gray-900 mb-2">撤销投苗记录 #{voidTarget.id}</h2>
            <p className="text-sm text-gray-600 mb-4">
              撤销为软删除：原值保留在审计记录中，且不再计入投苗汇总与成活率分析。
              已参与周期分析的记录将被服务端拒绝。
            </p>
            <textarea
              value={voidReason}
              onChange={(e) => setVoidReason(e.target.value)}
              className="input-field"
              rows={3}
              placeholder="撤销原因（必填），例如：重复录入"
            />
            <div className="flex justify-end space-x-3 mt-4">
              <button onClick={() => setVoidTarget(null)} className="btn-secondary">
                取消
              </button>
              <button
                onClick={handleVoid}
                disabled={submitting}
                className="btn-primary bg-amber-600 hover:bg-amber-700 disabled:opacity-60"
              >
                {submitting ? '处理中…' : '确认撤销'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 审计历史弹窗 */}
      {historyTarget && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
          <div className="bg-white rounded-xl p-6 w-full max-w-2xl mx-4 max-h-[85vh] overflow-y-auto">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-lg font-bold text-gray-900">
                投苗记录 #{historyTarget.id} 变更历史
              </h2>
              <button
                onClick={() => setHistoryTarget(null)}
                className="p-2 text-gray-400 hover:text-gray-600"
              >
                <X size={20} />
              </button>
            </div>
            {events.length === 0 ? (
              <p className="text-gray-500 text-sm py-6 text-center">暂无历史事件</p>
            ) : (
              <ol className="relative border-l border-gray-200 ml-2 space-y-4">
                {events.map((ev) => (
                  <li key={ev.id} className="ml-4">
                    <div className="text-sm font-medium text-gray-900">
                      {ev.event_type === 'created' && '创建'}
                      {ev.event_type === 'corrected' && '更正'}
                      {ev.event_type === 'voided' && '撤销'}
                      {ev.event_type === 'repaired' && '历史口径修复'}
                      <span className="ml-2 text-xs text-gray-400">
                        v{ev.from_version ?? '?'} → v{ev.to_version ?? '?'} · {ev.event_at}
                      </span>
                    </div>
                    {ev.reason && (
                      <div className="text-sm text-amber-700 mt-0.5">原因：{ev.reason}</div>
                    )}
                    {ev.previous_value && (
                      <pre className="text-xs bg-gray-50 rounded p-2 mt-1 overflow-x-auto text-gray-600">
                        {`原值: 尾数 ${(ev.previous_value as { quantity?: number }).quantity}，`
                          + `克/尾 ${(ev.previous_value as { weight_per_unit?: number }).weight_per_unit}，`
                          + `总重 ${(ev.previous_value as { total_weight?: number }).total_weight}kg，`
                          + `状态 ${(ev.previous_value as { status?: string }).status}`}
                      </pre>
                    )}
                    {ev.new_value && (
                      <pre className="text-xs bg-green-50 rounded p-2 mt-1 overflow-x-auto text-gray-600">
                        {`新值: 尾数 ${(ev.new_value as { quantity?: number }).quantity}，`
                          + `克/尾 ${(ev.new_value as { weight_per_unit?: number }).weight_per_unit}，`
                          + `总重 ${(ev.new_value as { total_weight?: number }).total_weight}kg，`
                          + `状态 ${(ev.new_value as { status?: string }).status}`}
                      </pre>
                    )}
                  </li>
                ))}
              </ol>
            )}
          </div>
        </div>
      )}
    </div>
  );
};

export default StockingRecords;
