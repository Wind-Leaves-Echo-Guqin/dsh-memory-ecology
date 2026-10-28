"""root —— 一个数据根（自证 + 只读降级 + 路径）与 detail 条目模型。

自证机制（A2A §7-5 把"禁止设全局根"这条纪律换成机制）：
  - 根内 `.eco/root.json` 保存 `{root_id, schema_version, created_at}`；
  - 任何写操作前 `probe_writable()`（探针名带 pid），失败 → `readonly=True` + 原因；
  - schema 不匹配 → 只读降级，**不迁移、不删除**（迁移必须显式 `migrate` 子命令）；
  - 只读时所有写命令返回 `{ok:false, readonly:true, reason}`，退出码 3，绝不抛异常。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from . import ledger, paths, store

SCHEMA_VERSION = 1

# 记忆 id 的合法字符集。**不是装饰**：id 会拼进文件路径，允许 `..` 或分隔符
# 就等于允许任意文件读取（`memory_load` 的 id 直接来自模型）。
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")


class Root:
    """一个数据根。构造不写盘；`open()` 才做自证。"""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.meta: dict = {}
        self.readonly = False
        self.reason = ""

    # ---- 自证 ----
    @property
    def meta_path(self) -> Path:
        return self.path / ".eco" / "root.json"

    def open(self, create: bool = False) -> "Root":
        if self.meta_path.is_file():
            try:
                self.meta = json.loads(paths.read_text(self.meta_path) or "{}")
            except (ValueError, TypeError):
                self.meta = {}
                self.readonly = True
                self.reason = "root.json-corrupt"
                return self
        if not self.meta:
            if not create:
                self.readonly = True
                self.reason = "root-not-initialized"
                return self
            self._init_meta()
        schema = self.meta.get("schema_version")
        if schema != SCHEMA_VERSION:
            self.readonly = True
            self.reason = "schema-mismatch:%s" % schema
            return self
        if not paths.probe_writable(self.path):
            self.readonly = True
            self.reason = "root-not-writable"
        return self

    def _init_meta(self) -> None:
        self.meta = {
            "root_id": paths.fingerprint(str(self.path), paths.now())[:16],
            "schema_version": SCHEMA_VERSION,
            "created_at": paths.now(),
        }
        paths.write_atomic(self.meta_path,
                           json.dumps(self.meta, ensure_ascii=False, indent=2))

    def readonly_payload(self) -> dict:
        return {"ok": False, "readonly": True, "reason": self.reason,
                "root": str(self.path)}

    # ---- 路径 ----
    @property
    def memories(self) -> Path:
        return self.path / "memories"

    @property
    def detail(self) -> Path:
        return self.memories / "detail"

    @property
    def pending(self) -> Path:
        return self.memories / "pending"

    @property
    def quarantine(self) -> Path:
        return self.memories / "quarantine"

    @property
    def user_candidates(self) -> Path:
        return self.memories / "user_candidates"

    @property
    def db(self) -> Path:
        return self.path / "eco.db"

    @property
    def catalog_dir(self) -> Path:
        return self.path / ".eco" / "catalog"

    @property
    def packs_dir(self) -> Path:
        return self.path / "packs"

    @property
    def memory_md(self) -> Path:
        return self.memories / "MEMORY.md"

    @property
    def user_md(self) -> Path:
        return self.memories / "USER.md"

    # ---- 便捷 ----
    def connect(self):
        return ledger.open_rw(self.db)

    def connect_ro(self):
        return ledger.open_ro(self.db)

    def items(self) -> list[dict]:
        """全部 detail 条目（含已降级/已取代的——调用方按 status/fate 过滤）。"""
        return store.load_detail(self.detail)

    def item(self, ident: str) -> dict | None:
        """按 id 取一条记忆。

        **必须做 id 校验**：`memory_load` 的 id 来自模型，直接拼路径等于把
        "读根内/根外任意 .md"暴露给模型（实测 `load ../PRIVATE` 能读出根内其它
        文件，`load ../../secret` 能读根外文件，绝对路径也吃）。
        """
        ident = str(ident or "").strip()
        if not _SAFE_ID.fullmatch(ident):
            return None
        p = self.detail / (ident + ".md")
        if p.is_file():
            fm, body = store.parse_frontmatter(paths.read_text(p))
            return {"path": p, "name": p.stem, "fm": fm, "body": body,
                    "type": store.norm_type(fm.get("type", "")),
                    "status": (fm.get("status") or "active").strip().lower(),
                    "scope": store.norm_scope(fm.get("scope")) if fm.get("scope") else "global"}
        for it in self.items():
            if it["fm"].get("id") == ident:
                return it
        return None

    def write_item(self, name: str, fm: dict, body: str) -> Path:
        """写一条 detail。**同样校验 name**（它会变成文件名）。"""
        name = str(name or "").strip()
        if not _SAFE_ID.fullmatch(name):
            raise ValueError("不安全的条目名：%r" % name)
        p = self.detail / (name + ".md")
        paths.write_atomic(p, store.dump_frontmatter(fm, body))
        return p

    # ---- 子目录懒建 ----
    def ensure_dirs(self) -> None:
        for d in (self.detail, self.pending, self.quarantine, self.user_candidates,
                  self.catalog_dir, self.path / ".eco"):
            d.mkdir(parents=True, exist_ok=True)


def init_root(path: Path | str) -> Root:
    """建根（幂等）。已存在则只做自证，不重写 meta（否则 root_id 会漂）。"""
    root = Root(path)
    fresh = not root.meta_path.is_file()
    root.path.mkdir(parents=True, exist_ok=True)
    root.ensure_dirs()
    root.open(create=True)
    conn = root.connect()
    try:
        if fresh:
            ledger.event(conn, "init", str(root.path), "root_id=%s" % root.meta.get("root_id"))
            conn.commit()
    finally:
        conn.close()
    return root
