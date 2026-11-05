/** 观测舱自身版本（**唯一前端文案来源**）。
 *
 * 为什么不从后端读：`/api/meta` 的 `version` 是**生态数据根**的版本
 * （历史上读 `%LOCALAPPDATA%\hermes\VERSION.md`，现在读核心引擎），
 * 与"观测舱这个前端应用自己是第几版"是两件事——之前混用导致设置页出现
 * 「观测舱版本 v3.4.1」旁边写着「生态版本 v3.1.0」，看着像版本错乱。
 *
 * 这个值必须与 `integrations/gui-v3/package.json` 的 `version` 一致；
 * `python -m memory_ecology version` 会校验 package.json，
 * 所以改版本时：package.json + 本文件 + index.html 标题三处一起改。
 */
export const OBSERVATORY_VERSION = '3.4.1'

/** 带 v 前缀的展示形态 */
export const OBSERVATORY_VERSION_TAG = `v${OBSERVATORY_VERSION}`
