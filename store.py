"""Armazenamento local: configurações do app e o soundboard (pasta de sons do host)."""
import json
import os
import shutil
from pathlib import Path

import protocol as P

PALETTE = ["#5865F2", "#7C4DFF", "#3B82F6", "#8B5CF6", "#6366F1",
           "#0EA5E9", "#A855F7", "#4F46E5", "#14B8A6", "#EC4899"]


def app_dir() -> Path:
    base = os.environ.get("APPDATA") or os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    d = Path(base) / "KioBotEX"
    d.mkdir(parents=True, exist_ok=True)
    return d


class Settings:
    DEFAULTS = {"nick": "", "volume": 80, "folder": "", "relay_url": "", "room_mode": "internet"}

    def __init__(self, path=None):
        self.path = Path(path) if path else app_dir() / "settings.json"
        self.data = dict(self.DEFAULTS)
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                self.data.update({k: loaded[k] for k in self.DEFAULTS if k in loaded})
        except (OSError, ValueError):
            pass
        if not self.data["relay_url"]:
            self.data["relay_url"] = P.DEFAULT_RELAY_URL
        if P.is_temporary_tunnel(self.data["relay_url"]):
            # sobra de teste manual: esse endereço já morreu e desligaria a conexão automática
            self["relay_url"] = ""

    def __getitem__(self, k):
        return self.data[k]

    def __setitem__(self, k, v):
        self.data[k] = v
        try:
            self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass


class Board:
    """Soundboard de uma pasta: ordem, nome, cor e imagem de cada botão."""
    FILE = "kiobot_ex_board.json"
    IMG_DIR = ".imagens"

    def __init__(self, folder):
        self.folder = Path(folder)
        self.entries = []        # {"file","name","color","image"}
        self.hidden = set()
        self.skipped = []        # (arquivo, motivo)
        self._hash_cache = {}
        self.load()

    # ---------- leitura/gravação ----------
    def load(self):
        self.entries, self.skipped = [], []
        saved = {}
        try:
            data = json.loads((self.folder / self.FILE).read_text(encoding="utf-8"))
            self.hidden = set(data.get("hidden", []))
            saved_items = data.get("items", [])
        except (OSError, ValueError):
            self.hidden, saved_items = set(), []
        on_disk = {p.name: p for p in self.folder.iterdir()
                   if p.is_file() and p.suffix.lower() in P.AUDIO_EXTS} if self.folder.is_dir() else {}
        for it in saved_items:
            fn = it.get("file")
            if fn in on_disk and fn not in self.hidden and self._playable(on_disk[fn]):
                self.entries.append({
                    "file": fn,
                    "name": P.clean_text(it.get("name"), P.MAX_NAME_LEN) or self._default_name(fn),
                    "color": it["color"] if P.valid_color(it.get("color")) else PALETTE[len(self.entries) % len(PALETTE)],
                    "image": it.get("image") if isinstance(it.get("image"), str) else None})
            saved[fn] = True
        for fn in sorted(on_disk, key=str.lower):
            if fn in saved or fn in self.hidden:
                continue
            if len(self.entries) >= P.MAX_SOUNDS:
                self.skipped.append((fn, f"limite de {P.MAX_SOUNDS} sons"))
                continue
            if self._playable(on_disk[fn]):
                self.entries.append({"file": fn, "name": self._default_name(fn),
                                     "color": PALETTE[len(self.entries) % len(PALETTE)], "image": None})
        self.save()

    def save(self):
        used = {e["image"] for e in self.entries if e["image"]}
        img_dir = self.folder / self.IMG_DIR
        if img_dir.is_dir():
            for f in img_dir.glob("*"):
                if f.name not in used:
                    f.unlink(missing_ok=True)
        try:
            (self.folder / self.FILE).write_text(
                json.dumps({"version": 1, "items": self.entries, "hidden": sorted(self.hidden)},
                           ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass

    @staticmethod
    def _default_name(fn):
        return P.clean_text(Path(fn).stem.replace("_", " ").replace("-", " "), P.MAX_NAME_LEN) or "Som"

    def _playable(self, path: Path):
        try:
            size = path.stat().st_size
            if size == 0 or size > P.MAX_SOUND_BYTES:
                self.skipped.append((path.name, "maior que 5 MB" if size else "arquivo vazio"))
                return False
            with open(path, "rb") as f:
                head = f.read(16)
            if not P.detect_audio(head):
                self.skipped.append((path.name, "formato não reconhecido"))
                return False
            return True
        except OSError:
            return False

    # ---------- edição ----------
    def add_files(self, paths):
        added = 0
        for src in paths:
            src = Path(src)
            if src.suffix.lower() not in P.AUDIO_EXTS or not src.is_file():
                continue
            dest = self.folder / src.name
            if dest.exists() and dest.resolve() != src.resolve():
                n = 2
                while (self.folder / f"{src.stem} ({n}){src.suffix}").exists():
                    n += 1
                dest = self.folder / f"{src.stem} ({n}){src.suffix}"
            if not dest.exists():
                shutil.copy2(src, dest)
            self.hidden.discard(dest.name)
            added += 1
        self.load()
        return added

    def remove(self, i):
        if 0 <= i < len(self.entries):
            self.hidden.add(self.entries.pop(i)["file"])
            self.save()

    def move(self, i, delta):
        j = i + delta
        if 0 <= i < len(self.entries) and 0 <= j < len(self.entries):
            self.entries[i], self.entries[j] = self.entries[j], self.entries[i]
            self.save()
            return j
        return i

    def rename(self, i, name):
        self.entries[i]["name"] = P.clean_text(name, P.MAX_NAME_LEN) or self._default_name(self.entries[i]["file"])
        self.save()

    def recolor(self, i, color):
        if P.valid_color(color):
            self.entries[i]["color"] = color
            self.save()

    def set_image(self, i, src):
        """Reduz a imagem (até 256x256), converte para PNG e guarda ao lado dos sons."""
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QImage
        try:
            if Path(src).stat().st_size > 20 * 1024 * 1024:
                return False
        except OSError:
            return False
        img = QImage(str(src))
        if img.isNull():
            return False
        d = self.folder / self.IMG_DIR
        d.mkdir(exist_ok=True)
        for side in (256, 192, 128, 96):
            small = img.scaled(side, side, Qt.AspectRatioMode.KeepAspectRatio,
                               Qt.TransformationMode.SmoothTransformation) if max(img.width(), img.height()) > side else img
            tmp = d / "_tmp.png"
            if not small.save(str(tmp), "PNG"):
                return False
            data = tmp.read_bytes()
            if len(data) <= P.MAX_IMAGE_BYTES:
                final = d / f"{P.content_id(data)}.png"
                tmp.replace(final)
                self.entries[i]["image"] = final.name
                self.save()
                return True
        tmp.unlink(missing_ok=True)
        return False

    def clear_image(self, i):
        self.entries[i]["image"] = None
        self.save()

    # ---------- o que vai para a rede ----------
    def _sha(self, path: Path):
        st = path.stat()
        key = (str(path), st.st_mtime_ns, st.st_size)
        if key not in self._hash_cache:
            self._hash_cache[key] = P.content_id(path.read_bytes())
        return self._hash_cache[key]

    def snapshot(self):
        """Retorna (manifest, paths) prontos para o HostSession."""
        manifest, paths = [], {}
        for e in self.entries:
            p = self.folder / e["file"]
            try:
                sid = self._sha(p)
                ext = P.detect_audio(p.read_bytes()[:16]) or p.suffix.lower()
                item = {"sid": sid, "ext": ext, "size": p.stat().st_size, "name": e["name"],
                        "color": e["color"], "iid": None, "isize": 0}
                paths[("snd", sid)] = p
                if e["image"]:
                    ip = self.folder / self.IMG_DIR / e["image"]
                    if ip.is_file():
                        iid = self._sha(ip)
                        item["iid"], item["isize"] = iid, ip.stat().st_size
                        paths[("img", iid)] = ip
                manifest.append(item)
            except OSError:
                continue
        return manifest, paths
