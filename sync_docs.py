"""
ローカル docs/*.md と AnythingLLM ワークスペースのドキュメント一覧を突き合わせ、
未登録のファイルだけを /api/v1/document/upload でアップロードする。

環境変数:
  ANYTHINGLLM_BASE_URL  例: https://your-host (末尾の / は可、/api は含めない)
  ANYTHINGLLM_API_KEY   Developer API キー (Bearer)
  ANYTHINGLLM_WORKSPACE_SLUG  ワークスペースの slug

オプション:
  --dry-run  アップロードせず差分のみ表示
  --docs-dir  同期元ディレクトリ (既定: リポジトリ直下 docs)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

import requests

# AnythingLLM は処理後ファイル名が「元ファイル名-UUID.json」になる
_UUID_JSON_TAIL = re.compile(
    r"-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.json$",
    re.IGNORECASE,
)


def _strip_uuid_json_suffix(filename: str) -> str:
    return _UUID_JSON_TAIL.sub("", filename)


def _normalize_workspace_payload(data: dict) -> dict:
    """GET /workspace/:slug の workspace がオブジェクトまたは長さ1の配列の両方に対応。"""
    ws = data.get("workspace")
    if isinstance(ws, list):
        if not ws:
            raise ValueError("workspace 配列が空です")
        return ws[0]
    if isinstance(ws, dict):
        return ws
    raise ValueError("レスポンスに workspace がありません")


def _flatten_document_entries(documents_field: object) -> list[dict]:
    """documents がフラット配列または { folder, documents } のバケツ混在に対応。"""
    out: list[dict] = []

    def walk(node: object) -> None:
        if node is None:
            return
        if isinstance(node, list):
            for item in node:
                walk(item)
            return
        if not isinstance(node, dict):
            return
        if isinstance(node.get("documents"), list) and (
            "folder" in node or node.get("type") == "folder"
        ):
            walk(node["documents"])
            return
        if node.get("docpath") or node.get("filename") or node.get("name"):
            out.append(node)
            return
        if isinstance(node.get("documents"), list):
            walk(node["documents"])

    walk(documents_field)
    return out


def logical_md_name_from_remote_doc(doc: dict) -> str | None:
    """リモート1件から、ローカルと対応付ける論理ファイル名 (例: 360010454759.md) を推定。"""
    raw = doc.get("filename") or doc.get("name")
    if not raw and doc.get("docpath"):
        raw = str(doc["docpath"]).replace("\\", "/").split("/")[-1]
    if not raw:
        return None
    base = _strip_uuid_json_suffix(raw)
    if base.endswith(".md"):
        return base

    meta = doc.get("metadata")
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except json.JSONDecodeError:
            meta = None
    if isinstance(meta, dict):
        title = meta.get("title") or ""
        if isinstance(title, str) and title.endswith(".md"):
            return title
    return None


def fetch_remote_md_basenames(
    session: requests.Session,
    base_url: str,
    api_key: str,
    workspace_slug: str,
) -> set[str]:
    url = f"{base_url.rstrip('/')}/api/v1/workspace/{workspace_slug}"
    r = session.get(
        url,
        headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
        timeout=120,
    )
    r.raise_for_status()
    ws = _normalize_workspace_payload(r.json())
    names: set[str] = set()
    for doc in _flatten_document_entries(ws.get("documents")):
        logical = logical_md_name_from_remote_doc(doc)
        if logical:
            names.add(logical)
    return names


def upload_markdown(
    session: requests.Session,
    base_url: str,
    api_key: str,
    workspace_slug: str,
    file_path: Path,
) -> dict:
    url = f"{base_url.rstrip('/')}/api/v1/document/upload"
    headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
    with file_path.open("rb") as f:
        files = {"file": (file_path.name, f, "text/markdown")}
        data = {"addToWorkspaces": workspace_slug}
        r = session.post(url, headers=headers, files=files, data=data, timeout=300)
    r.raise_for_status()
    return r.json()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="AnythingLLM へ docs の不足分のみアップロード")
    p.add_argument(
        "--docs-dir",
        type=Path,
        default=Path(__file__).resolve().parent / "docs",
        help="同期元ディレクトリ",
    )
    p.add_argument("--dry-run", action="store_true", help="アップロードしない")
    p.add_argument(
        "--interval",
        type=float,
        default=1.0,
        help="アップロード間隔 (秒)",
    )
    args = p.parse_args(argv)

    base_url = os.environ.get("ANYTHINGLLM_BASE_URL", "").strip()
    api_key = os.environ.get("ANYTHINGLLM_API_KEY", "").strip()
    slug = os.environ.get("ANYTHINGLLM_WORKSPACE_SLUG", "").strip()

    missing = [v for v, n in [(base_url, "ANYTHINGLLM_BASE_URL"), (api_key, "ANYTHINGLLM_API_KEY"), (slug, "ANYTHINGLLM_WORKSPACE_SLUG")] if not v]
    if missing:
        print("以下の環境変数が必要です: " + ", ".join(missing), file=sys.stderr)
        return 2

    docs_dir: Path = args.docs_dir
    if not docs_dir.is_dir():
        print(f"docs ディレクトリがありません: {docs_dir}", file=sys.stderr)
        return 2

    local_files = sorted(docs_dir.glob("*.md"))
    if not local_files:
        print(f"{docs_dir} に .md がありません")
        return 0

    session = requests.Session()

    print(f"AnythingLLM からドキュメント一覧を取得: {base_url} workspace={slug}")
    try:
        remote = fetch_remote_md_basenames(session, base_url, api_key, slug)
    except requests.RequestException as e:
        print(f"一覧取得に失敗: {e}", file=sys.stderr)
        if getattr(e, "response", None) is not None and e.response is not None:
            print(e.response.text[:2000], file=sys.stderr)
        return 1

    to_upload = [f for f in local_files if f.name not in remote]

    print(f"ローカル: {len(local_files)} 件 / リモート (.md 相当): {len(remote)} 件 / 未登録: {len(to_upload)} 件")

    if args.dry_run:
        for f in to_upload[:50]:
            print(f"  [dry-run] would upload {f.name}")
        if len(to_upload) > 50:
            print(f"  ... 他 {len(to_upload) - 50} 件")
        return 0

    uploaded = 0
    errors = 0
    last_end: float | None = None

    for path in to_upload:
        if last_end is not None:
            elapsed = time.monotonic() - last_end
            if elapsed < args.interval:
                time.sleep(args.interval - elapsed)
        try:
            upload_markdown(session, base_url, api_key, slug, path)
            uploaded += 1
            print(f"アップロード: {path.name}")
        except requests.RequestException as e:
            errors += 1
            print(f"失敗: {path.name} — {e}", file=sys.stderr)
            if getattr(e, "response", None) is not None and e.response is not None:
                print(e.response.text[:500], file=sys.stderr)
        finally:
            last_end = time.monotonic()

    already_on_remote = len(local_files) - len(to_upload)
    print(
        f"アップロード: {uploaded} 件 / スキップ(既にリモートに同一 .md あり): {already_on_remote} 件 / エラー: {errors} 件"
    )
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
