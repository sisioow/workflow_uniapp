from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from typing import Any

from openai import OpenAI

from app.config import ROOT_DIR, get_settings
from app.models import PageDesign, RequirementPlan

logger = logging.getLogger(__name__)

_PM_BRAINSTORM_PROMPT = (
  ROOT_DIR / ".harness" / "skills" / "pm-brainstorm" / "requirement-prompt.md"
)

REQUIREMENT_CONSTRAINTS = """你是一名高级移动端 App 产品经理。根据应用名称，设计三套功能方向明显不同的纯前端工具类应用方案。

硬性约束：
- 必须是工具类应用，与应用名称紧密贴合
- 三套方案必须是不同功能方向（例如：记录整理型 / 决策计算型 / 习惯流程型等），页面结构与核心功能不得雷同
- 每套方案只设计 4 个一级页面（page1~page4），每个页面 2~4 个核心功能点
- 不要设计个人中心页面
- 严禁游戏、金融、大范围知识科普、论坛博客方向
- 强调交互类功能，可在应用商店相似 App 基础上做功能创新
- 【禁止】涉及拍照、相册选图、相机、录音、语音采集、音视频录制、社交分享、分享到微信/朋友圈/微博等外部分发能力（含生成分享海报后外发）
- 可用本地文本/列表/标记/筛选/统计等能力，避免依赖硬件采集与社交传播

输出严格 JSON，不要 markdown，不要废话：
{
  "options": [
    {
      "direction": "功能方向名称（如：清单整理型）",
      "summary": "一句话产品定位",
      "pages": [
        {"key": "page1", "title": "页面标题", "features": ["功能A","功能B"], "description": "布局与交互说明"},
        {"key": "page2", "title": "...", "features": [], "description": ""},
        {"key": "page3", "title": "...", "features": [], "description": ""},
        {"key": "page4", "title": "...", "features": [], "description": ""}
      ],
      "sample_data": ["示例数据项1", "示例数据项2"]
    },
    { "direction": "第二套方向", "summary": "...", "pages": [], "sample_data": [] },
    { "direction": "第三套方向", "summary": "...", "pages": [], "sample_data": [] }
  ]
}

必须恰好输出 3 个 options。"""


@lru_cache
def _load_pm_brainstorm_brief() -> str:
  """加载 .harness/skills/pm-brainstorm 注入片段；缺失时用内置兜底。"""
  path = _PM_BRAINSTORM_PROMPT
  try:
    text = path.read_text(encoding="utf-8").strip()
    # 去掉 markdown 一级标题，避免与 system 角色重复喧宾
    lines = [ln for ln in text.splitlines() if not ln.startswith("# ")]
    brief = "\n".join(lines).strip()
    if brief:
      return brief
  except OSError as exc:
    logger.warning("读取 pm-brainstorm 提示词失败，使用内置兜底: %s", exc)
  return (
    "先 Frame（谁/结果/非目标），再用 JTBD/HMW 发散至少 3 条可互相替代的功能方向，"
    "施压检查禁止能力与 4 页闭环后，收敛为实质不同的三套方案再输出 JSON。"
  )


def build_requirement_system_prompt() -> str:
  """需求分析 system prompt = 硬约束 + PM 头脑风暴 skill 简报。"""
  return f"{REQUIREMENT_CONSTRAINTS}\n\n{_load_pm_brainstorm_brief()}"


class LlmClient:
  def __init__(self, model: str | None = None) -> None:
    settings = get_settings()
    self.model = model or settings.llm_model
    self.client = OpenAI(
      api_key=settings.llm_api_key,
      base_url=settings.llm_base_url or None,
    )

  def chat(self, system: str, user: str, temperature: float = 0.85) -> str:
    response = self.client.chat.completions.create(
      model=self.model,
      messages=[
        {"role": "system", "content": system},
        {"role": "user", "content": user},
      ],
      temperature=temperature,
    )
    content = response.choices[0].message.content or ""
    return content.strip()

  def analyze_requirement(self, app_name: str, extra_prompt: str = "") -> list[RequirementPlan]:
    """生成三套不同功能方向的需求方案。extra_prompt 为用户补充要求（可选）。"""
    user_msg = (
      f'应用名称："{app_name}"\n'
      "请先按产品经理头脑风暴节奏（Frame→Diverge→Converge）想清三套方向，"
      "再输出恰好 3 套不同功能方向的 4 页方案 JSON。"
      "再次确认：禁止照片/录音/分享相关能力。"
    )
    hint = (extra_prompt or "").strip()
    if hint:
      user_msg += (
        f"\n\n用户补充要求（请映射为约束或 HMW 后重新发散三套方向，"
        f"允许整体替换，勿仅微调旧案；仍遵守硬性约束）：\n{hint}"
      )
    raw = self.chat(build_requirement_system_prompt(), user_msg)
    try:
      data = self._parse_json(raw)
      options_raw = data.get("options")
      # 兼容旧格式：单方案顶层 pages
      if not isinstance(options_raw, list) or not options_raw:
        if data.get("pages"):
          options_raw = [data]
        else:
          raise KeyError("options")

      plans: list[RequirementPlan] = []
      for i, opt in enumerate(options_raw[:3]):
        if not isinstance(opt, dict):
          continue
        plans.append(self._plan_from_dict(app_name, opt, raw_text=raw if i == 0 else ""))

      while len(plans) < 3:
        idx = len(plans) + 1
        plans.append(
          RequirementPlan(
            app_name=app_name,
            direction=f"备用方向{idx}",
            summary=f"{app_name}备用方案{idx}",
            pages=self._empty_pages(),
            raw_text=raw if idx == 1 else "",
          )
        )
      return plans[:3]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
      logger.warning("LLM JSON 解析失败，使用文本回退: %s", exc)
      fallback = RequirementPlan(
        app_name=app_name,
        direction="默认方案",
        summary="",
        pages=self._fallback_pages_from_text(raw),
        raw_text=raw,
      )
      return [
        fallback,
        RequirementPlan(
          app_name=app_name,
          direction="备选方向二",
          summary="",
          pages=self._empty_pages(),
        ),
        RequirementPlan(
          app_name=app_name,
          direction="备选方向三",
          summary="",
          pages=self._empty_pages(),
        ),
      ]

  def _plan_from_dict(self, app_name: str, data: dict[str, Any], raw_text: str = "") -> RequirementPlan:
    pages = [
      PageDesign(
        key=p.get("key", f"page{i+1}"),
        title=p.get("title", f"页面{i+1}"),
        features=p.get("features") or [],
        description=p.get("description", ""),
      )
      for i, p in enumerate(data.get("pages") or [])
    ]
    while len(pages) < 4:
      idx = len(pages) + 1
      pages.append(PageDesign(key=f"page{idx}", title=f"页面{idx}"))
    pages = pages[:4]

    return RequirementPlan(
      app_name=app_name,
      direction=(data.get("direction") or data.get("name") or "").strip(),
      summary=data.get("summary", "") or "",
      pages=pages,
      sample_data=data.get("sample_data") or [],
      raw_text=raw_text,
    )

  @staticmethod
  def _empty_pages() -> list[PageDesign]:
    return [PageDesign(key=f"page{i}", title=f"页面{i}") for i in range(1, 5)]

  @staticmethod
  def _parse_json(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    return json.loads(cleaned)

  @staticmethod
  def _fallback_pages_from_text(text: str) -> list[PageDesign]:
    pages: list[PageDesign] = []
    for i in range(1, 5):
      key = f"page{i}"
      match = re.search(rf"\[?{key}\]?[：:]\s*(.+)", text, re.IGNORECASE)
      title = match.group(1).strip() if match else f"页面{i}"
      pages.append(PageDesign(key=key, title=title[:40], features=[]))
    return pages
