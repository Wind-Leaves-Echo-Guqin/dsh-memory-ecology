/* ============================================================
   polyfill.js — 旧 WebView 内核兼容层（T12/G）
   pywebview 原生窗口（宿主主用环境）内核较旧，缺以下 API 时逐个补齐。
   全部为最小实现；有原生 API 时不动。
   ============================================================ */
'use strict';

/* RENDER 注册表必须先于 views/*.js 存在（视图脚本向它挂载渲染函数） */
var RENDER = {};

/* CSS.escape —— focusSkill / 选择器拼接依赖（缺失时星图定位抛错级联失效）。
   OCR 2026-10-03：对齐规范——补首字符为数字 / 「-」后跟数字 / 单独「-」的转义，
   原实现 `CSS.escape("1col")` 原样返回，拼出的 `#1col` 在旧内核仍是非法选择器。 */
if (typeof CSS !== 'object') { window.CSS = {}; }
if (typeof CSS.escape !== 'function') {
  CSS.escape = function (s) {
    var str = String(s);
    var first = str.charCodeAt(0);
    var out = '';
    for (var i = 0; i < str.length; i++) {
      var c = str.charCodeAt(i);
      if (c === 0) { out += '\uFFFD'; continue; }
      if ((c >= 0x01 && c <= 0x1F) || c === 0x7F ||
          (i === 0 && c >= 0x30 && c <= 0x39) ||
          (i === 1 && c >= 0x30 && c <= 0x39 && first === 0x2D)) {
        out += '\\' + c.toString(16) + ' ';
        continue;
      }
      if (i === 0 && str.length === 1 && c === 0x2D) { out += '\\-'; continue; }
      if (c >= 0x80 || c === 0x2D || c === 0x5F ||
          (c >= 0x30 && c <= 0x39) || (c >= 0x41 && c <= 0x5A) || (c >= 0x61 && c <= 0x7A)) {
        out += str.charAt(i);
      } else {
        out += '\\' + str.charAt(i);
      }
    }
    return out;
  };
}

/* Object.assign */
if (typeof Object.assign !== 'function') {
  Object.assign = function (target) {
    if (target == null) throw new TypeError('Cannot convert undefined or null to object');
    var to = Object(target);
    for (var i = 1; i < arguments.length; i++) {
      var src = arguments[i];
      if (src == null) continue;
      for (var k in src) { if (Object.prototype.hasOwnProperty.call(src, k)) to[k] = src[k]; }
    }
    return to;
  };
}

/* Element.closest */
if (window.Element && !Element.prototype.closest) {
  Element.prototype.closest = function (s) {
    var el = this;
    while (el && el.nodeType === 1) {
      if (el.matches(s)) return el;
      el = el.parentElement || el.parentNode;
    }
    return null;
  };
}

/* Element.matches */
if (window.Element && !Element.prototype.matches) {
  Element.prototype.matches =
    Element.prototype.msMatchesSelector ||
    Element.prototype.webkitMatchesSelector ||
    function (s) {
      var el = this;
      var list = (el.document || el.ownerDocument).querySelectorAll(s);
      for (var i = 0; i < list.length; i++) { if (list[i] === el) return true; }
      return false;
    };
}

/* Number.isFinite / Number.isInteger */
if (typeof Number.isFinite !== 'function') {
  Number.isFinite = function (v) { return typeof v === 'number' && isFinite(v); };
}
if (typeof Number.isInteger !== 'function') {
  Number.isInteger = function (v) { return typeof v === 'number' && isFinite(v) && Math.floor(v) === v; };
}

/* String.prototype.includes / startsWith / endsWith */
if (!String.prototype.includes) {
  String.prototype.includes = function (s, i) {
    return this.indexOf(s, i || 0) !== -1;
  };
}
if (!String.prototype.startsWith) {
  String.prototype.startsWith = function (s, i) { return this.indexOf(s, i || 0) === 0; };
}
if (!String.prototype.endsWith) {
  // OCR 2026-10-03：对齐规范——原实现只看"出现于 l-s.length 之后"，未校验恰好结束于 l，
  // `"hello".endsWith("llo", 4)` 会误返回 true；且负起点未防护
  String.prototype.endsWith = function (s, l) {
    s = String(s);
    if (l === undefined || l > this.length) l = this.length;
    else if (l < 0) l = 0;
    var start = l - s.length;
    return start >= 0 && this.substring(start, l) === s;
  };
}

/* Array.prototype.includes */
if (!Array.prototype.includes) {
  // OCR 2026-10-03：对齐 SameValueZero 语义——原复用 indexOf 丢 NaN 匹配、忽略 fromIndex
  Array.prototype.includes = function (x, fromIndex) {
    var len = this.length >>> 0;
    var i = fromIndex | 0;
    if (i < 0) i = Math.max(len + i, 0);
    for (; i < len; i++) {
      var v = this[i];
      if (v === x || (typeof v === 'number' && typeof x === 'number' && isNaN(v) && isNaN(x))) return true;
    }
    return false;
  };
}

/* Object.entries / Object.values（图表与视图遍历常用） */
if (typeof Object.entries !== 'function') {
  Object.entries = function (o) {
    return Object.keys(o).map(function (k) { return [k, o[k]]; });
  };
}
if (typeof Object.values !== 'function') {
  Object.values = function (o) {
    return Object.keys(o).map(function (k) { return o[k]; });
  };
}

/* fetch 仅在彻底缺失时警告（正常内核都有；急救模式页不依赖 fetch） */

/* ── 内核能力检测（旧 → 顶栏提示，不强制） ── */
function ecoKernelCheck() {
  try {
    var old = typeof CSS.escape !== 'function' || typeof Promise !== 'function';
    var bd = false;
    try { bd = typeof CSS !== 'undefined' && 'supports' in CSS && CSS.supports('backdrop-filter', 'blur(1px)'); } catch (e) { bd = false; }
    if (old || !bd) {
      var bar = document.createElement('div');
      bar.className = 'kernel-bar';
      // OCR 2026-10-03（security）：location.href 原直接拼进 innerHTML——URL 携带 < 等
      // 字符（查询参数反射）会被当 HTML 解析构成 XSS；改用文本节点插入
      bar.textContent = '⚠ 当前窗口内核较旧（' + (old ? '缺基础 API' : '无 backdrop-filter') +
        '），部分视觉效果受限。建议复制地址用系统浏览器打开：';
      var link = document.createElement('a');
      link.href = '#';
      link.id = 'kernel-copy-url';
      link.textContent = location.href;
      link.onclick = function () {
        if (window.ecoCopy) window.ecoCopy(location.href);
        return false;
      };
      bar.appendChild(link);
      // OCR 2026-10-03：body 未就绪时不再直接访问 firstChild（抛错被吞、提示条永不出现）
      if (document.body) {
        document.body.insertBefore(bar, document.body.firstChild);
      } else {
        document.addEventListener('DOMContentLoaded', function () {
          document.body.insertBefore(bar, document.body.firstChild);
        });
      }
    }
  } catch (e) { /* 检测失败不阻塞 */ }
}
