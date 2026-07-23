#!/usr/bin/env python3
"""命令行工作流（无 Web UI，适合脚本化调用）。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from app.config import load_yaml_config  # noqa: E402
from app.workflow.orchestrator import WorkflowOrchestrator  # noqa: E402


def parse_styles(raw: str) -> list[dict[str, str]]:
    cfg = load_yaml_config()
    style_map = {s["id"]: s["name"] for s in cfg.get("ui_styles", [])}
    ids = [s.strip() for s in raw.split(",") if s.strip()]
    colors = []
    return [
        {"id": sid, "name": style_map[sid], "color": colors[i] if i < len(colors) else ""}
        for i, sid in enumerate(ids)
        if sid in style_map
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description="App 生成工作流 CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    start_p = sub.add_parser("start", help="输入应用名并分析需求")
    start_p.add_argument("app_name")
    start_p.add_argument("project_dir")
    start_p.add_argument("--group-no", default="", help="组号（可选）")

    design_p = sub.add_parser("design", help="为会话生成设计稿")
    design_p.add_argument("session_id")
    design_p.add_argument("--styles", required=True, help="风格 id，逗号分隔，如 ai-native,glacier")
    design_p.add_argument("--colors", default="", help="配色，分号分隔")

    gen_p = sub.add_parser("generate", help="选择方案并生成代码")
    gen_p.add_argument("session_id")
    gen_p.add_argument("--plan", type=int, default=0, help="方案序号（从 0 开始）")
    gen_p.add_argument("--framework", default="vue")
    gen_p.add_argument(
        "--tool",
        default="claude",
        choices=["codex", "claude", "cursor"],
        help="代码生成工具（默认 claude）",
    )

    status_p = sub.add_parser("status", help="查看会话状态")
    status_p.add_argument("session_id")

    args = parser.parse_args()

    if args.cmd == "start":
        orch = WorkflowOrchestrator()
        state = orch.start(args.app_name, args.project_dir, group_no=args.group_no)
        print(json.dumps(state.to_public_dict(), ensure_ascii=False, indent=2))
        print(f"\n会话 ID: {state.session_id}")

    elif args.cmd == "design":
        orch = WorkflowOrchestrator(args.session_id)
        styles = parse_styles(args.styles)
        colors = [c.strip() for c in args.colors.replace(";", "；").split("；") if c.strip()]
        for i, s in enumerate(styles):
            if i < len(colors):
                s["color"] = colors[i]
        orch.set_styles(styles)
        state = orch.run_design_step()
        print(json.dumps(state.to_public_dict(), ensure_ascii=False, indent=2))

    elif args.cmd == "generate":
        orch = WorkflowOrchestrator(args.session_id)
        orch.select_plan(args.plan, args.framework, args.tool)
        state = orch.run_init_and_codegen(args.tool)
        print(json.dumps(state.to_public_dict(), ensure_ascii=False, indent=2))

    elif args.cmd == "status":
        orch = WorkflowOrchestrator(args.session_id)
        print(json.dumps(orch.get_state().to_public_dict(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
