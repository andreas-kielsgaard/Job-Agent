from __future__ import annotations

from pathlib import Path


def find_repo_root(start: Path) -> Path:
    """Find the checkout root from either the legacy or app/code package layout."""
    for parent in [start.resolve(), *start.resolve().parents]:
        if (parent / "README.md").exists() and (
            (parent / "job_agent").exists() or (parent / "app" / "code" / "job_agent").exists()
        ):
            return parent
    return start.resolve().parents[1]


def uses_new_layout(root: Path) -> bool:
    root = Path(root)
    return (root / "app").exists() and (root / "setup").exists()


def user_dir(root: Path) -> Path:
    return Path(root) / "user" if uses_new_layout(root) else Path(root)


def runtime_dir(root: Path) -> Path:
    return Path(root) / "runtime" if uses_new_layout(root) else Path(root)


def profile_dir(root: Path) -> Path:
    return user_dir(root) / "profile" if uses_new_layout(root) else Path(root) / "profile"


def profile_defaults_dir(root: Path) -> Path:
    new_path = Path(root) / "setup" / "defaults" / "profile"
    return new_path if new_path.exists() or uses_new_layout(root) else Path(root) / "profile.example"


def profile_input_dir(root: Path) -> Path:
    private = profile_dir(root)
    return private if private.exists() else profile_defaults_dir(root)


def uploads_dir(root: Path) -> Path:
    return user_dir(root) / "uploads" if uses_new_layout(root) else profile_dir(root) / "files"


def cv_upload_dir(root: Path) -> Path:
    return uploads_dir(root) / "cv" if uses_new_layout(root) else uploads_dir(root)


def env_file(root: Path) -> Path:
    return user_dir(root) / ".env" if uses_new_layout(root) else Path(root) / ".env"


def env_defaults_file(root: Path) -> Path:
    new_path = Path(root) / "setup" / "defaults" / ".env.example"
    return new_path if new_path.exists() or uses_new_layout(root) else Path(root) / ".env.example"


def sources_dir(root: Path) -> Path:
    return user_dir(root) / "sources" if uses_new_layout(root) else Path(root) / "sources"


def source_defaults_dir(root: Path) -> Path:
    new_path = Path(root) / "setup" / "defaults" / "sources"
    return new_path if new_path.exists() or uses_new_layout(root) else Path(root) / "sources"


def recipes_dir(root: Path) -> Path:
    return sources_dir(root) / "recipes"


def runtime_jobs_dir(root: Path) -> Path:
    return runtime_dir(root) / "jobs" if uses_new_layout(root) else Path(root) / "jobs"


def sample_jobs_dir(root: Path) -> Path:
    new_path = Path(root) / "app" / "resources" / "jobs"
    return new_path if new_path.exists() or uses_new_layout(root) else Path(root) / "jobs"


def output_dir(root: Path) -> Path:
    return runtime_dir(root) / "output" if uses_new_layout(root) else Path(root) / "output"


def templates_dir(root: Path) -> Path:
    new_path = Path(root) / "app" / "resources" / "templates"
    return new_path if new_path.exists() or uses_new_layout(root) else Path(root) / "templates"


def prompts_dir(root: Path) -> Path:
    new_path = Path(root) / "app" / "resources" / "prompts"
    return new_path if new_path.exists() or uses_new_layout(root) else Path(root) / "prompts"


def venv_dir(root: Path) -> Path:
    return Path(root) / "app" / "environment" / ".venv" if uses_new_layout(root) else Path(root) / ".venv"


def requirements_file(root: Path, name: str = "requirements.txt") -> Path:
    new_path = Path(root) / "app" / "environment" / name
    return new_path if new_path.exists() or uses_new_layout(root) else Path(root) / name


def resolve_project_path(root: Path, path: str | Path) -> Path:
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate
    parts = candidate.parts
    if not parts:
        return Path(root)
    head = parts[0]
    tail = Path(*parts[1:]) if len(parts) > 1 else Path()
    mapped_roots = {
        "profile": profile_dir(root),
        "profile.example": profile_defaults_dir(root),
        "sources": sources_dir(root),
        "jobs": runtime_jobs_dir(root),
        "output": output_dir(root),
        "templates": templates_dir(root),
        "prompts": prompts_dir(root),
    }
    if head == "jobs":
        runtime_candidate = runtime_jobs_dir(root) / tail
        if runtime_candidate.exists() or not (sample_jobs_dir(root) / tail).exists():
            return runtime_candidate
        return sample_jobs_dir(root) / tail
    if head in mapped_roots:
        mapped = mapped_roots[head] / tail
        if mapped.exists() or not uses_new_layout(root):
            return mapped
        if head == "sources":
            default_candidate = source_defaults_dir(root) / tail
            if default_candidate.exists():
                return default_candidate
        return mapped
    return Path(root) / candidate


def display_path(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(Path(root).resolve()).as_posix()
    except ValueError:
        return str(path)
