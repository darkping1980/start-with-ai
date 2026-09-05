"""Pandas 한글 문서 생성기와 검증기가 함께 사용하는 기능입니다."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterable

from bs4 import BeautifulSoup, Comment, Declaration, Doctype, ProcessingInstruction


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "pandas"
OUTPUT_ROOT = PROJECT_ROOT / "dist" / "pandas-ko"
CACHE_PATH = PROJECT_ROOT / ".translation-cache" / "translations.sqlite3"

SCOPES = (
    "getting_started",
    "user_guide",
    "reference",
    "generated",
    "development",
    "whatsnew",
    "all",
)

# 코드와 API signature는 파싱/직렬화 과정에서도 바뀌지 않도록 원문 조각째 보호합니다.
PROTECTED_ELEMENT_RE = re.compile(
    r"<(pre|script|style|kbd|samp|var|code)\b[^>]*>.*?</\1\s*>",
    re.IGNORECASE | re.DOTALL,
)
HEADERLINK_RE = re.compile(
    r"<a\b(?=[^>]*\bclass\s*=\s*(?:\"[^\"]*\bheaderlink\b[^\"]*\"|'[^']*\bheaderlink\b[^']*'))"
    r"[^>]*>.*?</a\s*>",
    re.IGNORECASE | re.DOTALL,
)
SIGNATURE_RE = re.compile(
    r"<dt\b(?=[^>]*\bclass\s*=\s*(?:\"[^\"]*\bsig\b[^\"]*\"|'[^']*\bsig\b[^']*'))"
    r"[^>]*>.*?</dt\s*>",
    re.IGNORECASE | re.DOTALL,
)
PROTECTED_COMMENT_RE = re.compile(r"<!--PANDAS_KO_PROTECTED_(\d{6})_([0-9a-f]{12})-->")

SKIP_TEXT_PARENTS = {
    "script",
    "style",
    "pre",
    "code",
    "kbd",
    "samp",
    "var",
    "svg",
    "math",
    "noscript",
}

KOREAN_TOKEN_RE = re.compile(r"[가-힣]+")
ENGLISH_TEXT_RE = re.compile(r"[A-Za-z]{2,}")


def html_files_for_scope(root: Path, scope: str) -> list[Path]:
    """scope에 해당하는 HTML 파일을 안정적인 순서로 반환합니다."""
    if scope == "all":
        return sorted(root.rglob("*.html"))
    return sorted((root / scope).rglob("*.html"))


def relative_docname(html_path: Path, root: Path) -> str:
    return html_path.relative_to(root).with_suffix("").as_posix()


def protect_html(raw_html: str) -> tuple[str, list[str]]:
    """절대 변경하면 안 되는 요소를 주석 토큰으로 치환합니다."""
    protected: list[str] = []

    def replace(match: re.Match[str]) -> str:
        import hashlib

        original = match.group(0)
        index = len(protected)
        protected.append(original)
        digest = hashlib.sha256(original.encode("utf-8")).hexdigest()[:12]
        return f"<!--PANDAS_KO_PROTECTED_{index:06d}_{digest}-->"

    # permalink는 Sphinx 구조이고, pre 안의 code/span은 바이트 단위로 보존합니다.
    result = HEADERLINK_RE.sub(replace, raw_html)
    result = PROTECTED_ELEMENT_RE.sub(replace, result)
    result = SIGNATURE_RE.sub(replace, result)
    return result, protected


def restore_html(serialized_html: str, protected: list[str]) -> str:
    """보호 토큰을 원본 바이트 문자열로 되돌립니다."""
    found: set[int] = set()

    def replace(match: re.Match[str]) -> str:
        import hashlib

        index = int(match.group(1))
        if index >= len(protected):
            raise ValueError(f"알 수 없는 보호 토큰: {index}")
        original = protected[index]
        digest = hashlib.sha256(original.encode("utf-8")).hexdigest()[:12]
        if digest != match.group(2):
            raise ValueError(f"보호 토큰 checksum 불일치: {index}")
        found.add(index)
        return original

    # API signature 안에 headerlink/code 토큰이 중첩될 수 있어 바깥쪽부터 반복 복원합니다.
    restored = serialized_html
    for _ in range(len(protected) + 1):
        restored, replacement_count = PROTECTED_COMMENT_RE.subn(replace, restored)
        if replacement_count == 0:
            break
    else:
        raise ValueError("보호 요소의 중첩 복원이 종료되지 않았습니다.")
    expected = set(range(len(protected)))
    if found != expected:
        missing = sorted(expected - found)
        raise ValueError(f"복원되지 않은 보호 요소: {missing[:10]}")
    return restored


def translatable_nodes(soup: BeautifulSoup) -> list:
    """사용자가 읽는 영문 텍스트 노드만 골라냅니다."""
    nodes = []
    for node in soup.find_all(string=True):
        if isinstance(node, (Comment, Declaration, Doctype, ProcessingInstruction)) or node.parent is None:
            continue
        if node.parent.name in SKIP_TEXT_PARENTS:
            continue
        if node.find_parent(SKIP_TEXT_PARENTS):
            continue
        text = str(node)
        if ENGLISH_TEXT_RE.search(text) and text.strip():
            nodes.append(node)
    return nodes


def split_outer_whitespace(text: str) -> tuple[str, str, str]:
    leading = text[: len(text) - len(text.lstrip())]
    trailing = text[len(text.rstrip()) :]
    core_end = len(text) - len(trailing) if trailing else len(text)
    return leading, text[len(leading) : core_end], trailing


def load_search_index(path: Path) -> dict:
    raw = path.read_text(encoding="utf-8")
    prefix = "Search.setIndex("
    if not raw.startswith(prefix) or not raw.rstrip().endswith(")"):
        raise ValueError(f"지원하지 않는 Sphinx searchindex 형식: {path}")
    payload = raw[len(prefix) : raw.rfind(")")]
    return json.loads(payload)


def save_search_index(path: Path, index: dict) -> None:
    payload = json.dumps(index, ensure_ascii=False, separators=(",", ":"))
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(f"Search.setIndex({payload})", encoding="utf-8")
    temporary.replace(path)


def as_doc_ids(value: int | list[int] | None) -> set[int]:
    if value is None:
        return set()
    if isinstance(value, int):
        return {value}
    return set(value)


def compact_doc_ids(values: Iterable[int]) -> int | list[int]:
    ids = sorted(set(values))
    if len(ids) == 1:
        return ids[0]
    return ids


# 동의어는 API 이름/가이드 문서명과 의미가 명확히 연결되는 경우만 등록합니다.
SYNONYM_TARGETS: dict[str, tuple[str, ...]] = {
    "결측치": ("dropna", "fillna", "isna", "notna", "missing_data"),
    "누락값": ("dropna", "fillna", "isna", "notna", "missing_data"),
    "빈값": ("dropna", "fillna", "isna", "notna", "missing_data"),
    "제거": ("dropna", "drop_duplicates"),
    "채우기": ("fillna", "ffill", "bfill"),
    "데이터프레임": ("pandas.dataframe", "01_table_oriented"),
    "데이터": ("08_combine_dataframes", "merge", "join", "concat"),
    "프레임": ("pandas.dataframe", "01_table_oriented"),
    "피벗": ("pivot", "07_reshape_table_layout"),
    "테이블": ("pivot_table", "07_reshape_table_layout"),
    "그룹화": ("groupby", "06_calculate_statistics"),
    "그룹바이": ("groupby", "06_calculate_statistics"),
    "합치기": ("merge", "join", "concat", "08_combine_dataframes"),
    "병합": ("merge", "join", "concat", "08_combine_dataframes"),
    "정렬": ("sort_values", "sort_index", "sorting"),
    "인덱스": ("indexing", "pandas.index"),
    "열": ("03_subset_data", "05_add_columns"),
    "행": ("03_subset_data",),
    "컬럼": ("03_subset_data", "05_add_columns"),
    "필터": ("03_subset_data", "filter"),
    "조건": ("03_subset_data", "query"),
    "검색": ("03_subset_data", "query"),
    "문자열": ("10_text_data", "string"),
    "날짜": ("09_timeseries", "datetime", "to_datetime"),
    "시간": ("09_timeseries", "datetime", "timedelta"),
    "중복": ("duplicated", "drop_duplicates"),
    "csv": ("read_csv", "to_csv", "02_read_write"),
    "읽기": ("read_csv", "read_excel", "02_read_write"),
    "저장": ("to_csv", "to_excel", "02_read_write"),
    "엑셀": ("read_excel", "to_excel", "02_read_write"),
    "평균": ("mean", "06_calculate_statistics"),
    "중앙값": ("median", "06_calculate_statistics"),
    "집계": ("aggregate", "agg", "groupby", "06_calculate_statistics"),
}


def matching_doc_ids(index: dict, patterns: Iterable[str]) -> set[int]:
    matches: set[int] = set()
    lowered_patterns = tuple(pattern.lower() for pattern in patterns)
    for doc_id, (docname, title) in enumerate(zip(index["docnames"], index["titles"])):
        haystack = f"{docname} {title}".lower()
        if any(pattern in haystack for pattern in lowered_patterns):
            matches.add(doc_id)
    return matches


def add_korean_search_terms(index: dict, translated_root: Path, html_files: Iterable[Path]) -> dict[str, int]:
    """번역 본문 토큰과 한글 동의어를 기존 Sphinx index에 추가합니다."""
    doc_ids = {name: i for i, name in enumerate(index["docnames"])}
    body_additions = 0
    title_additions = 0

    for html_path in html_files:
        docname = relative_docname(html_path, translated_root)
        doc_id = doc_ids.get(docname)
        if doc_id is None:
            continue
        soup = BeautifulSoup(html_path.read_text(encoding="utf-8"), "html.parser")
        main = soup.find(attrs={"role": "main"}) or soup.body or soup
        tokens = set(KOREAN_TOKEN_RE.findall(main.get_text(" ")))
        for token in tokens:
            if len(token) < 2:
                continue
            current = as_doc_ids(index["terms"].get(token))
            if doc_id not in current:
                current.add(doc_id)
                index["terms"][token] = compact_doc_ids(current)
                body_additions += 1

        heading = main.find("h1")
        if heading:
            korean_title = " ".join(heading.get_text(" ", strip=True).split())
            if korean_title:
                index["titles"][doc_id] = korean_title
                for token in set(KOREAN_TOKEN_RE.findall(korean_title)):
                    if len(token) < 2:
                        continue
                    current = as_doc_ids(index["titleterms"].get(token))
                    if doc_id not in current:
                        current.add(doc_id)
                        index["titleterms"][token] = compact_doc_ids(current)
                        title_additions += 1

    synonym_additions = 0
    for term, patterns in SYNONYM_TARGETS.items():
        targets = matching_doc_ids(index, patterns)
        if not targets:
            continue
        current = as_doc_ids(index["terms"].get(term.lower()))
        before = len(current)
        current.update(targets)
        index["terms"][term.lower()] = compact_doc_ids(current)
        synonym_additions += len(current) - before

    return {
        "body_terms": body_additions,
        "title_terms": title_additions,
        "synonym_links": synonym_additions,
    }
