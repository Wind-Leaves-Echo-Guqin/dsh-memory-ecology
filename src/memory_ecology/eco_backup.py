#!/usr/bin/env python3
"""核心文件定期备份（体检报告 §9 P1 落地件，2026-10-06 生态护理；v3.1.0 收编+加密）。

范围：MEMORY.md / USER.md / jobs.json / index.db（.agent-index SQL 索引）。
备份区：<HERMES>/backup_core/；manifest.json 记 sha256 台账（明文摘要，与密文并存）。
保留期：小文件 30 期；index.db 3 期；轮转只删本脚本自己产出的 .bak-core-*。
校验：复制后 sha256/SQLite 逻辑画像双向核对，不一致即删副本报错。

v3.1.0（信息安全件，用户拍板"加密必须做"）：
  · **新备份全部加密**（lib/crypto ECOENC1 封套：SHAKE256-CTR + HMAC etM，stdlib-only）
  · **密钥分离**：密钥文件默认 ~/.memory-ecology-keys/backup.key（数据根之外，红线强制）；
    MEMORY_ECOLOGY_BACKUP_KEY_FILE 可覆盖；丢失时可用 --rederive <口令> 重建（PBKDF2）
  · **解密失败 = 只读**：MAC 不符/密钥缺失时绝不触碰原备份，报错退出（Molly 纪律）
  · 历史明文备份保持可读（manifest 无 enc 标记按明文校验），自然轮转淘汰
  · --drill 恢复演练：加密备份解到临时目录验证后清理
调度：cron「生态核心备份」每日 12:55（live 既有任务，文件名/cwd 契约不变）。
"""
import datetime
import hashlib
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib.config import hermes_root
from lib import crypto

HERMES = hermes_root()
BACKUP_DIR = HERMES / "backup_core"
KEY_FILE = Path(os.environ.get("MEMORY_ECOLOGY_BACKUP_KEY_FILE")
                or (Path.home() / ".memory-ecology-keys" / "backup.key"))
KEEP_DEFAULT = 30
KEEP_BY_NAME = {"index.db": 3}
TARGETS = [
    HERMES / "memories" / "MEMORY.md",
    HERMES / "memories" / "USER.md",
    HERMES / "cron" / "jobs.json",
    Path.home() / ".memory-ecology" / "index.db",
]


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_master() -> bytes:
    """取主密钥：数据根之外的密钥文件（红线强制），必要时创建。"""
    crypto.assert_key_outside_roots(KEY_FILE, [HERMES])
    return crypto.load_or_create_key(KEY_FILE)


def copy_live(src: Path, dst: Path):
    """普通文件直接 copy2；SQLite 库走 backup API（防 WAL 撕裂）。"""
    import shutil
    import sqlite3
    if src.suffix == ".db":
        try:
            con = sqlite3.connect(f"file:{src.as_posix()}?mode=ro", uri=True)
            try:
                out = sqlite3.connect(str(dst))
                try:
                    with out:
                        con.backup(out)
                finally:
                    out.close()
            finally:
                con.close()
            return
        except sqlite3.Error:
            dst.unlink(missing_ok=True)
    shutil.copy2(src, dst)


def sqlite_profile(p: Path) -> dict:
    import sqlite3
    con = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True)
    try:
        ic = con.execute("PRAGMA integrity_check").fetchone()[0]
        tables = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        counts = {t: con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tables}
        return {"integrity": ic, "tables": counts}
    finally:
        con.close()


def backup_one(src: Path, master: bytes) -> dict:
    """备份单目标 → 加密封套。失败路径：副本删除 + 异常（cron last_status 如实转 error）。"""
    if not src.is_file():
        raise FileNotFoundError(f"目标不存在: {src}")
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    plain = BACKUP_DIR / f".plain-{src.name}-{ts}"      # 明文中转（同目录短暂存在）
    dst = BACKUP_DIR / f"{src.name}.bak-core-{ts}.enc"
    try:
        copy_live(src, plain)
        check_kind, detail = None, {}
        if src.suffix == ".db":
            a, b = sqlite_profile(src), sqlite_profile(plain)
            if a["integrity"] != "ok" or b["integrity"] != "ok" or a["tables"] != b["tables"]:
                raise RuntimeError(f"校验失败（sqlite 画像不一致）: {src.name} src={a} dst={b}")
            check_kind, detail = "sqlite-profile", {"tables": b["tables"]}
            plain_sha = f"sqlite:{len(b['tables'])}tables"
        else:
            s = sha256(plain)
            if s != sha256(src):
                raise RuntimeError(f"校验失败（复制后 hash 不一致）: {src.name}")
            check_kind, plain_sha, detail = "sha256", s, {}
        size = crypto.encrypt_file(master, plain, dst)
        if size != plain.stat().st_size:
            raise RuntimeError(f"加密长度不符: {src.name}")
        return {"target": str(src), "file": dst.name, "check": check_kind,
                "sha256": plain_sha, "enc": True, "size": dst.stat().st_size,
                "plain_size": size, "time": ts, **detail}
    finally:
        plain.unlink(missing_ok=True)   # 明文中转无论成败即删


def rotate(name: str):
    keep = KEEP_BY_NAME.get(name, KEEP_DEFAULT)
    mine = sorted(BACKUP_DIR.glob(f"{name}.bak-core-*"))
    for old in mine[:-keep]:
        old.unlink()
        print(f"  轮转清理（超 {keep} 期）: {old.name}")


def load_manifest() -> dict:
    p = BACKUP_DIR / "manifest.json"
    if p.is_file():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            print("⚠️ manifest.json 损坏，重建台账（历史清空，备份文件本体不受影响）")
    return {}


def save_manifest(m: dict):
    tmp = BACKUP_DIR / "manifest.json.tmp"
    tmp.write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, BACKUP_DIR / "manifest.json")


def drill() -> bool:
    """恢复演练：解密到临时目录，按台账校验（sqlite 画像 / sha256），失败不碰原备份。"""
    import shutil
    ok = True
    m = load_manifest()
    master = None
    for src in TARGETS:
        ent = m.get("latest", {}).get(src.name)
        if not ent or not (BACKUP_DIR / ent["file"]).is_file():
            print(f"✗ 无可用备份: {src.name}")
            ok = False
            continue
        tmpd = BACKUP_DIR / f".tmp-drill-{datetime.datetime.now().strftime('%Y%m%d-%H%M%S%f')}"
        tmpd.mkdir(parents=True, exist_ok=True)
        try:
            restored = tmpd / src.name
            src_bak = BACKUP_DIR / ent["file"]
            if ent.get("enc"):
                if master is None:
                    try:
                        # 演练是只读场景：密钥缺失绝不重建（重建出的新密钥必解不开，
                        # 还会在磁盘上留一个误导性的密钥文件）
                        crypto.assert_key_outside_roots(KEY_FILE, [HERMES])
                        master = crypto.load_or_create_key(KEY_FILE, create=False)
                    except crypto.CryptoError as e:
                        # 密钥不可用：备份保持只读，演练失败但原文件分毫未动
                        print(f"✗ 密钥不可用（备份保持只读）: {e}")
                        return False
                try:
                    crypto.decrypt_file(master, src_bak, restored)
                except crypto.CryptoError as e:
                    print(f"✗ 解密失败（备份保持只读）: {src.name}（{e}）")
                    ok = False
                    continue
            else:
                shutil.copy2(src_bak, restored)   # 历史明文备份
            if ent.get("check") == "sqlite-profile":
                prof = sqlite_profile(restored)
                good = prof["integrity"] == "ok" and prof["tables"] == ent.get("tables")
            else:
                good = sha256(restored) == ent.get("sha256")
            print(("✓ 恢复演练通过: " if good else "✗ 恢复演练不一致: ")
                  + f"{src.name}（{ent['file']}{'，已解密' if ent.get('enc') else '，明文'}）")
            ok = ok and good
        finally:
            shutil.rmtree(tmpd, ignore_errors=True)
    return ok


def main():
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    if "--drill" in sys.argv:
        sys.exit(0 if drill() else 1)
    if "--rederive" in sys.argv:
        # 密钥文件丢失的离线恢复通道：口令 → PBKDF2 重建主密钥（需当初用同口令导出过）
        # OCR 修正：口令输错会毁掉在位密钥——重写前先备份在位密钥（恢复通道自身
        # 不得具有破坏性；也许它是唯一的）
        i = sys.argv.index("--rederive")
        passphrase = sys.argv[i + 1] if len(sys.argv) > i + 1 else ""
        if not passphrase:
            print("用法: eco_backup.py --rederive <口令> [salt-hex]", file=sys.stderr)
            return 2
        salt = bytes.fromhex(sys.argv[i + 2]) if len(sys.argv) > i + 2 else b"eco-backup-v1"
        key = crypto.derive_master_from_passphrase(passphrase, salt)
        crypto.assert_key_outside_roots(KEY_FILE, [HERMES])
        KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
        if KEY_FILE.is_file():
            import datetime as _dt
            bak = KEY_FILE.with_name(f"{KEY_FILE.name}.bak-{_dt.datetime.now():%Y%m%d%H%M%S}")
            KEY_FILE.replace(bak)
            print(f"ℹ️ 在位密钥已备份 → {bak.name}（口令若与当初不同，历史备份将解不开，"
                  f"可用该备份回退）")
        KEY_FILE.write_text(key.hex() + "\n", encoding="utf-8")
        print(f"✓ 主密钥已按口令重建 → {KEY_FILE}")
        return 0

    # v3.1.0：实例锁（稳定性 S1 抓到并发同名碰撞；与门①②④同款 gatekit 纪律）。
    # cron 与手动并发时后到者整轮跳过（备份幂等，下一班次自然补上）。
    from lib.gatekit import acquire_lock_auto
    lock = HERMES / "scripts" / ".eco_backup.lock"
    locked, how = acquire_lock_auto(lock)
    if not locked:
        print("⚠️ 已有 eco_backup 实例在运行，本轮跳过")
        sys.exit(0)
    if how == "stale-cleared":
        print("ℹ️ 已回收上次残留的锁")
    try:
        return _backup_all()
    finally:
        lock.unlink(missing_ok=True)


def _backup_all() -> None:
    try:
        master = load_master()
    except crypto.CryptoError as e:
        print(f"✗ {e}——本轮跳过（既有备份不动）", file=sys.stderr)
        sys.exit(1)

    m = load_manifest()
    m.setdefault("latest", {})
    m.setdefault("history", [])
    fails = skips = 0
    for src in TARGETS:
        try:
            ent = backup_one(src, master)
        except FileNotFoundError as e:
            if src.name == "index.db":
                print(f"⚠︎ {src.name}: 本机无 .agent-index，跳过")
                skips += 1
                continue
            print(f"✗ {src.name}: {e}")
            fails += 1
            continue
        except Exception as e:
            print(f"✗ {src.name}: {e}")
            fails += 1
            continue
        m["latest"][src.name] = ent
        m["history"].append(ent)
        rotate(src.name)
        detail = (f"integrity ok，{len(ent.get('tables', {}))} 表" if ent["check"] == "sqlite-profile"
                  else f"sha256 {ent['sha256'][:12]}…")
        print(f"✓ {src.name} -> {ent['file']}（{detail}，加密 {ent['size']} B）")
    m["history"] = m["history"][-200:]
    m["updated"] = datetime.datetime.now().isoformat(timespec="seconds")
    m["enc"] = "ECOENC1"
    save_manifest(m)
    print(f"备份完成：成功 {len(TARGETS) - fails - skips}/{len(TARGETS) - skips}，"
          f"备份区 {BACKUP_DIR}（全部加密，密钥 {KEY_FILE}）")
    sys.exit(0 if fails == 0 else 1)


if __name__ == "__main__":
    main()
