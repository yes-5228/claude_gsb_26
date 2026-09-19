/**
 * 生成客户端幂等键：同一批次（弹窗打开 → 提交 → 超时重试）复用同一个键，
 * 后端据此保证重复提交不产生重复结果。
 */
export function newIdempotencyKey() {
  if (typeof crypto !== 'undefined' && crypto.randomUUID) {
    return `fe_${crypto.randomUUID().replace(/-/g, '')}`;
  }
  return `fe_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 18)}`;
}
