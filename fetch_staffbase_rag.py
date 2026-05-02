"""
staffbase_urls.md の URL 一覧から記事を Help Center JSON API で取得し、
docs/ に Markdown として保存する。
既存ファイルは API 結果と比較し、タイトル・URL・本文が変わった場合のみ上書きする。
"""

from __future__ import annotations

import json
import re
import time
from html import unescape
from html.parser import HTMLParser
from datetime import datetime, timezone
from pathlib import Path

import requests

URLS_MD = Path(__file__).resolve().parent / "staffbase_urls.md"
DOCS_DIR = Path(__file__).resolve().parent / "docs"
ERROR_LOG = Path(__file__).resolve().parent / "error.log"
API_TEMPLATE = "https://support.staffbase.com/api/v2/help_center/ja/articles/{article_id}.json"
REQUEST_INTERVAL_SEC = 1.0

# fetch_staffbase.py と同様の API ベース URL
USER_AGENT = (
    "Mozilla/5.0 (compatible; StaffbaseRAGFetcher/1.0; +https://support.staffbase.com)"
)


def parse_urls_from_markdown(path: Path) -> list[tuple[str, str]]:
    """staffbase_urls.md から (元のURL, 記事ID) のリストを返す。"""
    text = path.read_text(encoding="utf-8")
    pattern = re.compile(
        r"-\s*\[([^\]]*)\]\((https://support\.staffbase\.com/hc/ja/articles/(\d+))\)"
    )
    out: list[tuple[str, str]] = []
    for m in pattern.finditer(text):
        url, article_id = m.group(2), m.group(3)
        out.append((url, article_id))
    return out


class _HTMLToText(HTMLParser):
    """記事 body の HTML をプレーンテキストに変換する（script/style は無視）。"""

    _BLOCK = frozenset(
        {
            "p",
            "div",
            "br",
            "li",
            "tr",
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6",
            "section",
            "article",
            "blockquote",
            "pre",
        }
    )
    _SKIP = frozenset({"script", "style", "noscript"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        t = tag.lower()
        if t in self._SKIP:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if t == "br" or t in self._BLOCK:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        if t in self._SKIP and self._skip_depth > 0:
            self._skip_depth -= 1
            return
        if self._skip_depth:
            return
        if t in self._BLOCK:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        self._parts.append(data)

    def get_text(self) -> str:
        raw = "".join(self._parts)
        raw = unescape(raw)
        lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
        return "\n".join(lines)


def html_to_plain_text(html: str) -> str:
    parser = _HTMLToText()
    parser.feed(html)
    parser.close()
    return parser.get_text()


def build_markdown(title: str, source_url: str, fetched_at: str, body_plain: str) -> str:
    return (
        "---\n"
        f"# {title}\n\n"
        f"**URL**: {source_url}\n"
        f"**取得日**: {fetched_at}\n\n"
        "## 内容\n\n"
        f"{body_plain}\n"
        "---\n"
    )


# 取得日は実行のたびに変わるため、差分判定からは除外する
_SAVED_DOC_RE = re.compile(
    r"^---\n#\s*(.+?)\n\n\*\*URL\*\*:\s*(.+?)\n\*\*取得日\*\*:[^\n]*\n\n##\s*内容\n\n([\s\S]*?)\n---\s*$",
    re.MULTILINE,
)


def parse_saved_doc_for_compare(text: str) -> tuple[str, str, str] | None:
    """既存 Markdown から (タイトル, URL, 本文) を取り出す。想定外フォーマットなら None。"""
    text = text.replace("\r\n", "\n")
    m = _SAVED_DOC_RE.match(text)
    if not m:
        return None
    title, url, body = m.group(1), m.group(2), m.group(3)
    return (title.strip(), url.strip(), body.rstrip("\n"))


def article_compare_key(title: str, source_url: str, body_plain: str) -> tuple[str, str, str]:
    return (title.strip(), source_url.strip(), body_plain.rstrip("\n"))


def append_error_log(message: str) -> None:
    with ERROR_LOG.open("a", encoding="utf-8") as f:
        f.write(message + "\n")


def fetch_article(article_id: str, session: requests.Session) -> dict:
    url = API_TEMPLATE.format(article_id=article_id)
    r = session.get(url, timeout=60)
    r.raise_for_status()
    data = r.json()
    article = data.get("article")
    if not article:
        raise ValueError("レスポンスに article がありません")
    return article


def main() -> None:
    if not URLS_MD.is_file():
        print(f"エラー: {URLS_MD} が見つかりません")
        return

    DOCS_DIR.mkdir(parents=True, exist_ok=True)

    pairs = parse_urls_from_markdown(URLS_MD)
    if not pairs:
        print(f"{URLS_MD} から URL を解析できませんでした")
        return

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})

    n_new = 0
    n_updated = 0
    n_skipped_same = 0
    n_error = 0
    last_request_end: float | None = None

    for source_url, article_id in pairs:
        out_path = DOCS_DIR / f"{article_id}.md"

        # 取得間隔（直前のリクエスト完了から 1 秒以上）
        if last_request_end is not None:
            elapsed = time.monotonic() - last_request_end
            if elapsed < REQUEST_INTERVAL_SEC:
                time.sleep(REQUEST_INTERVAL_SEC - elapsed)

        try:
            article = fetch_article(article_id, session)
            title = article.get("title") or "(無題)"
            body_html = article.get("body") or ""
            body_plain = html_to_plain_text(body_html)
            fetched_at = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
            md = build_markdown(title, source_url, fetched_at, body_plain)
            new_key = article_compare_key(title, source_url, body_plain)

            existed = out_path.is_file()
            if existed:
                try:
                    old_text = out_path.read_text(encoding="utf-8")
                except OSError as e:
                    n_error += 1
                    msg = (
                        f"[{datetime.now(timezone.utc).isoformat()}] "
                        f"article_id={article_id} url={source_url} read_existing: {e}"
                    )
                    append_error_log(msg)
                    continue

                parsed = parse_saved_doc_for_compare(old_text)
                if parsed is not None:
                    old_key = article_compare_key(parsed[0], parsed[1], parsed[2])
                    if old_key == new_key:
                        n_skipped_same += 1
                        continue

                out_path.write_text(md, encoding="utf-8")
                n_updated += 1
            else:
                out_path.write_text(md, encoding="utf-8")
                n_new += 1
        except requests.HTTPError as e:
            n_error += 1
            msg = (
                f"[{datetime.now(timezone.utc).isoformat()}] "
                f"article_id={article_id} url={source_url} "
                f"HTTPError: {e.response.status_code if e.response else ''} {e}"
            )
            append_error_log(msg)
        except (requests.RequestException, json.JSONDecodeError, ValueError, OSError) as e:
            n_error += 1
            msg = (
                f"[{datetime.now(timezone.utc).isoformat()}] "
                f"article_id={article_id} url={source_url} {type(e).__name__}: {e}"
            )
            append_error_log(msg)
        finally:
            last_request_end = time.monotonic()

    print(f"更新件数: {n_updated} 件")
    print(f"スキップ件数: {n_skipped_same} 件")
    print(f"新規件数: {n_new} 件")
    if n_error:
        print(f"エラー件数: {n_error} 件（詳細は {ERROR_LOG}）")


if __name__ == "__main__":
    main()
