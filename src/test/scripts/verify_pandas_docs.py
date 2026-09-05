"""생성된 Pandas 한글 HTML, 리소스, 코드 보존, 한영 검색을 자동 검증합니다."""

from __future__ import annotations

import argparse
import filecmp
import os
import re
import sys
import urllib.parse
from collections import Counter
from pathlib import Path

from bs4 import BeautifulSoup

from pandas_docs_common import (
    ENGLISH_TEXT_RE,
    OUTPUT_ROOT,
    SCOPES,
    SOURCE_ROOT,
    as_doc_ids,
    html_files_for_scope,
    load_search_index,
    protect_html,
    split_outer_whitespace,
    translatable_nodes,
)


SEARCH_CASES: dict[str, tuple[str, ...]] = {
    "결측치": ("dropna", "fillna", "isna", "missing"),
    "결측치 제거": ("dropna",),
    "데이터프레임": ("dataframe", "01_table_oriented"),
    "피벗 테이블": ("pivot", "07_reshape_table_layout"),
    "그룹화": ("groupby", "06_calculate_statistics"),
    "데이터 합치기": ("merge", "join", "concat", "08_combine_dataframes"),
    "CSV 읽기": ("read_csv", "02_read_write"),
    "dropna": ("dropna",),
    "fillna": ("fillna",),
    "pivot_table": ("pivot_table",),
    "groupby": ("groupby",),
    "merge": ("merge",),
    "read_csv": ("read_csv",),
}

ENGLISH_STEMS = {
    "merge": "merg",
    "pivot_table": "pivot_t",
}

CRITICAL_ATTRIBUTES = ("id", "class", "href", "src")
LOCAL_SCHEMES = {"", "file"}


class Verification:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.stats: Counter = Counter()

    def error(self, message: str) -> None:
        self.errors.append(message)

    def warning(self, message: str) -> None:
        self.warnings.append(message)


def normalized_attributes(soup: BeautifulSoup) -> list[tuple[str, tuple]]:
    result = []
    for tag in soup.find_all(True):
        attributes = []
        for name in CRITICAL_ATTRIBUTES:
            value = tag.attrs.get(name)
            if isinstance(value, list):
                value = tuple(value)
            attributes.append((name, value))
        result.append((tag.name, tuple(attributes)))
    return result


def attribute_inventory(soup: BeautifulSoup) -> Counter:
    return Counter(normalized_attributes(soup))


def verify_file_pair(source: Path, output: Path, result: Verification) -> None:
    relative = source.relative_to(SOURCE_ROOT).as_posix()
    if not output.is_file():
        result.error(f"번역 HTML 누락: {relative}")
        return

    source_raw = source.read_text(encoding="utf-8")
    output_raw = output.read_text(encoding="utf-8")
    source_doctype = re.search(r"<!DOCTYPE[^>]*>", source_raw, re.IGNORECASE)
    output_doctype = re.search(r"<!DOCTYPE[^>]*>", output_raw, re.IGNORECASE)
    if (source_doctype.group(0) if source_doctype else None) != (
        output_doctype.group(0) if output_doctype else None
    ):
        result.error(f"DOCTYPE 변경 또는 누락: {relative}")
    _, source_protected = protect_html(source_raw)
    _, output_protected = protect_html(output_raw)
    if Counter(source_protected) != Counter(output_protected):
        result.error(f"code/pre/script/style/kbd/samp/var/API signature 변경: {relative}")
    else:
        result.stats["protected_files_ok"] += 1

    source_soup = BeautifulSoup(source_raw, "html.parser")
    output_soup = BeautifulSoup(output_raw, "html.parser")
    if output_soup.html is None or output_soup.body is None:
        result.error(f"HTML parsing/구조 오류: {relative}")
    for generated_link in output_soup.select('link[data-pandas-ko="responsive"]'):
        generated_link.extract()
    if attribute_inventory(source_soup) != attribute_inventory(output_soup):
        result.error(f"HTML tag 또는 id/class/href/src 개수 변경: {relative}")
    else:
        result.stats["attributes_ok"] += 1

    # main 안에 긴 영문 설명만 남은 경우를 찾아 번역 누락 후보로 보고합니다.
    main = output_soup.find(attrs={"role": "main"}) or output_soup.body
    if main:
        candidates = []
        for node in translatable_nodes(main):
            _, core, _ = split_outer_whitespace(str(node))
            english_words = ENGLISH_TEXT_RE.findall(core)
            if len(core) >= 80 and len(english_words) >= 10 and not re.search(r"[가-힣]", core):
                candidates.append(" ".join(core.split())[:140])
        if candidates:
            result.warning(f"주요 영문 설명 잔존 {relative}: {len(candidates)}개, 예: {candidates[0]}")
            result.stats["untranslated_candidates"] += len(candidates)


def local_target(page: Path, url: str) -> tuple[Path | None, str]:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme.lower() not in LOCAL_SCHEMES or parsed.netloc:
        return None, ""
    path_text = urllib.parse.unquote(parsed.path)
    if not path_text or path_text.startswith("/"):
        return None, parsed.fragment
    target = (page.parent / path_text).resolve()
    return target, urllib.parse.unquote(parsed.fragment)


def verify_links(files: list[Path], result: Verification) -> None:
    anchor_cache: dict[Path, set[str]] = {}
    output_resolved = OUTPUT_ROOT.resolve()
    for page in files:
        soup = BeautifulSoup(page.read_text(encoding="utf-8"), "html.parser")
        references: list[tuple[str, str]] = []
        references.extend(("href", tag.get("href", "")) for tag in soup.find_all(href=True))
        references.extend(("src", tag.get("src", "")) for tag in soup.find_all(src=True))
        for attribute, url in references:
            target, fragment = local_target(page, url)
            if target is None:
                continue
            try:
                target.relative_to(output_resolved)
            except ValueError:
                result.error(f"문서 루트 밖 내부 링크: {page.relative_to(OUTPUT_ROOT)} -> {url}")
                continue
            if not target.exists():
                result.error(f"깨진 {attribute}: {page.relative_to(OUTPUT_ROOT)} -> {url}")
                continue
            result.stats["local_links_ok"] += 1
            if fragment and target.suffix.lower() in {".html", ".htm"}:
                if target not in anchor_cache:
                    target_soup = BeautifulSoup(target.read_text(encoding="utf-8"), "html.parser")
                    anchor_cache[target] = {
                        str(tag["id"]) for tag in target_soup.find_all(id=True)
                    }
                if fragment not in anchor_cache[target]:
                    result.error(
                        f"깨진 HTML anchor: {page.relative_to(OUTPUT_ROOT)} -> {url}"
                    )


def verify_copied_resources(result: Verification) -> None:
    """번역 대상이 아닌 정적 파일과 _sources가 원본 그대로인지 확인합니다."""
    for source in SOURCE_ROOT.rglob("*"):
        if not source.is_file():
            continue
        relative = source.relative_to(SOURCE_ROOT)
        if source.suffix.lower() == ".html" or relative.as_posix() == "searchindex.js":
            continue
        output = OUTPUT_ROOT / relative
        if not output.is_file():
            result.error(f"복사 리소스 누락: {relative.as_posix()}")
            continue
        if not filecmp.cmp(source, output, shallow=False):
            result.error(f"복사 리소스 내용 변경: {relative.as_posix()}")
            continue
        result.stats["copied_resources_ok"] += 1


def docs_for_term(index: dict, term: str) -> set[int]:
    terms = index["terms"]
    titleterms = index["titleterms"]
    candidates = [term, ENGLISH_STEMS.get(term, term)]
    found: set[int] = set()
    for candidate in candidates:
        found.update(as_doc_ids(terms.get(candidate)))
        found.update(as_doc_ids(titleterms.get(candidate)))
    if found:
        return found
    if len(term) > 2:
        for indexed_term, doc_ids in terms.items():
            if term in indexed_term:
                found.update(as_doc_ids(doc_ids))
        for indexed_term, doc_ids in titleterms.items():
            if term in indexed_term:
                found.update(as_doc_ids(doc_ids))
    return found


def search(index: dict, query: str) -> list[str]:
    query_terms = re.findall(r"\w+", query.lower(), flags=re.UNICODE)
    sets = [docs_for_term(index, term) for term in query_terms]
    if not sets or any(not values for values in sets):
        return []
    matches = set.intersection(*sets)
    return [index["docnames"][doc_id] for doc_id in sorted(matches)]


def verify_search(result: Verification) -> None:
    path = OUTPUT_ROOT / "searchindex.js"
    if not path.is_file():
        result.error("searchindex.js 누락")
        return
    try:
        index = load_search_index(path)
    except (ValueError, TypeError) as error:
        result.error(f"searchindex.js parsing 오류: {error}")
        return

    for query, expected_patterns in SEARCH_CASES.items():
        docs = search(index, query)
        related = [
            doc for doc in docs if any(pattern in doc.lower() for pattern in expected_patterns)
        ]
        if not related:
            result.error(f"검색 실패: {query!r} -> 관련 문서 없음 (전체 결과 {len(docs)}개)")
        else:
            result.stats["search_cases_ok"] += 1
            print(f"  검색 OK {query!r}: {related[0]}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scope", choices=SCOPES, default="getting_started")
    parser.add_argument(
        "--strict-english",
        action="store_true",
        help="긴 영문 설명 잔존 후보가 있으면 warning 대신 실패 처리합니다.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = Verification()
    if not OUTPUT_ROOT.is_dir():
        print(f"오류: 번역 결과 폴더가 없습니다: {OUTPUT_ROOT}", file=sys.stderr)
        return 2

    source_all_count = sum(1 for _ in SOURCE_ROOT.rglob("*.html"))
    output_all_count = sum(1 for _ in OUTPUT_ROOT.rglob("*.html"))
    print(f"HTML 파일 수: 원본 {source_all_count}, 번역본 {output_all_count}")
    if source_all_count != output_all_count:
        result.error(f"전체 HTML 파일 수 불일치: {source_all_count} != {output_all_count}")

    source_files = html_files_for_scope(SOURCE_ROOT, args.scope)
    output_files = []
    print(f"보호 영역/속성 검사: {len(source_files)}개")
    for source in source_files:
        output = OUTPUT_ROOT / source.relative_to(SOURCE_ROOT)
        verify_file_pair(source, output, result)
        if output.is_file():
            output_files.append(output)

    print("내부 링크와 CSS/JS/이미지 검사")
    verify_links(output_files, result)
    print("정적 파일과 _sources 원본 일치 검사")
    verify_copied_resources(result)
    print("한국어 + 영어 검색 검사")
    verify_search(result)

    if args.strict_english and result.stats["untranslated_candidates"]:
        result.error(
            f"주요 영문 설명 잔존 후보: {result.stats['untranslated_candidates']}개"
        )

    print("\n검증 통계")
    for name, value in sorted(result.stats.items()):
        print(f"  {name}: {value}")
    if result.warnings:
        print(f"\n경고 {len(result.warnings)}개")
        for warning in result.warnings[:20]:
            print(f"  - {warning}")
        if len(result.warnings) > 20:
            print(f"  ... {len(result.warnings) - 20}개 생략")
    if result.errors:
        print(f"\n실패 {len(result.errors)}개", file=sys.stderr)
        for error in result.errors[:50]:
            print(f"  - {error}", file=sys.stderr)
        if len(result.errors) > 50:
            print(f"  ... {len(result.errors) - 50}개 생략", file=sys.stderr)
        return 1
    print("\n검증 성공")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
