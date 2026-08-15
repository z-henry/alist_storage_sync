from pathlib import Path, PurePosixPath


VIDEO_EXTENSIONS = frozenset(
    {
        ".mp4",
        ".mkv",
        ".flv",
        ".avi",
        ".wmv",
        ".ts",
        ".m2ts",
        ".rmvb",
        ".webm",
    }
)
SUBTITLE_EXTENSIONS = frozenset({".ass", ".srt", ".ssa", ".sub", ".vtt"})
IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".webp"})
NFO_EXTENSIONS = frozenset({".nfo"})


def normalize_remote_path(value: str) -> PurePosixPath:
    path = PurePosixPath("/" + value.strip("/"))
    if ".." in path.parts:
        raise ValueError(f"Remote path must not contain '..': {value}")
    return path


def resolve_output_base(output_root: str | Path, target_dir: str) -> Path:
    if not target_dir or Path(target_dir).is_absolute():
        raise ValueError("target_dir must be a non-empty path relative to STRM_OUTPUT_ROOT")
    if ".." in Path(target_dir).parts:
        raise ValueError("target_dir must not contain '..'")

    root = Path(output_root).resolve(strict=False)
    target = (root / target_dir).resolve(strict=False)
    if target != root and root not in target.parents:
        raise ValueError("target_dir escapes STRM_OUTPUT_ROOT")
    return target


def map_local_path(
    output_base: Path,
    source_dir: str,
    remote_path: str,
    flatten_mode: bool,
    video_extensions: frozenset[str] = VIDEO_EXTENSIONS,
) -> Path:
    source = normalize_remote_path(source_dir)
    remote = normalize_remote_path(remote_path)
    try:
        relative = remote.relative_to(source)
    except ValueError as error:
        raise ValueError(f"Remote path {remote} is outside source directory {source}") from error

    if not relative.parts:
        raise ValueError(f"Remote path does not identify a file: {remote}")
    local = output_base / (relative.name if flatten_mode else Path(*relative.parts))
    if remote.suffix.lower() in video_extensions:
        local = local.with_suffix(".strm")
    return local
