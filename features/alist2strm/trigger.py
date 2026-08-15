from collections.abc import Iterable

from .models import Alist2StrmTask
from .path_mapper import normalize_remote_path


def build_strm_triggers(
    tasks: Iterable[Alist2StrmTask],
    changed_paths: Iterable[str],
) -> list[dict]:
    normalized_paths = list(
        dict.fromkeys(str(normalize_remote_path(path)) for path in changed_paths)
    )
    triggers = []
    for task in tasks:
        source = normalize_remote_path(task.source_dir)
        matched_paths = []
        for path_value in normalized_paths:
            path = normalize_remote_path(path_value)
            try:
                path.relative_to(source)
            except ValueError:
                continue
            matched_paths.append(path_value)
        if matched_paths:
            triggers.append({"task_uuid": task.uuid, "paths": matched_paths})
    return triggers
