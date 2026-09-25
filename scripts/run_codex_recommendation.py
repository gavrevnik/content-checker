from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

from openai_codex import ApprovalMode, Codex, CodexConfig, Sandbox


def main() -> None:
    parser = argparse.ArgumentParser(description="Run an isolated Codex movie recommendation turn")
    parser.add_argument("--model", required=True)
    parser.add_argument("--schema", type=Path, required=True)
    args = parser.parse_args()
    recommendation_instructions = sys.stdin.read().strip()
    if not recommendation_instructions:
        raise SystemExit("Recommendation prompt is empty")
    try:
        output_schema = json.loads(args.schema.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SystemExit(f"Cannot read recommendation schema: {error}") from error

    with tempfile.TemporaryDirectory(prefix="whats-new-recommend-") as directory:
        config = CodexConfig(cwd=directory)
        with Codex(config) as codex:
            thread = codex.thread_start(
                approval_mode=ApprovalMode.deny_all,
                base_instructions=recommendation_instructions,
                cwd=directory,
                ephemeral=True,
                model=args.model,
                sandbox=Sandbox.read_only,
            )
            result = thread.run(
                "Выполни заданный в системных инструкциях контракт рекомендаций.",
                sandbox=Sandbox.read_only,
                output_schema=output_schema,
            )
    if result.error:
        raise SystemExit(str(result.error))
    if not result.final_response:
        raise SystemExit("Codex returned an empty response")
    print(result.final_response.strip())


if __name__ == "__main__":
    main()
