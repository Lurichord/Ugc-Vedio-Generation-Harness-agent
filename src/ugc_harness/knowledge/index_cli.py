from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .corpus import knowledge_root
from .embed import ArkEmbedder
from ..shared.settings import EmbeddingSettings
from .retrieve import build_index


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ugc-knowledge-index",
        description="把知识库 Markdown 切块并写入本地向量索引。",
    )
    parser.add_argument(
        "--root",
        type=Path,
        help="知识库根目录，默认仓库 knowledge/",
    )
    parser.add_argument("--api-keys-file", type=Path)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    root = args.root or knowledge_root()
    try:
        settings = EmbeddingSettings.from_environment(args.api_keys_file)
        embedder = ArkEmbedder(settings)
        chunks = build_index(root=root, embedder=embedder, model=settings.model)
    except Exception as exc:
        print(f"Knowledge index failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    print(
        json.dumps(
            {
                "knowledge_root": str(Path(root).resolve()),
                "chunks": len(chunks),
                "model": settings.model,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
