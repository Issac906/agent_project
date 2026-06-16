"""Command line entry point for the LightRAG-based agent workflow."""

from __future__ import annotations

import argparse
from pathlib import Path

from dotenv import load_dotenv

from config import load_config
from lightrag_client import LightRAGClient, LightRAGClientError
from patent_discovery_agent import run_interactive_agent
from workflow import run_workflow


DEFAULT_OUTPUT_DIR = Path("outputs")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a LightRAG-based patent writing agent workflow."
    )
    parser.add_argument(
        "question",
        nargs="*",
        help="可选：直接指定写作任务。不传则进入专利发现交互流程。",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="检查 /documents 和 /documents/status_counts 是否可用。",
    )
    parser.add_argument(
        "--with-query-data",
        action="store_true",
        help="同时调用 /query/data，并把原始检索上下文写入 Markdown。",
    )
    parser.add_argument(
        "--enable-external-search",
        action="store_true",
        help="强制启用外部搜索。默认在知识库无上下文或 API 报错时自动启用。",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="输出目录，默认 outputs。",
    )
    parser.add_argument(
        "--direct",
        action="store_true",
        help="跳过专利发现流程，直接按输入任务生成文档。",
    )
    return parser.parse_args()


def main() -> int:
    load_dotenv()
    args = parse_args()
    config = load_config()

    client = LightRAGClient(
        base_url=config.lightrag_base_url,
        api_key=config.lightrag_api_key,
        query_mode=config.lightrag_query_mode,
        include_chunk_content=config.lightrag_include_chunk_content,
    )

    if args.check:
        return run_api_check(client)

    question = " ".join(args.question).strip()
    if not args.direct:
        run_interactive_agent(
            config=config,
            client=client,
            output_dir=Path(args.output_dir),
        )
        return 0

    if not question:
        print("直接生成模式下任务不能为空。")
        return 1

    output_path = run_workflow(
        task=question,
        client=client,
        output_dir=Path(args.output_dir),
        config=config,
        include_query_data=args.with_query_data,
        enable_external_search=args.enable_external_search,
    )

    print(f"已生成最终文档：{output_path}")
    print(f"同时更新兼容输出：{Path(args.output_dir) / 'result.md'}")
    return 0


def run_api_check(client: LightRAGClient) -> int:
    checks = (
        ("GET /documents", client.list_documents),
        ("GET /documents/status_counts", client.get_status_counts),
    )

    ok = True
    for name, check in checks:
        try:
            result = check()
        except LightRAGClientError as exc:
            ok = False
            print(f"[FAIL] {name}: {exc}")
        else:
            print(f"[OK] {name}: {result}")

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
