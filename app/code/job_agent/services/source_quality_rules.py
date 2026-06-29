from __future__ import annotations

from urllib.parse import urlparse

GENERIC_TITLE_LABELS = {
    "apply",
    "apply now",
    "all jobs",
    "browse jobs",
    "easy apply",
    "quick apply",
    "apply today",
    "contact us",
    "cookie policy",
    "cookie settings",
    "data protection officer",
    "details",
    "learn more",
    "more",
    "more info",
    "privacy policy",
    "reporting violations",
    "read more",
    "see details",
    "search jobs",
    "services",
    "sitemap",
    "terms of use",
    "view",
    "view all jobs",
    "view details",
    "view job",
    "view role",
}

NOISE_TEXT_TERMS = (
    "apply now",
    "services",
    "job search",
    "upload sap job",
    "improve my cv",
    "contract staffing",
    "filter",
    "reporting violations",
    "terms of use",
    "cookie policy",
    "privacy policy",
    "data protection officer",
    "sitemap",
    "newsletter",
)

NOISE_LINK_TERMS = (
    "all jobs",
    "apply now",
    "browse jobs",
    "easy apply",
    "quick apply",
    "#job-application",
    "find your plan",
    "find-your-plan",
    "login",
    "post a job",
    "sign up",
    "reporting violations",
    "terms of use",
    "cookie policy",
    "cookie settings",
    "privacy policy",
    "data protection officer",
    "sitemap",
    "search jobs",
    "view all jobs",
)

LISTING_PATH_LEAFS = {
    "all-jobs",
    "browse",
    "browse-jobs",
    "job-search",
    "jobs",
    "openings",
    "search",
    "vacancies",
}

LISTING_PATH_PAIRS = {
    ("careers", "search"),
    ("job", "search"),
    ("jobs", "browse"),
    ("jobs", "search"),
    ("openings", "search"),
    ("vacancies", "search"),
}

NON_JOB_URL_FRAGMENTS = (
    "/-/media/",
    "/media/",
    "/assets/",
    "/static/",
    "/find-your-plan",
    "/privacy",
    "/cookie",
    "/terms",
    "/sitemap",
    "/accessibility",
    "/contact",
)

NON_JOB_URL_EXTENSIONS = (
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".zip",
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".svg",
    ".webp",
    ".ico",
    ".css",
    ".js",
    ".woff",
    ".woff2",
    ".ttf",
)


def title_quality(title: str) -> str:
    normalized = normalize_text(title)
    if normalized in GENERIC_TITLE_LABELS:
        return "generic"
    if len(normalized) < 8:
        return "generic"
    return "useful"


def job_url_quality(url: str) -> str:
    normalized = url.lower().strip()
    if not normalized:
        return "missing"
    if is_non_job_url(normalized):
        return "non_job"
    return "job_like"


def is_non_job_url(url: str) -> bool:
    normalized = url.lower().strip()
    if any(fragment in normalized for fragment in NON_JOB_URL_FRAGMENTS):
        return True
    path = normalized.split("?", 1)[0].split("#", 1)[0]
    return any(path.endswith(extension) for extension in NON_JOB_URL_EXTENSIONS)


def is_probable_detail_url(url_or_path: str) -> bool:
    value = url_or_path.strip()
    if not value or is_non_job_url(value):
        return False
    path = _url_path(value).lower()
    segments = [segment for segment in path.strip("/").split("/") if segment]
    if not segments:
        return False
    if segments[-1] in LISTING_PATH_LEAFS:
        return False
    return len(segments) < 2 or (segments[-2], segments[-1]) not in LISTING_PATH_PAIRS


def link_text_is_noise(text: str, href: str) -> bool:
    normalized = normalize_text(f"{text} {href}")
    return any(term in normalized for term in NOISE_LINK_TERMS)


def text_has_noise_term(text: str) -> bool:
    normalized = normalize_text(text)
    return any(term in normalized for term in NOISE_TEXT_TERMS)


def normalize_text(value: str) -> str:
    return " ".join(value.lower().split())


def _url_path(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme or parsed.netloc:
        return parsed.path
    return value.split("?", 1)[0].split("#", 1)[0]
