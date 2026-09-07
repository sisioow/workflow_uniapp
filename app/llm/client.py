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

REQUIREMENT_CONSTRAINTS = """你是一名高级移动端 App 产品经理。根据应用名称，设计三套功能方向明显不同、但各自需求完整的纯前端工具类应用方案。

硬性约束：
- 必须是工具类应用，与应用名称紧密贴合
- 三套方案必须是不同功能方向（例如：记录整理型 / 决策计算型 / 习惯流程型等），页面结构与核心功能不得雷同
- 每套方案只设计 4 个一级页面（page1~page4）；不要设计个人中心页面
- 每套方案必须是可独立交付的完整工具：覆盖「进入→完成核心任务→回看/调整→形成闭环」的全流程，禁止只有一两条简单小功能
- 每个一级页须给出 5~8 条可验收功能点；每条写成完整能力（含对象、操作、结果），禁止「添加/删除/查看」这类单动词凑数
- 每个一级页的 description 须写清：布局分区、主操作路径、空态/异常态、以及需要的二级页（如详情/编辑/筛选结果/记录明细）及其职责
- 严禁游戏、金融、大范围知识科普、论坛博客方向
- 强调交互类功能，可在应用商店相似 App 基础上做功能创新
- 【禁止】涉及拍照、相册选图、相机、录音、语音采集、音视频录制、社交分享、分享到微信/朋友圈/微博等外部分发能力（含生成分享海报后外发）
- 可用本地文本/列表/标记/筛选/统计等能力，避免依赖硬件采集与社交传播

输出严格 JSON，不要 markdown，不要废话：
{
  "options": [
    {
      "direction": "功能方向名称（如：清单整理型）",
      "summary": "2~3 句产品定位：服务谁、核心闭环是什么、四页如何组成完整工具",
      "pages": [
        {"key": "page1", "title": "页面标题", "features": ["完整功能点1","完整功能点2","完整功能点3","完整功能点4","完整功能点5"], "description": "布局分区、主路径、空态/异常、二级页职责"},
        {"key": "page2", "title": "...", "features": [], "description": ""},
        {"key": "page3", "title": "...", "features": [], "description": ""},
        {"key": "page4", "title": "...", "features": [], "description": ""}
      ],
      "sample_data": ["示例数据项1", "示例数据项2", "示例数据项3", "示例数据项4", "示例数据项5", "示例数据项6"]
    },
    { "direction": "第二套方向", "summary": "...", "pages": [], "sample_data": [] },
    { "direction": "第三套方向", "summary": "...", "pages": [], "sample_data": [] }
  ]
}

必须恰好输出 3 个 options。每套 pages 必须恰好 4 页；每页 features 至少 5 条；sample_data 至少 6 条且能支撑列表/详情演示。"""


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
    # 需求分析等长响应易触发 CDN 524；拉长超时，并优先流式收取
    self.client = OpenAI(
      api_key=settings.llm_api_key,
      base_url=settings.llm_base_url or None,
      timeout=300.0,
      max_retries=2,
    )

  @staticmethod
  def _friendly_llm_error(exc: BaseException) -> str:
    text = str(exc)
    if "524" in text or "Origin Time-out" in text or "origin time-out" in text.lower():
      return (
        "LLM 网关超时（524 Origin Time-out）：需求分析响应过长被 CDN 切断。"
        "请稍后重试，或换用更快模型（如 deepseek-v4-flash）。"
      )
    if "529" in text or "503" in text:
      return f"LLM 服务繁忙：{text}"
    return text

  def chat(self, system: str, user: str, temperature: float = 0.85) -> str:
    """调用聊天补全。优先流式，降低长响应被 CDN 空闲切断的概率。"""
    messages = [
      {"role": "system", "content": system},
      {"role": "user", "content": user},
    ]
    try:
      stream = self.client.chat.completions.create(
        model=self.model,
        messages=messages,
        temperature=temperature,
        stream=True,
      )
      parts: list[str] = []
      for chunk in stream:
        if not chunk.choices:
          continue
        delta = chunk.choices[0].delta
        if delta and delta.content:
          parts.append(delta.content)
      content = "".join(parts).strip()
      if content:
        return content
      # 流式空内容时回退一次非流式
      response = self.client.chat.completions.create(
        model=self.model,
        messages=messages,
        temperature=temperature,
      )
      return (response.choices[0].message.content or "").strip()
    except Exception as exc:  # noqa: BLE001
      raise RuntimeError(self._friendly_llm_error(exc)) from exc

  def analyze_requirement(self, app_name: str, extra_prompt: str = "") -> list[RequirementPlan]:
    """生成三套不同功能方向的需求方案。extra_prompt 为用户补充要求（可选）。"""
    user_msg = (
      f'应用名称："{app_name}"\n'
      "请先按产品经理头脑风暴节奏（Frame→Diverge→Converge）想清三套不同功能方向，"
      "再为每一套写出完整需求（4 个一级页 + 必要二级页职责），输出恰好 3 套 JSON。"
      "每一套都必须是可独立使用的完整工具，禁止只有一两条简单小功能。"
      "每页 5~8 条完整可验收功能点；description 写清布局、主路径、空态和二级页。"
      "再次确认：禁止照片/录音/分享相关能力。"
      "只输出 JSON，不要解释过程。"
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
