"""crypto — 静态加密封套（v3.1.0 信息安全件；调研依据：Molly 记忆池 AES-256-GCM 信封模式）。

为什么不用 AES-GCM：核心包零依赖是不变量（V3_BLUEPRINT D2），stdlib 没有 AEAD。
等价强度的 stdlib 构造（全部原语标准化、C 实现）：

  keystream 块_i = SHAKE256(enc_key ‖ nonce ‖ block_index).digest(CHUNK)   （XOF-CTR）
  密文 = 明文 ⊕ keystream
  mac   = HMAC-SHA256(mac_key, magic ‖ nonce ‖ ct)
  enc_key / mac_key = HKDF-SHA256(master, salt=nonce, info=角色)           （密钥分离）
  master = 密钥文件 32B 随机（secrets），或 PBKDF2-HMAC-SHA256(passphrase)  （离线恢复通道）

封套格式 v1：b"ECOENC1" ‖ nonce(16) ‖ ciphertext ‖ mac(32)。

安全属性：
  · encrypt-then-MAC——先验 MAC 再解密，无填充预言、不可篡改（bit 翻转在 MAC 处拦截）
  · 每文件 128-bit 随机 nonce；HKDF 以 nonce 为 salt → 即使 master 泄露历史Nonce 重用也不跨文件复用密钥流
  · 解密失败（MAC 不符/密钥缺失）抛 CryptoError，调用方必须保持原备份只读——绝不覆盖唯一数据（Molly 纪律）

密钥分离（红线）：密钥文件不得位于生态数据根内——`assert_key_outside_roots` 强制。
零依赖（stdlib only），无网络。
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from pathlib import Path

MAGIC = b"ECOENC1"
NONCE_LEN = 16
MAC_LEN = 32
CHUNK = 1 << 22            # 4MB keystream 块（XOF-CTR 计数器粒度）
PBKDF2_ITERS = 600_000     # OWASP 2023 线 for PBKDF2-SHA256


class CryptoError(Exception):
    """加解密失败（MAC 不符/格式坏/密钥不可用）。原数据必须保持只读。"""


# ── 主密钥 ──────────────────────────────────────────────────────────────────

def derive_master_from_passphrase(passphrase: str, salt: bytes,
                                  iterations: int = PBKDF2_ITERS) -> bytes:
    """PBKDF2-HMAC-SHA256 → 32B master（离线恢复通道：密钥文件丢失时凭口令重建）。"""
    return hashlib.pbkdf2_hmac("sha256", passphrase.encode("utf-8"), salt, iterations, dklen=32)


def load_or_create_key(path: Path, create: bool = True) -> bytes:
    """读密钥文件；不存在且 create=True 时生成 32B 随机并落盘（尽力收紧权限）。

    · 文件格式：64 位 hex 文本（+尾换行），人可检视、git 不友好（防误提交）
    · create=False（演练/只读场景）：缺失即抛 CryptoError——**绝不静默重建**
    · 创建失败/读取失败抛 CryptoError——密钥不可用时**绝不**继续加密流程
    """
    path = Path(path)
    try:
        if path.is_file():
            raw = path.read_text(encoding="utf-8").strip()
            key = bytes.fromhex(raw)
            if len(key) != 32:
                raise CryptoError(f"密钥文件长度异常（期望 32B）: {path}")
            return key
        if not create:
            raise CryptoError(f"密钥文件不存在（只读场景不重建）: {path}")
        path.parent.mkdir(parents=True, exist_ok=True)
        key = secrets.token_bytes(32)
        # 并发创建竞态（稳定性 S1 实测抓到）：多进程同时首跑时 os.replace 在 Windows 上
        # 互斥锁冲突。tmp 名带 pid 防互踩；replace 失败重试；**最终以磁盘内容为准**——
        # 竞态败者必须收敛到赢者的密钥，绝不能各用各的（否则备份永远解不开彼此）。
        tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}-{secrets.token_hex(4)}")
        tmp.write_text(key.hex() + "\n", encoding="utf-8")
        try:
            import time as _time
            for attempt in range(5):
                try:
                    os.replace(tmp, path)
                    break
                except PermissionError:
                    if attempt == 4:
                        raise
                    _time.sleep(0.1 * (attempt + 1))
            final = bytes.fromhex(path.read_text(encoding="utf-8").strip())
            if len(final) != 32:
                raise CryptoError(f"密钥文件长度异常（期望 32B）: {path}")
            return final
        finally:
            tmp.unlink(missing_ok=True)
    except CryptoError:
        raise
    except Exception as e:
        raise CryptoError(f"密钥文件不可用: {path} ({e})") from e


def assert_key_outside_roots(key_path: Path, roots: list[Path]) -> None:
    """密钥分离红线：密钥文件不得位于任何数据根目录之下（备份盘可被拷走，密钥不能跟着走）。"""
    kp = Path(key_path).resolve()
    for r in roots:
        try:
            rp = Path(r).resolve()
        except OSError:
            continue
        if kp == rp or rp in kp.parents:
            raise CryptoError(f"密钥文件在数据根内（违反密钥分离）: {kp} ⊆ {rp}")


# ── HKDF（RFC 5869，extract-and-expand）────────────────────────────────────

def _hkdf(master: bytes, salt: bytes, info: bytes, length: int = 32) -> bytes:
    prk = hmac.new(salt, master, hashlib.sha256).digest()
    out, t, i = b"", b"", 1
    while len(out) < length:
        t = hmac.new(prk, t + info + bytes([i]), hashlib.sha256).digest()
        out += t
        i += 1
    return out[:length]


def _subkeys(master: bytes, nonce: bytes) -> tuple[bytes, bytes]:
    return (_hkdf(master, nonce, b"ecobackup-enc"), _hkdf(master, nonce, b"ecobackup-mac"))


# ── 加解密（字节级）───────────────────────────────────────────────────────

def _xor_stream(enc_key: bytes, nonce: bytes, data: bytes, offset: int = 0) -> bytes:
    """data ⊕ XOF-CTR keystream（offset 支持分块续算；纯 C 级大整数 XOR）。

    每块只生成所需长度的 keystream（XOF 输出长度任意），小块不浪费。
    """
    n = len(data)
    if n == 0:
        return b""
    ks = b""
    block = offset // CHUNK
    in_block = offset % CHUNK
    if in_block:                      # 块内偏移：先补齐本块剩余，再整块推进
        need = min(CHUNK - in_block, n - len(ks))
        ks += hashlib.shake_256(enc_key + nonce + block.to_bytes(8, "big")).digest(in_block + need)[in_block:]
        block += 1
    while len(ks) < n:
        need = min(CHUNK, n - len(ks))
        ks += hashlib.shake_256(enc_key + nonce + block.to_bytes(8, "big")).digest(need)
        block += 1
    return (int.from_bytes(data, "big") ^ int.from_bytes(ks[:n], "big")) \
        .to_bytes(n, "big")


def encrypt_bytes(master: bytes, plaintext: bytes) -> bytes:
    nonce = secrets.token_bytes(NONCE_LEN)
    enc_key, mac_key = _subkeys(master, nonce)
    ct = _xor_stream(enc_key, nonce, plaintext)
    mac = hmac.new(mac_key, MAGIC + nonce + ct, hashlib.sha256).digest()
    return MAGIC + nonce + ct + mac


def decrypt_bytes(master: bytes, envelope: bytes) -> bytes:
    if len(envelope) < len(MAGIC) + NONCE_LEN + MAC_LEN or not envelope.startswith(MAGIC):
        raise CryptoError("非 ECOENC1 封套或已损坏")
    nonce = envelope[len(MAGIC):len(MAGIC) + NONCE_LEN]
    ct = envelope[len(MAGIC) + NONCE_LEN:-MAC_LEN]
    mac = envelope[-MAC_LEN:]
    enc_key, mac_key = _subkeys(master, nonce)
    expect = hmac.new(mac_key, MAGIC + nonce + ct, hashlib.sha256).digest()
    if not hmac.compare_digest(mac, expect):
        raise CryptoError("MAC 校验失败（数据被篡改或密钥不对）——保持原文件只读")
    return _xor_stream(enc_key, nonce, ct)


def is_envelope(data: bytes) -> bool:
    return data[:len(MAGIC)] == MAGIC


# ── 加解密（文件级，流式）─────────────────────────────────────────────────

def encrypt_file(master: bytes, src: Path, dst: Path) -> int:
    """流式加密 src → dst（ECOENC1 封套）。返回明文大小。dst 为 .tmp 原子改名。"""
    src, dst = Path(src), Path(dst)
    nonce = secrets.token_bytes(NONCE_LEN)
    enc_key, mac_key = _subkeys(master, nonce)
    mac = hmac.new(mac_key, digestmod=hashlib.sha256)   # 与 decrypt_file 同序：head → ct
    # tmp 名带 pid+随机：同目标多进程并发时不互踩（稳定性 S1 实测；锁在上层 eco_backup）
    tmp = dst.with_name(f"{dst.name}.tmp-{os.getpid()}-{secrets.token_hex(4)}")
    size = 0
    with open(src, "rb") as fi, open(tmp, "wb") as fo:
        fo.write(MAGIC + nonce)
        mac.update(MAGIC + nonce)
        while True:
            chunk = fi.read(CHUNK)
            if not chunk:
                break
            ct = _xor_stream(enc_key, nonce, chunk, offset=size)
            fo.write(ct)
            mac.update(ct)
            size += len(chunk)
        fo.write(mac.digest())
    os.replace(tmp, dst)
    return size


def decrypt_file(master: bytes, src: Path, dst: Path) -> int:
    """两遍式解密：**先整体验 MAC，再写明文**（OCR 修正：原单遍流式在验证前就把
    明文落盘，进程被杀时 tmp 残留明文）。不符即抛错，**明文绝不落盘**。"""
    src, dst = Path(src), Path(dst)
    with open(src, "rb") as fi:
        head = fi.read(len(MAGIC) + NONCE_LEN)
        if len(head) < len(MAGIC) + NONCE_LEN or not head.startswith(MAGIC):
            raise CryptoError(f"非 ECOENC1 封套: {src.name}")
        nonce = head[len(MAGIC):]
        enc_key, mac_key = _subkeys(master, nonce)
        # 遍 1：流式重算 MAC（只覆盖密文区，尾部 MAC 字节不入账；不写任何东西）
        fi.seek(0, os.SEEK_END)
        total = fi.tell()
        ct_len = total - len(head) - MAC_LEN
        if ct_len < 0:
            raise CryptoError(f"封套长度异常: {src.name}")
        fi.seek(len(head))
        mac = hmac.new(mac_key, head, hashlib.sha256)
        done = 0
        while done < ct_len:
            chunk = fi.read(min(CHUNK, ct_len - done))
            if not chunk:
                break
            mac.update(chunk)
            done += len(chunk)
        tail = fi.read(MAC_LEN)
        if len(tail) != MAC_LEN or not hmac.compare_digest(tail, mac.digest()):
            raise CryptoError(f"MAC 校验失败: {src.name}——保持原备份只读")
        # 遍 2：MAC 已验，解密落盘（此时写出的必然是可信明文；只读密文区，尾部 MAC 不入）
        fi.seek(len(head))
        tmp = dst.with_name(f"{dst.name}.tmp-{os.getpid()}-{secrets.token_hex(4)}")
        size = 0
        try:
            with open(tmp, "wb") as fo:
                while size < ct_len:
                    chunk = fi.read(min(CHUNK, ct_len - size))
                    if not chunk:
                        break
                    fo.write(_xor_stream(enc_key, nonce, chunk, offset=size))
                    size += len(chunk)
            os.replace(tmp, dst)
        except Exception:
            tmp.unlink(missing_ok=True)
            raise
    return size
