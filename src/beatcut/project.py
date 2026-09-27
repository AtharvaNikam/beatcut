"""Project workspace: project.json + analysis/ + renders/. All paths the tool writes live under the project root."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .media import MediaError, probe_duration

GRADES = ("night", "natural", "none")
QUALITIES = ("share", "master")


class ProjectError(ValueError):
    pass


def parse_size(s: str) -> tuple[int, int]:
    try:
        w, h = (int(v) for v in s.lower().split("x"))
    except Exception as e:
        raise ProjectError(f"--size must look like 1080x1920, got {s!r}") from e
    if not (64 <= w <= 4096 and 64 <= h <= 4096) or w % 2 or h % 2:
        raise ProjectError("size must be even and between 64 and 4096 px")
    return w, h


@dataclass
class Project:
    root: Path
    footage: str
    song: str
    song_start: float = 0.0
    length: float = 0.0          # montage length in seconds (0 = rest of the song)
    width: int = 1080
    height: int = 1920
    fps: int = 30
    grade: str = "night"
    quality: str = "share"
    exclude: list = field(default_factory=list)   # glob patterns of files in the footage folder to ignore
    extra: dict = field(default_factory=dict)

    # ---- paths -------------------------------------------------------------
    @property
    def file(self) -> Path: return self.root / "project.json"
    @property
    def analysis(self) -> Path: return self.root / "analysis"
    @property
    def beats_path(self) -> Path: return self.analysis / "beats.json"
    @property
    def clips_path(self) -> Path: return self.analysis / "clips.json"
    @property
    def sheets(self) -> Path: return self.analysis / "sheets"
    @property
    def frames(self) -> Path: return self.analysis / "frames"
    @property
    def brief_path(self) -> Path: return self.analysis / "brief.md"
    @property
    def renders(self) -> Path: return self.root / "renders"
    @property
    def edl_path(self) -> Path: return self.root / "edl.json"

    def resolve(self, p: str | Path) -> Path:
        """Paths inside project.json may be relative to the project root."""
        p = Path(p).expanduser()
        return p if p.is_absolute() else (self.root / p).resolve()

    def in_root(self, name: str | Path, default_dir: Path | None = None) -> Path:
        """Resolve a user-supplied output/input name and refuse anything outside the project root (no ../ escapes)."""
        p = Path(name).expanduser()
        p = p if p.is_absolute() else ((default_dir or self.root) / p)
        p = p.resolve()
        root = self.root.resolve()
        if p != root and root not in p.parents:
            raise ProjectError(f"{name} resolves outside the project folder {root}")
        return p

    def out_video(self, name: str | None, suffix: str = "") -> Path:
        base = Path(name).name if name else f"montage{suffix}.mp4"   # basename only: renders always land in renders/
        if not base.lower().endswith(".mp4"):
            base += ".mp4"
        self.renders.mkdir(parents=True, exist_ok=True)
        return self.renders / base

    # ---- validation / io ----------------------------------------------------
    def validate(self) -> None:
        if not self.resolve(self.footage).is_dir():
            raise ProjectError(f"footage folder not found: {self.footage}")
        if not self.resolve(self.song).is_file():
            raise ProjectError(f"song not found: {self.song}")
        if self.grade not in GRADES:
            raise ProjectError(f"grade must be one of {GRADES}")
        if self.quality not in QUALITIES:
            raise ProjectError(f"quality must be one of {QUALITIES}")
        if not (12 <= self.fps <= 60):
            raise ProjectError("fps must be 12-60")
        if self.song_start < 0 or self.length < 0:
            raise ProjectError("song_start and length must be >= 0")

    def total_length(self) -> float:
        """Effective montage length: explicit length, else rest of the song from song_start."""
        dur = probe_duration(self.resolve(self.song)) - self.song_start
        if dur <= 0.5:
            raise ProjectError(f"song_start {self.song_start}s is past the end of the song")
        return min(self.length, dur) if self.length else dur

    def save(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        d = asdict(self)
        d.pop("root")
        self.file.write_text(json.dumps(d, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, root: str | Path) -> "Project":
        root = Path(root).expanduser().resolve()
        f = root / "project.json"
        if not f.is_file():
            raise ProjectError(f"no project.json in {root} — run `beatcut init {root} --footage DIR --song FILE` first")
        d = json.loads(f.read_text(encoding="utf-8-sig"))
        known = {k: d[k] for k in cls.__dataclass_fields__ if k in d and k != "root"}
        p = cls(root=root, **known)
        p.validate()
        return p

    @classmethod
    def create(cls, root: str | Path, footage: str, song: str, **kw) -> "Project":
        root = Path(root).expanduser().resolve()
        p = cls(root=root, footage=str(Path(footage).expanduser().resolve()), song=str(Path(song).expanduser().resolve()), **kw)
        p.validate()
        foot = Path(p.footage)
        if root == foot or foot in root.parents:
            raise ProjectError(f"the project folder must not be inside the footage folder {foot} (renders would mix with the clips)")
        try:
            p.total_length()
        except MediaError as e:
            raise ProjectError(f"cannot read song: {e}") from e
        for d in (p.root, p.analysis, p.renders):
            d.mkdir(parents=True, exist_ok=True)
        p.save()
        return p


def load_json(path: Path, what: str) -> dict:
    if not path.is_file():
        raise ProjectError(f"{what} missing ({path}) — run `beatcut analyze` first")
    return json.loads(path.read_text(encoding="utf-8-sig"))


def dumps(obj, **kw) -> str:
    """json.dumps that accepts numpy scalars/arrays."""
    return json.dumps(obj, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o), **kw)
