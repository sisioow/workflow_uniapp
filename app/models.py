from __future__ import annotations

from enum import Enum
from typing import Any, Optional, List, Dict

from pydantic import BaseModel, Field


class WorkflowStep(str, Enum):
    INPUT = "input"
    ANALYZE = "analyze"
    REVIEW_REQUIREMENT = "review_requirement"
    SELECT_STYLES = "select_styles"
    DESIGN = "design"
    SELECT_PLAN = "select_plan"
    INIT_PROJECT = "init_project"
    CODEGEN = "codegen"
    EVALUATE = "evaluate"  # 第 8 步：整理测试发布
    DONE = "done"
    ERROR = "error"


class PageDesign(BaseModel):
    key: str
    title: str
    features: list[str] = Field(default_factory=list)
    description: str = ""


class RequirementPlan(BaseModel):
    app_name: str
    summary: str = ""
    pages: list[PageDesign] = Field(default_factory=list)
    sample_data: list[str] = Field(default_factory=list)
    raw_text: str = ""
    direction: str = ""  # 功能方向名称，如「清单管理型」


class StyleTask(BaseModel):
    style_id: str
    style_name: str
    color_hint: str = ""
    project_id: str = ""
    screen_ids: list[str] = Field(default_factory=list)
    status: str = "pending"  # pending | running | done | failed
    error: str = ""
    skip_design: bool = False  # 是否跳过了 Stitch 设计步骤


class QualityCheckItem(BaseModel):
    """单项质量检查结果。"""
    label: str         # 检查项名称（如 "page1.vue 文件存在"）
    passed: bool       # 是否通过
    detail: str = ""   # 详情（如 "文件大小 3.2KB" 或 "缺失"）


class QualityReport(BaseModel):
    """代码生成后的自动化质量评估报告。"""
    checks: list[QualityCheckItem] = Field(default_factory=list)
    score: int = 0  # 0-100 分

    @property
    def passed_count(self) -> int:
        return sum(1 for c in self.checks if c.passed)

    @property
    def total_count(self) -> int:
        return len(self.checks)

    @property
    def all_passed(self) -> bool:
        return all(c.passed for c in self.checks) if self.checks else False


class UserFeedback(BaseModel):
    """用户对生成结果的评分和反馈。"""
    rating: str = ""       # good | ok | bad
    score: int = 0         # 1-5 星
    comment: str = ""      # 文字反馈
    timestamp: str = ""    # ISO 时间戳


class FollowupMessage(BaseModel):
    """代码生成后的补充对话消息。"""
    role: str  # user | assistant | system
    content: str
    timestamp: str = ""
    status: str = "done"  # pending | running | done | error
    tool: str = ""


class WorkflowState(BaseModel):
    session_id: str
    step: WorkflowStep = WorkflowStep.INPUT
    app_name: str = ""
    project_dir: str = ""
    group_no: str = ""  # 组号，用于侧边栏展示与过滤
    created_at: str = ""  # ISO 创建时间，用于侧边栏列表展示
    framework: str = "vue"
    llm_model: str = ""
    codegen_tool: str = "claude"  # codex | claude | cursor — 默认 Claude Code
    codegen_running: bool = False  # 是否正在执行代码生成
    codegen_started_at: float = 0.0  # 代码生成开始时间戳，用于僵死状态回收
    design_running: bool = False  # 是否正在执行 Stitch 设计生成
    design_started_at: float = 0.0
    analyze_running: bool = False  # 是否正在执行需求分析（含重试）
    requirement: Optional[RequirementPlan] = None
    requirement_options: List[RequirementPlan] = Field(default_factory=list)  # 三套功能方向方案
    selected_requirement_index: int = 0
    selected_styles: List[Dict[str, str]] = Field(default_factory=list)
    style_tasks: List[StyleTask] = Field(default_factory=list)
    selected_task_index: int = 0
    project_path: str = ""
    logs: List[str] = Field(default_factory=list)
    # 代码生成实施进度（提示词 / 生成中 / 完成+耗时），不 stretch 终端全量日志
    codegen_progress: List[str] = Field(default_factory=list)
    error: str = ""
    error_step: str = ""
    quality_report: Optional[QualityReport] = None
    user_feedback: Optional[UserFeedback] = None
    followup_messages: List[FollowupMessage] = Field(default_factory=list)
    followup_running: bool = False

    # 第 8 步：整理测试发布
    readme_status: str = ""  # "" | running | done | failed
    readme_path: str = ""
    readme_error: str = ""
    h5_test_status: str = ""  # "" | running | done | failed
    h5_test_running: bool = False
    h5_test_url: str = ""
    h5_test_port: int = 0
    h5_test_message: str = ""
    release_status: str = ""  # "" | running | done | failed | branch_exists
    release_running: bool = False
    release_repo_path: str = ""
    release_branch: str = ""
    release_message: str = ""
    release_remote_url: str = ""
    release_commit: str = ""

    def append_log(self, message: str) -> None:
        self.logs.append(message)
        limit = 300
        if len(self.logs) > limit:
            self.logs = self.logs[-limit:]

    def reset_codegen_progress(self) -> None:
        self.codegen_progress = []

    def append_codegen_progress(self, message: str) -> None:
        self.codegen_progress.append(message)

    def to_public_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")
