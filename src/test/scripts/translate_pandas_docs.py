"""Pandas Sphinx HTML을 한국어로 번역하고 한영 검색 index를 만듭니다.

예시:
    uv run python scripts/translate_pandas_docs.py --scope getting_started
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import random
import re
import shutil
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ElementTree
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from bs4 import BeautifulSoup, Comment

from pandas_docs_common import (
    CACHE_PATH,
    OUTPUT_ROOT,
    SCOPES,
    SOURCE_ROOT,
    add_korean_search_terms,
    html_files_for_scope,
    load_search_index,
    protect_html,
    PROTECTED_COMMENT_RE,
    restore_html,
    save_search_index,
    split_outer_whitespace,
    translatable_nodes,
)


DEEPL_FREE_URL = "https://api-free.deepl.com/v2/translate"
DEEPL_PRO_URL = "https://api.deepl.com/v2/translate"
MAX_BATCH_ITEMS = 40
MAX_BATCH_CHARACTERS = 70_000
BLOCK_TAGS = {"title", "h1", "h2", "h3", "h4", "h5", "h6", "p", "dt", "dd", "th", "td", "caption", "figcaption"}
ATOMIC_INLINE_TAGS = {"a", "span", "em", "strong", "img", "br", "sup", "sub"}

# API 명칭과 코드 모양의 토큰이 일반 문장 속에 있어도 그대로 유지합니다.
PROTECTED_TERM_RE = re.compile(
    r"https?://[^\s<>]+"
    r"|\b(?:pandas|NumPy|numpy|Python|DataFrame|Series|Index|MultiIndex|GroupBy|"
    r"Categorical|Timestamp|Timedelta|Period|ExtensionArray|NA|NaN|NaT)\b"
    r"|\b[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+\b"
    r"|\b[A-Za-z_][A-Za-z0-9_]*\(\)"
    r"|\b[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]+\b"
)


class TranslationCache:
    """문장별 번역을 즉시 저장해 중단 후 이어서 실행할 수 있게 합니다."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS translations (
                source_hash TEXT PRIMARY KEY,
                source_text TEXT NOT NULL,
                translated_text TEXT NOT NULL,
                status TEXT NOT NULL,
                source_file TEXT,
                engine TEXT NOT NULL,
                translated_at TEXT NOT NULL
            )
            """
        )
        self.connection.commit()

    @staticmethod
    def key(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def get(self, text: str) -> str | None:
        row = self.connection.execute(
            "SELECT translated_text FROM translations WHERE source_hash = ? AND status = 'done'",
            (self.key(text),),
        ).fetchone()
        return row[0] if row else None

    def put(self, source: str, translated: str, source_file: str, engine: str) -> None:
        self.connection.execute(
            """
            INSERT INTO translations
                (source_hash, source_text, translated_text, status, source_file, engine, translated_at)
            VALUES (?, ?, ?, 'done', ?, ?, ?)
            ON CONFLICT(source_hash) DO UPDATE SET
                translated_text=excluded.translated_text,
                status='done',
                source_file=excluded.source_file,
                engine=excluded.engine,
                translated_at=excluded.translated_at
            """,
            (
                self.key(source),
                source,
                translated,
                source_file,
                engine,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()


def protect_terms(text: str) -> tuple[str, list[str]]:
    """API/식별자를 DeepL의 XML ignore tag로 감싸 원문 그대로 유지합니다."""
    parts = ["<pandasroot>"]
    originals: list[str] = []
    position = 0
    for match in PROTECTED_TERM_RE.finditer(text):
        parts.append(html.escape(text[position : match.start()], quote=False))
        original = match.group(0)
        originals.append(original)
        parts.append(f"<keep>{html.escape(original, quote=False)}</keep>")
        position = match.end()
    parts.append(html.escape(text[position:], quote=False))
    parts.append("</pandasroot>")
    return "".join(parts), originals


def restore_terms(text: str, originals: list[str]) -> str:
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError as error:
        raise ValueError("DeepL XML 응답을 parsing할 수 없습니다.") from error
    if root.tag != "pandasroot":
        raise ValueError(f"DeepL XML root가 변경됐습니다: {root.tag}")
    returned = [element.text or "" for element in root.iter("keep")]
    if returned != originals:
        raise ValueError("DeepL 응답에서 보호한 API/식별자가 변경됐습니다.")
    return "".join(root.itertext())


class DeepLTranslator:
    def __init__(self, api_key: str, endpoint: str, timeout: int = 60) -> None:
        self.api_key = api_key
        self.endpoint = endpoint
        self.timeout = timeout

    def _request(self, texts: list[str], extra_fields: list[tuple[str, str]]) -> list[str]:
        fields: list[tuple[str, str]] = [("text", text) for text in texts]
        fields.extend(
            [
                ("source_lang", "EN"),
                ("target_lang", "KO"),
                ("preserve_formatting", "1"),
            ]
        )
        fields.extend(extra_fields)
        body = urllib.parse.urlencode(fields).encode("utf-8")
        request = urllib.request.Request(
            self.endpoint,
            data=body,
            headers={
                "Authorization": f"DeepL-Auth-Key {self.api_key}",
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": "pandas-ko-docs/0.1",
            },
            method="POST",
        )

        for attempt in range(1, 6):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                translated = [item["text"] for item in payload["translations"]]
                if len(translated) != len(texts):
                    raise RuntimeError("DeepL 응답 개수가 요청 개수와 다릅니다.")
                return translated
            except urllib.error.HTTPError as error:
                detail = error.read().decode("utf-8", errors="replace")[:500]
                if error.code not in {429, 456, 500, 502, 503, 504} or attempt == 5:
                    raise RuntimeError(f"DeepL HTTP {error.code}: {detail}") from error
            except (urllib.error.URLError, TimeoutError) as error:
                if attempt == 5:
                    raise RuntimeError(f"DeepL 네트워크 오류: {error}") from error
            delay = min(2**attempt + random.random(), 30)
            print(f"  API 재시도 {attempt}/5: {delay:.1f}초 후", flush=True)
            time.sleep(delay)
        raise AssertionError("도달할 수 없는 코드")

    def translate_batch(self, texts: list[str]) -> list[str]:
        protected_texts: list[str] = []
        replacements: list[list[tuple[str, str]]] = []
        for text in texts:
            protected, saved = protect_terms(text)
            protected_texts.append(protected)
            replacements.append(saved)

        translated = self._request(
            protected_texts,
            [("tag_handling", "xml"), ("ignore_tags", "keep")],
        )
        return [restore_terms(result, saved) for result, saved in zip(translated, replacements)]

    def translate_html_batch(self, fragments: list[str]) -> list[str]:
        wrapped = [f"<pandasroot>{fragment}</pandasroot>" for fragment in fragments]
        translated = self._request(
            wrapped,
            [("tag_handling", "xml"), ("ignore_tags", "keep")],
        )
        results = []
        for value in translated:
            try:
                root = ElementTree.fromstring(value)
            except ElementTree.ParseError as error:
                raise ValueError("DeepL HTML fragment 응답을 parsing할 수 없습니다.") from error
            if root.tag != "pandasroot":
                raise ValueError(f"DeepL HTML root가 변경됐습니다: {root.tag}")
            inner = root.text or ""
            inner += "".join(ElementTree.tostring(child, encoding="unicode") for child in root)
            results.append(inner)
        return results


OLLAMA_SYSTEM_PROMPT = """You translate Pandas official technical documentation from English to Korean.
Translate only human-readable explanatory prose naturally and accurately.
Return only the translated content, with no explanation, commentary, Markdown fences, or quotation.
Never translate or alter Python/Pandas code, API signatures, function/class/method/parameter names,
DataFrame, Series, Index, GroupBy, identifiers, file paths, URLs, HTML/XML tags, attributes,
or anything inside <keep>...</keep>. Preserve every XML tag and its attributes exactly.
Keep technical product names such as pandas, Python, NumPy, CSV, SQL, JSON and Parquet unchanged.
"""


class OllamaTranslator:
    """localhost Ollama의 qwen 모델을 사용하는 DeepL 대체 번역기입니다."""

    engine = "ollama-qwen2.5:3b"

    def __init__(self, base_url: str = "http://localhost:11434", model: str = "qwen2.5:3b") -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = 120
        # CPU 과열을 피하기 위해 논리 프로세서의 약 80%만 Ollama에 사용합니다.
        self.num_thread = max(1, int((os.cpu_count() or 1) * 0.8))
        self.errors: list[str] = []

    def _chat(self, content: str, kind: str, json_mode: bool = False) -> str | None:
        payload = json.dumps(
            {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": OLLAMA_SYSTEM_PROMPT},
                    {"role": "user", "content": content},
                ],
                "stream": False,
                "options": {"temperature": 0.1, "num_thread": self.num_thread},
            },
            ensure_ascii=False,
        ).encode("utf-8")
        if json_mode:
            payload = json.dumps(
                {
                    **json.loads(payload.decode("utf-8")),
                    "format": "json",
                },
                ensure_ascii=False,
            ).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/api/chat",
            data=payload,
            headers={"Content-Type": "application/json", "User-Agent": "pandas-ko-docs/ollama"},
            method="POST",
        )
        last_error: Exception | None = None
        for attempt in range(1, 4):
            try:
                # 연속 요청 사이에 짧은 간격을 두어 순간 부하를 완화합니다.
                time.sleep(0.15)
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    result = json.loads(response.read().decode("utf-8"))
                text = result.get("message", {}).get("content")
                if not isinstance(text, str) or not text.strip():
                    raise RuntimeError("Ollama 응답에 message.content가 없습니다.")
                return text.strip()
            except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError) as error:
                last_error = error
                if attempt < 3:
                    time.sleep(attempt)
        self.errors.append(f"{kind}: {type(last_error).__name__}: {last_error}")
        return None

    def _chat_many(self, items: list[dict], kind: str) -> dict[str, str] | None:
        prompt = (
            "Translate each JSON item's content independently. Return only valid JSON in exactly this form: "
            '{"translations":[{"id":"same id","content":"translated content"}]}. '
            "Do not omit or merge items. Preserve XML tags, attributes, and <keep> elements.\n"
            + json.dumps({"items": items}, ensure_ascii=False)
        )
        response = self._chat(prompt, kind, json_mode=True)
        if response is None:
            return None
        try:
            cleaned = response.strip()
            if cleaned.startswith("```"):
                cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
                cleaned = re.sub(r"\s*```$", "", cleaned)
            payload = json.loads(cleaned)
            records = payload["translations"]
            return {str(record["id"]): str(record["content"]) for record in records}
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
            self.errors.append(f"{kind} JSON parsing: {error}")
            return None

    @staticmethod
    def _clean_xml_response(text: str) -> str:
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:xml|html)?\s*", "", cleaned, flags=re.IGNORECASE)
            cleaned = re.sub(r"\s*```$", "", cleaned)
        start = cleaned.find("<pandasroot>")
        end = cleaned.rfind("</pandasroot>")
        if start >= 0 and end >= 0:
            return cleaned[start : end + len("</pandasroot>")]
        return cleaned

    def translate_batch(self, texts: list[str]) -> list[str | None]:
        translated: list[str | None] = [None] * len(texts)
        for offset in range(0, len(texts), 16):
            group = texts[offset : offset + 16]
            protected = [protect_terms(text) for text in group]
            records = self._chat_many(
                [{"id": str(i), "content": value[0]} for i, value in enumerate(protected)],
                "text-batch",
            )
            if records is None:
                continue
            for i, (_, saved) in enumerate(protected):
                response = records.get(str(i))
                if response is None:
                    self.errors.append(f"text item 누락: {offset + i}")
                    continue
                try:
                    translated[offset + i] = restore_terms(self._clean_xml_response(response), saved)
                except ValueError as error:
                    self.errors.append(f"text 보호 복원: {error}")
        return translated

    def translate_html_batch(self, fragments: list[str]) -> list[str | None]:
        translated: list[str | None] = [None] * len(fragments)
        for offset in range(0, len(fragments), 12):
            group = fragments[offset : offset + 12]
            records = self._chat_many(
                [{"id": str(i), "content": f"<pandasroot>{fragment}</pandasroot>"} for i, fragment in enumerate(group)],
                "html-batch",
            )
            if records is None:
                continue
            for i in range(len(group)):
                response = records.get(str(i))
                if response is None:
                    self.errors.append(f"html item 누락: {offset + i}")
                    continue
                try:
                    root = ElementTree.fromstring(self._clean_xml_response(response))
                    if root.tag != "pandasroot":
                        raise ValueError(f"Ollama XML root가 변경됐습니다: {root.tag}")
                    inner = root.text or ""
                    inner += "".join(ElementTree.tostring(child, encoding="unicode") for child in root)
                    translated[offset + i] = inner
                except (ElementTree.ParseError, ValueError) as error:
                    self.errors.append(f"html 보호 복원: {error}")
        return translated


def resolve_endpoint(choice: str, api_key: str) -> str:
    override = os.environ.get("DEEPL_API_URL")
    if override:
        return override
    if choice == "free" or (choice == "auto" and api_key.endswith(":fx")):
        return DEEPL_FREE_URL
    return DEEPL_PRO_URL


def copy_source_tree() -> None:
    if not SOURCE_ROOT.is_dir():
        raise FileNotFoundError(f"Pandas 원본 문서가 없습니다: {SOURCE_ROOT}")
    OUTPUT_ROOT.parent.mkdir(parents=True, exist_ok=True)
    if not OUTPUT_ROOT.exists():
        shutil.copytree(SOURCE_ROOT, OUTPUT_ROOT)
    else:
        # scope를 나눠 실행해도 먼저 번역한 HTML/searchindex가 되돌아가지 않게 보존합니다.
        # 정적 리소스와 새로 생긴 문서는 원본에서 동기화합니다.
        for source in SOURCE_ROOT.rglob("*"):
            relative = source.relative_to(SOURCE_ROOT)
            destination = OUTPUT_ROOT / relative
            if source.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
                continue
            preserve_existing = source.suffix.lower() == ".html" or relative.as_posix() == "searchindex.js"
            if preserve_existing and destination.exists():
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)

    stylesheet_source = Path(__file__).resolve().parent / "assets" / "pandas-ko.css"
    stylesheet_output = OUTPUT_ROOT / "_static" / "pandas-ko.css"
    stylesheet_output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(stylesheet_source, stylesheet_output)


def inject_korean_stylesheet(soup: BeautifulSoup, output_path: Path) -> None:
    """원본 theme를 수정하지 않고 생성본에만 모바일 보정 CSS를 연결합니다."""
    if soup.head is None:
        return
    relative_css = os.path.relpath(
        OUTPUT_ROOT / "_static" / "pandas-ko.css", output_path.parent
    ).replace(os.sep, "/")
    link = soup.new_tag("link")
    link["rel"] = "stylesheet"
    link["href"] = relative_css
    link["data-pandas-ko"] = "responsive"
    soup.head.append(link)


def unique_missing_texts(nodes: list, cache: TranslationCache) -> tuple[list[str], int]:
    missing: list[str] = []
    seen: set[str] = set()
    cached_count = 0
    for node in nodes:
        _, core, _ = split_outer_whitespace(str(node))
        if not core or core in seen:
            continue
        seen.add(core)
        if cache.get(core) is None:
            missing.append(core)
        else:
            cached_count += 1
    return missing, cached_count


def batches(texts: list[str]):
    batch: list[str] = []
    characters = 0
    for text in texts:
        if batch and (len(batch) >= MAX_BATCH_ITEMS or characters + len(text) > MAX_BATCH_CHARACTERS):
            yield batch
            batch = []
            characters = 0
        batch.append(text)
        characters += len(text)
    if batch:
        yield batch


INLINE_MARKER_RE = re.compile(
    r"<keep\b(?=[^>]*data-pandas-ko-inline=[\"'](\d+)[\"'])[^>]*>.*?</keep>",
    re.IGNORECASE | re.DOTALL,
)


def atomicize_inline_tags(fragment: str) -> tuple[str, list[str]]:
    """문단 내부 tag subtree를 삭제/복제할 수 없는 하나의 marker로 만듭니다."""
    soup = BeautifulSoup(fragment, "html.parser")
    originals: list[str] = []
    candidates = [
        tag for tag in soup.find_all(tuple(ATOMIC_INLINE_TAGS)) if tag.parent is soup
    ]
    visible_counts = Counter(tag.get_text(" ", strip=True) or tag.name for tag in candidates)
    for tag in candidates:
        original = str(tag)
        index = len(originals)
        originals.append(original)
        marker = soup.new_tag("keep")
        marker["data-pandas-ko-inline"] = str(index)
        visible = tag.get_text(" ", strip=True) or tag.name
        # 같은 수식/레이블이 반복되면 DeepL이 하나로 합치지 않도록 내용도 고유하게 만듭니다.
        marker.string = (
            f"PANDASINLINE{index:04d}X {visible}"
            if visible_counts[visible] > 1
            else visible
        )
        tag.replace_with(marker)
    return soup.decode_contents(formatter="minimal"), originals


def restore_inline_markers(fragment: str, originals: list[str]) -> str:
    found: set[int] = set()

    def replace(match: re.Match[str]) -> str:
        index = int(match.group(1))
        if index >= len(originals):
            raise ValueError(f"DeepL inline marker 번호가 잘못됐습니다: {index}")
        found.add(index)
        return originals[index]

    restored = INLINE_MARKER_RE.sub(replace, fragment)
    expected = set(range(len(originals)))
    if found != expected:
        raise ValueError(f"DeepL 응답에서 inline marker가 누락됐습니다: {sorted(expected - found)}")
    return restored


def block_fragment(tag, protected: list[str]) -> tuple[str, set[int], list[str]]:
    """문단 HTML 안의 보호 주석을 문맥용 ignore tag로 바꿉니다."""
    fragment = tag.decode_contents(formatter="minimal")
    indexes: set[int] = set()

    def replace(match: re.Match[str]) -> str:
        index = int(match.group(1))
        if index >= len(protected):
            raise ValueError(f"문단의 보호 토큰 번호가 잘못됐습니다: {index}")
        original = protected[index]
        digest = hashlib.sha256(original.encode("utf-8")).hexdigest()[:12]
        if digest != match.group(2):
            raise ValueError(f"문단 보호 토큰 checksum 불일치: {index}")
        indexes.add(index)
        visible = BeautifulSoup(original, "html.parser").get_text(" ", strip=True)
        return (
            f'<keep data-pandas-ko-protected="{index}">'
            f"{html.escape(visible, quote=False)}</keep>"
        )

    with_protected = PROTECTED_COMMENT_RE.sub(replace, fragment)
    atomic, inline_originals = atomicize_inline_tags(with_protected)
    return atomic, indexes, inline_originals


KEEP_MARKER_RE = re.compile(
    r"<keep\b(?=[^>]*data-pandas-ko-protected=[\"'](\d+)[\"'])[^>]*>.*?</keep>",
    re.IGNORECASE | re.DOTALL,
)


def restore_block_markers(fragment: str, protected: list[str], expected: set[int]) -> str:
    found: set[int] = set()

    def replace(match: re.Match[str]) -> str:
        index = int(match.group(1))
        if index not in expected or index >= len(protected):
            raise ValueError(f"DeepL 문단 응답의 보호 marker가 잘못됐습니다: {index}")
        found.add(index)
        digest = hashlib.sha256(protected[index].encode("utf-8")).hexdigest()[:12]
        return f"<!--PANDAS_KO_PROTECTED_{index:06d}_{digest}-->"

    restored = KEEP_MARKER_RE.sub(replace, fragment)
    if found != expected:
        raise ValueError(f"DeepL 문단 응답에서 보호 marker가 누락됐습니다: {sorted(expected - found)}")
    return restored


def tag_signature(tag) -> tuple:
    attributes = []
    for name, value in sorted(tag.attrs.items()):
        if isinstance(value, list):
            value = tuple(value)
        attributes.append((name, value))
    return tag.name, tuple(attributes)


def repair_fragment_structure(source: str, translated: str) -> str:
    """DeepL이 드물게 복제한 tag는 벗기고, 누락/속성 변경은 오류로 막습니다."""
    source_soup = BeautifulSoup(source, "html.parser")
    translated_soup = BeautifulSoup(translated, "html.parser")
    required = Counter(tag_signature(tag) for tag in source_soup.find_all(True))
    seen: Counter = Counter()
    for tag in list(translated_soup.find_all(True)):
        signature = tag_signature(tag)
        seen[signature] += 1
        if seen[signature] > required[signature]:
            tag.unwrap()

    actual = Counter(tag_signature(tag) for tag in translated_soup.find_all(True))
    if actual != required:
        missing = list((required - actual).items())[:3]
        extra = list((actual - required).items())[:3]
        raise ValueError(f"DeepL 문단 tag/속성 변경: missing={missing}, extra={extra}")
    return translated_soup.decode_contents(formatter="minimal")


def translatable_blocks(soup: BeautifulSoup) -> list:
    """인라인 code까지 포함한 문맥 전체를 번역할 leaf block을 선택합니다."""
    blocks = []
    names = tuple(BLOCK_TAGS)
    for tag in soup.find_all(names):
        if tag.find(names) is not None:
            continue
        if re.search(r"[A-Za-z]{2,}", tag.get_text(" ")):
            blocks.append(tag)
    return blocks


def replace_inner_html(tag, fragment: str) -> None:
    parsed = BeautifulSoup(fragment, "html.parser")
    tag.clear()
    for child in list(parsed.contents):
        tag.append(child.extract())


def translate_file(
    source_path: Path,
    output_path: Path,
    cache: TranslationCache,
    translator,
) -> tuple[int, int]:
    raw = source_path.read_text(encoding="utf-8")
    protected_html, protected = protect_html(raw)
    soup = BeautifulSoup(protected_html, "html.parser")
    relative = source_path.relative_to(SOURCE_ROOT).as_posix()

    # 본문 문단/제목/표 셀은 인라인 code의 앞뒤 문맥을 한 번에 번역합니다.
    blocks = translatable_blocks(soup)
    block_sources: list[str] = []
    block_markers: list[set[int]] = []
    block_inline_originals: list[list[str]] = []
    for tag in blocks:
        source, markers, inline_originals = block_fragment(tag, protected)
        block_sources.append(source)
        block_markers.append(markers)
        block_inline_originals.append(inline_originals)
    missing_blocks = list(dict.fromkeys(source for source in block_sources if cache.get(source) is None))
    for batch in batches(missing_blocks):
        results = translator.translate_html_batch(batch)
        for source, translated in zip(batch, results):
            if translated is None:
                continue
            cache.put(source, translated, relative, getattr(translator, "engine", "translation-html"))
        print(f"    API 문단 {len(batch):>2}개 번역 및 cache 저장", flush=True)

    fallback_block_ids: set[int] = set()
    for tag, source, markers, inline_originals in zip(
        blocks, block_sources, block_markers, block_inline_originals
    ):
        translated = cache.get(source)
        if translated is None:
            fallback_block_ids.add(id(tag))
            continue
        try:
            repaired = repair_fragment_structure(source, translated)
            with_inline = restore_inline_markers(repaired, inline_originals)
            restored_fragment = restore_block_markers(with_inline, protected, markers)
            replace_inner_html(tag, restored_fragment)
        except ValueError as error:
            # DeepL이 tag를 삭제/변경하면 해당 문단만 원본 골격에서 텍스트별 번역으로 전환합니다.
            fallback_block_ids.add(id(tag))
            print(f"    구조 보호 fallback: {error}", flush=True)

    # 문단 밖의 탐색 메뉴/버튼 같은 짧은 UI 텍스트는 기존 문장 cache를 활용합니다.
    nodes = []
    for node in translatable_nodes(soup):
        block_parent = node.find_parent(tuple(BLOCK_TAGS))
        inline_parent = node.find_parent(tuple(ATOMIC_INLINE_TAGS))
        if (
            block_parent is None
            or inline_parent is not None
            or (block_parent is not None and id(block_parent) in fallback_block_ids)
        ):
            nodes.append(node)
    missing, cached_count = unique_missing_texts(nodes, cache)

    for batch in batches(missing):
        results = translator.translate_batch(batch)
        for source, translated in zip(batch, results):
            if translated is None:
                continue
            cache.put(source, translated, relative, getattr(translator, "engine", "translation-text"))
        print(f"    API {len(batch):>2}개 문장 번역 및 cache 저장", flush=True)

    replaced = 0
    for node in nodes:
        leading, core, trailing = split_outer_whitespace(str(node))
        translated = cache.get(core) if core else None
        if translated is None:
            continue
        node.replace_with(f"{leading}{translated}{trailing}")
        replaced += 1

    inject_korean_stylesheet(soup, output_path)
    restored = restore_html(str(soup), protected)
    temporary = output_path.with_suffix(output_path.suffix + ".tmp")
    temporary.write_text(restored, encoding="utf-8")
    temporary.replace(output_path)
    return replaced + len(blocks), cached_count


def update_search_index(translated_files: list[Path]) -> dict[str, int]:
    index_path = OUTPUT_ROOT / "searchindex.js"
    index = load_search_index(index_path)
    stats = add_korean_search_terms(index, OUTPUT_ROOT, translated_files)
    save_search_index(index_path, index)
    return stats


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scope", choices=SCOPES, default="getting_started")
    parser.add_argument("--ollama-url", default="http://localhost:11434")
    parser.add_argument("--ollama-model", default="qwen2.5:3b")
    parser.add_argument("--limit", type=int, help="개발 확인용 최대 HTML 파일 수")
    parser.add_argument(
        "--start-at",
        help="이 상대 HTML 경로부터 이어서 처리합니다. 예: reference/aliases.html",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="파일/API를 변경하지 않고 대상 문장 수만 분석합니다.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    source_files = html_files_for_scope(SOURCE_ROOT, args.scope)
    if args.start_at:
        normalized_start = Path(args.start_at).as_posix().lower()
        start_index = next(
            (
                index
                for index, path in enumerate(source_files)
                if path.relative_to(SOURCE_ROOT).as_posix().lower() == normalized_start
            ),
            None,
        )
        if start_index is None:
            raise ValueError(f"--start-at 파일을 찾을 수 없습니다: {args.start_at}")
        source_files = source_files[start_index:]
    if args.limit is not None:
        if args.limit < 1:
            raise ValueError("--limit은 1 이상이어야 합니다.")
        source_files = source_files[: args.limit]
    print(f"번역 범위: {args.scope} ({len(source_files)}개 HTML)")

    cache = TranslationCache(CACHE_PATH)
    try:
        if args.dry_run:
            total_blocks = 0
            total_nodes = 0
            missing_blocks: set[str] = set()
            missing_texts: set[str] = set()
            for path in source_files:
                protected_html, protected = protect_html(path.read_text(encoding="utf-8"))
                soup = BeautifulSoup(protected_html, "html.parser")
                blocks = translatable_blocks(soup)
                total_blocks += len(blocks)
                fallback_ids: set[int] = set()
                for tag in blocks:
                    source, _, _ = block_fragment(tag, protected)
                    translated = cache.get(source)
                    if translated is None:
                        missing_blocks.add(source)
                    else:
                        try:
                            repair_fragment_structure(source, translated)
                        except ValueError:
                            fallback_ids.add(id(tag))
                nodes = []
                for node in translatable_nodes(soup):
                    block_parent = node.find_parent(tuple(BLOCK_TAGS))
                    inline_parent = node.find_parent(tuple(ATOMIC_INLINE_TAGS))
                    if (
                        block_parent is None
                        or inline_parent is not None
                        or (block_parent is not None and id(block_parent) in fallback_ids)
                    ):
                        nodes.append(node)
                total_nodes += len(nodes)
                for node in nodes:
                    _, core, _ = split_outer_whitespace(str(node))
                    if core and cache.get(core) is None:
                        missing_texts.add(core)
            print(f"번역 대상 문단: {total_blocks}, 보조 텍스트 노드: {total_nodes}")
            print(f"API가 필요한 고유 문단: {len(missing_blocks)}")
            print(f"API가 필요한 고유 보조 문장: {len(missing_texts)}")
            return 0

        print(f"Ollama: {args.ollama_url} / {args.ollama_model}")
        print("원본 전체를 dist/pandas-ko로 복사합니다.")
        copy_source_tree()

        translator = OllamaTranslator(args.ollama_url, args.ollama_model)
        output_files: list[Path] = []
        file_errors: list[tuple[str, str]] = []
        total_replaced = 0
        for number, source_path in enumerate(source_files, start=1):
            relative = source_path.relative_to(SOURCE_ROOT)
            output_path = OUTPUT_ROOT / relative
            print(f"[{number}/{len(source_files)}] {relative.as_posix()}", flush=True)
            try:
                replaced, cached = translate_file(source_path, output_path, cache, translator)
            except ValueError as error:
                file_errors.append((relative.as_posix(), str(error)))
                print(f"    파일 건너뜀: {error}", flush=True)
                continue
            total_replaced += replaced
            output_files.append(output_path)
            print(f"    적용 {replaced}개 (기존 cache 고유 문장 {cached}개)", flush=True)

        # 이어하기 전에 완료된 페이지도 최종 한국어 검색 index에 포함합니다.
        search_stats = update_search_index(html_files_for_scope(OUTPUT_ROOT, args.scope))
        if getattr(translator, "errors", None):
            file_errors.extend(("[ollama]", message) for message in translator.errors)
        error_log = OUTPUT_ROOT / "translation-errors.txt"
        if file_errors:
            error_log.write_text(
                "\n".join(f"{path}\t{message}" for path, message in file_errors) + "\n",
                encoding="utf-8",
            )
        else:
            error_log.write_text("오류 없음\n", encoding="utf-8")
        print(f"번역 텍스트 노드 적용: {total_replaced}")
        print(
            "검색 index 추가: "
            f"본문 {search_stats['body_terms']}, 제목 {search_stats['title_terms']}, "
            f"동의어 연결 {search_stats['synonym_links']}"
        )
        print(f"완료: {OUTPUT_ROOT}")
        print(f"건너뛴 파일: {len(file_errors)} (기록: {error_log})")
        return 0
    finally:
        cache.close()


if __name__ == "__main__":
    raise SystemExit(main())
