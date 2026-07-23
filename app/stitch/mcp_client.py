from __future__ import annotations

import json
import logging
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

# 「Request contains an invalid argument」多为 Stitch/Gemini 瞬时拒识，生成通常未启动，可有限重试。
# 超时/连接类失败仍不重试（官方：生成可能仍在后台进行）。
_RETRYABLE_GEN_MARKERS = (
    "invalid argument",
    "invalid_argument",
    "invalidargument",
)


def _is_retryable_generate_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    if "timeout" in text or "timed out" in text:
        return False
    return any(m in text for m in _RETRYABLE_GEN_MARKERS)

# Stitch MCP 需要的 Google Cloud OAuth scope
_OAUTH_SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]
# Google Device OAuth 端点
_DEVICE_AUTH_URL = "https://accounts.google.com/o/oauth2/device/code"
_TOKEN_URL = "https://oauth2.googleapis.com/token"


class StitchMcpError(RuntimeError):
    pass


class StitchMcpClient:
  """Google Stitch MCP HTTP JSON-RPC 客户端。"""

  def __init__(self) -> None:
    settings = get_settings()
    self.url = settings.stitch_mcp_url
    self.api_key = settings.stitch_api_key
    self._request_id = 0
    self._oauth_token: str | None = None

  def _next_id(self) -> int:
    self._request_id += 1
    return self._request_id

  def _build_headers(self, use_oauth: bool = False) -> dict[str, str]:
    """构建请求头。

    优先策略：
    - use_oauth=False（默认推荐）：只发 X-Goog-Api-Key
    - use_oauth=True：只发 Bearer（不可与 API Key 混用，否则 401）
    - OAuth 请求但无 token：自动回退 API Key，避免裸请求导致 403
    """
    headers: dict[str, str] = {
      "Content-Type": "application/json",
      "Accept": "application/json, text/event-stream",
    }
    if use_oauth and self._oauth_token:
      headers["Authorization"] = f"Bearer {self._oauth_token}"
    elif self.api_key:
      headers["X-Goog-Api-Key"] = self.api_key
    return headers

  @staticmethod
  def _extract_oauth_token(data: dict[str, Any]) -> str | None:
    """从 initialize 响应中提取 OAuth 2 access token。

    Google Stitch MCP 可能在多种字段中返回 token，
    按优先级尝试：access_token → token → auth_token → oauth_token。
    """
    candidates: dict[str, Any] = {}

    # 1) 直接在 result 顶层
    result = data.get("result", data)
    if isinstance(result, dict):
      candidates = result

    # 2) 在 capabilities 或 serverInfo 中
    for sub_key in ("serverInfo", "capabilities", "auth", "credentials"):
      sub = candidates.get(sub_key)
      if isinstance(sub, dict):
        candidates.update(sub)

    for key in ("access_token", "accessToken", "token", "auth_token", "authToken", "oauth_token", "bearer_token"):
      val = candidates.get(key)
      if isinstance(val, str) and val.strip():
        logger.info(f"Stitch OAuth token 已从字段 '{key}' 提取（长度 {len(val)}）")
        return val.strip()

    # 3) 在 HTTP 响应头中也可能携带（通过 httpx response headers）
    logger.debug(f"未在 initialize 响应中找到 OAuth token，候选字段: {list(candidates.keys())[:10]}")
    return None

  def _post(self, method: str, params: dict[str, Any] | None = None, *, use_oauth: bool = False) -> dict[str, Any]:
    if not self.api_key and not (use_oauth and self._oauth_token):
      raise StitchMcpError("未配置 STITCH_API_KEY，请在 local_config.yaml 的 stitch.api_key 中设置")

    logger.info(f"Stitch MCP 请求: {method}")

    payload: dict[str, Any] = {
      "jsonrpc": "2.0",
      "id": self._next_id(),
      "method": method,
    }
    if params is not None:
      payload["params"] = params

    # OAuth 易因未设置 quota project 返回 403；API Key 当前可用，默认优先 API Key。
    # 若显式要求 OAuth 且失败，再自动回退 API Key。
    modes: list[bool] = []
    if use_oauth and self._oauth_token:
      modes.append(True)
    modes.append(False)
    # 去重并保持顺序
    seen: set[bool] = set()
    auth_modes = [m for m in modes if not (m in seen or seen.add(m))]

    last_error: Exception | None = None
    for oauth_mode in auth_modes:
      headers = self._build_headers(use_oauth=oauth_mode)
      auth_info = "OAuth" if ("Authorization" in headers) else "API-Key"
      logger.info(f"认证方式: {auth_info}")
      try:
        with httpx.Client(timeout=600.0) as client:
          resp = client.post(self.url, headers=headers, json=payload)
          # OAuth 403/401 时尝试下一认证模式
          if resp.status_code in (401, 403) and oauth_mode and False in auth_modes:
            logger.warning(
              f"Stitch {auth_info} 返回 {resp.status_code}，回退 API Key 重试。"
              f" 响应片段: {resp.text[:180]}"
            )
            last_error = StitchMcpError(
              f"OAuth 认证失败 ({resp.status_code})，已尝试回退 API Key"
            )
            continue
          resp.raise_for_status()
          body = resp.text.strip()

          if body.startswith("data:"):
            for line in body.splitlines():
              if line.startswith("data:"):
                body = line[5:].strip()
                break

          data = json.loads(body)
          if "error" in data:
            error_msg = json.dumps(data["error"], ensure_ascii=False)
            logger.error(f"Stitch MCP 错误: {error_msg}")
            raise StitchMcpError(f"Stitch API 错误: {error_msg}")

          result = data.get("result", {})
          # JSON-RPC 层 isError（部分 403 内容会以 200 + isError 返回）
          if isinstance(result, dict) and result.get("isError"):
            text = ""
            for item in result.get("content") or []:
              if isinstance(item, dict) and item.get("type") == "text":
                text = item.get("text") or ""
                break
            if oauth_mode and False in auth_modes and (
              "quota project" in text.lower()
              or "authenticating" in text.lower()
              or "forbidden" in text.lower()
            ):
              logger.warning(f"Stitch OAuth 业务错误，回退 API Key：{text[:180]}")
              last_error = StitchMcpError(text or "OAuth isError")
              continue
            raise StitchMcpError(text or "Stitch 工具返回 isError")

          logger.info(f"Stitch MCP 响应成功: {method} ({auth_info})")
          return result if isinstance(result, dict) else {"result": result}
      except httpx.HTTPStatusError as e:
        last_error = e
        if e.response is not None and e.response.status_code in (401, 403) and oauth_mode:
          continue
        logger.error(f"HTTP 请求失败: {e}")
        raise StitchMcpError(f"网络请求失败: {str(e)}") from e
      except httpx.HTTPError as e:
        logger.error(f"HTTP 请求失败: {e}")
        raise StitchMcpError(f"网络请求失败: {str(e)}") from e
      except json.JSONDecodeError as e:
        logger.error(f"JSON 解析失败: {e}")
        raise StitchMcpError(f"JSON 解析失败: {str(e)}") from e

    if last_error:
      raise StitchMcpError(f"网络请求失败: {last_error}") from last_error
    raise StitchMcpError("Stitch 请求失败：无可用认证方式")

  def initialize(self) -> dict[str, Any]:
    """初始化 MCP 会话。

    默认用 STITCH_API_KEY 即可完成设计生成。
    OAuth（可选）仅在调用方显式要求时使用；gcloud ADC 需单独设置 quota project，
    否则易 403，因此不会默认强制走 OAuth。
    """
    token = self._get_oauth_token_from_env_or_gcloud()
    if token:
      self._oauth_token = token
      logger.info("检测到 OAuth token（可选；工具调用默认仍优先 API Key）")
    elif not self.api_key:
      raise StitchMcpError("未配置 STITCH_API_KEY，请在 local_config.yaml 的 stitch.api_key 中设置")
    else:
      logger.info("使用 STITCH_API_KEY 认证（推荐）")

    # 发送 MCP initialize 请求
    result = self._post(
      "initialize",
      {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "workflow-uniapp", "version": "1.0.0"},
      },
    )

    # 同时尝试从响应体中提取 token（备用路径）
    if not self._oauth_token:
      token = self._extract_oauth_token(result)
      if token:
        self._oauth_token = token

    return result

  def _get_oauth_token_from_env_or_gcloud(self) -> str | None:
    """获取 Google OAuth 2 access token（多策略降级）。

    优先级：
    1. STITCH_OAUTH_TOKEN 环境变量（直接填入）
    2. Google ADC（gcloud auth application-default login 后可用）
    3. gcloud 用户账号（gcloud auth login 后可用）
    4. 自定义 OAuth Client（STITCH_OAUTH_CLIENT_ID / _SECRET）
    """
    settings = get_settings()

    # 1) 环境变量
    if settings.stitch_oauth_token:
      logger.info("✅ 使用 STITCH_OAUTH_TOKEN 环境变量")
      return settings.stitch_oauth_token

    # 2) Google ADC
    token = self._get_adc_token()
    if token:
      logger.info("✅ 通过 Google ADC 获取 OAuth token")
      return token

    # 3) gcloud 用户账号
    token = self._get_gcloud_user_token()
    if token:
      logger.info("✅ 通过 gcloud 用户账号获取 OAuth token")
      return token

    # 4) gcloud application-default
    token = self._get_gcloud_token()
    if token:
      logger.info("✅ 通过 gcloud ADC CLI 获取 OAuth token")
      return token

    # 5) 自定义 OAuth Client
    token = self._get_custom_oauth_token()
    if token:
      logger.info("✅ 通过自定义 OAuth Client 获取 token")
      return token

    logger.warning(
      "❌ 所有 OAuth token 获取方式均失败。\n"
      "   请尝试以下任一方式：\n"
      "   1. 运行 gcloud auth application-default login\n"
      "   2. 在 .env 中设置 STITCH_OAUTH_TOKEN=<你的token>\n"
      "   3. 在 .env 中设置 STITCH_OAUTH_CLIENT_ID 和 STITCH_OAUTH_CLIENT_SECRET"
    )
    return None

  @staticmethod
  def _get_adc_token() -> str | None:
    """通过 Google Application Default Credentials 获取 token。"""
    try:
      import google.auth
      import google.auth.transport.requests

      credentials, project = google.auth.default(scopes=_OAUTH_SCOPES)
      auth_request = google.auth.transport.requests.Request()
      credentials.refresh(auth_request)
      if credentials.token:
        return credentials.token
    except Exception as exc:
      logger.debug(f"ADC 获取 token 失败: {exc}")
    return None

  @staticmethod
  def _get_gcloud_user_token() -> str | None:
    """通过 gcloud 用户账号获取 token（gcloud auth login）。"""
    try:
      result = subprocess.run(
        ["gcloud", "auth", "print-access-token"],
        capture_output=True,
        text=True,
        timeout=15,
      )
      if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    except Exception:
      pass
    return None

  @staticmethod
  def _get_custom_oauth_token() -> str | None:
    """通过自定义 OAuth Client ID 获取 token。

    需要 .env 中配置 STITCH_OAUTH_CLIENT_ID 和 STITCH_OAUTH_CLIENT_SECRET。
    可在 Google Cloud Console 创建 OAuth 2.0 客户端 ID 获取。
    """
    settings = get_settings()
    client_id = getattr(settings, "stitch_oauth_client_id", "") or ""
    client_secret = getattr(settings, "stitch_oauth_client_secret", "") or ""

    if not client_id or not client_secret:
      return None

    try:
      import google_auth_oauthlib.flow

      flow = google_auth_oauthlib.flow.InstalledAppFlow.from_client_config(
        {
          "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": _TOKEN_URL,
          }
        },
        scopes=_OAUTH_SCOPES,
      )

      logger.info("正在启动浏览器进行 Google 账号授权...")
      credentials = flow.run_local_server(
        port=0,
        authorization_prompt_message="请在弹出的浏览器中登录 Google 账号并授权 Stitch MCP。",
        success_message="✅ Google 授权成功！正在生成设计稿...",
        open_browser=True,
      )
      if credentials and credentials.token:
        return credentials.token
    except Exception as exc:
      logger.debug(f"自定义 OAuth 授权失败: {exc}")
    return None

  @staticmethod
  def _get_gcloud_token() -> str | None:
    """通过 gcloud CLI 获取 token（兜底）。"""
    try:
      result = subprocess.run(
        ["gcloud", "auth", "application-default", "print-access-token"],
        capture_output=True,
        text=True,
        timeout=15,
      )
      if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    except Exception:
      pass
    return None

  def call_tool(self, name: str, arguments: dict[str, Any], *, use_oauth: bool = False) -> dict[str, Any]:
    """调用 MCP 工具。默认 API Key（实测可完成 create/generate）；OAuth 仅作可选增强。"""
    result = self._post(
      "tools/call",
      {"name": name, "arguments": arguments},
      use_oauth=use_oauth,
    )
    return self._parse_tool_result(result)

  @staticmethod
  def _parse_tool_result(result: dict[str, Any]) -> dict[str, Any]:
    if "structuredContent" in result and result["structuredContent"]:
      return result["structuredContent"]

    content = result.get("content") or []
    for item in content:
      if item.get("type") == "text" and item.get("text"):
        text = item["text"]
        try:
          return json.loads(text)
        except json.JSONDecodeError:
          return {"text": text}
    return result

  def create_project(self, title: str) -> str:
    logger.info(f"创建 Stitch 项目: {title}")
    data = self.call_tool("create_project", {"title": title}, use_oauth=False)
    logger.debug(f"create_project 响应: {data}")

    name = data.get("name") or data.get("projectId") or ""
    if not name:
      logger.error(f"create_project 未返回 project id: {data}")
      raise StitchMcpError(f"create_project 未返回 project id: {data}")

    project_id = name.split("/")[-1] if "/" in name else str(name)
    logger.info(f"Stitch 项目创建成功: {project_id}")
    return project_id

  @staticmethod
  def _extract_screen_ref(data: dict[str, Any]) -> str:
    """从 generate_screen 响应中提取首个 screen name/id（兼容新旧字段结构）。"""
    refs = StitchMcpClient._extract_all_screen_refs(data)
    return refs[0] if refs else ""

  @staticmethod
  def _extract_all_screen_refs(data: dict[str, Any]) -> list[str]:
    """从响应中提取全部 screen name/id，去重并保持顺序。"""
    candidates: list[Any] = [
      data.get("name"),
      data.get("screenId"),
      data.get("id"),
      (data.get("screen") or {}).get("name") if isinstance(data.get("screen"), dict) else None,
      (data.get("screen") or {}).get("id") if isinstance(data.get("screen"), dict) else None,
    ]

    for screen in data.get("screens") or []:
      if isinstance(screen, dict):
        candidates.extend([screen.get("name"), screen.get("id"), screen.get("screenId")])

    for comp in data.get("outputComponents") or []:
      if not isinstance(comp, dict):
        continue
      design = comp.get("design") if isinstance(comp.get("design"), dict) else {}
      for screen in design.get("screens") or []:
        if isinstance(screen, dict):
          candidates.extend([screen.get("name"), screen.get("id"), screen.get("screenId")])
      screen = comp.get("screen")
      if isinstance(screen, dict):
        candidates.extend([screen.get("name"), screen.get("id")])

    seen: set[str] = set()
    result: list[str] = []
    for raw in candidates:
      if not isinstance(raw, str) or not raw.strip():
        continue
      sid = StitchMcpClient._normalize_screen_id(raw.strip())
      if sid and sid not in seen:
        seen.add(sid)
        result.append(sid)
    return result

  @staticmethod
  def _normalize_screen_id(screen_name: str) -> str:
    if "/screens/" in screen_name:
      return screen_name.split("/screens/")[-1]
    if "/" in screen_name:
      return screen_name.split("/")[-1]
    return screen_name

  def list_screens(self, project_id: str) -> list[str]:
    """列出项目下全部 screen id。"""
    data = self.call_tool("list_screens", {"projectId": project_id}, use_oauth=False)
    ids = self._extract_all_screen_refs(data)
    if ids:
      return ids
    # 兼容 list 顶层 screens / screenNames
    for key in ("screens", "screenNames", "items"):
      items = data.get(key)
      if not isinstance(items, list):
        continue
      for item in items:
        if isinstance(item, str) and item.strip():
          sid = self._normalize_screen_id(item.strip())
          if sid and sid not in ids:
            ids.append(sid)
        elif isinstance(item, dict):
          for k in ("name", "id", "screenId"):
            val = item.get(k)
            if isinstance(val, str) and val.strip():
              sid = self._normalize_screen_id(val.strip())
              if sid and sid not in ids:
                ids.append(sid)
              break
    return ids

  def generate_screen(
    self,
    project_id: str,
    prompt: str,
    device_type: str = "MOBILE",
  ) -> str:
    """兼容单屏：返回首个 screen id。"""
    ids = self.generate_screens(project_id, prompt, device_type=device_type)
    return ids[0]

  def generate_screens(
    self,
    project_id: str,
    prompt: str,
    device_type: str = "MOBILE",
    expected_count: int = 4,
    *,
    max_retries: int = 2,
  ) -> list[str]:
    """一次 generate_screen_from_text，尽量拿到多页 screen id。

    若响应中屏数不足，再 list_screens 补齐（仍算同一次生成会话的结果收集）。
    对「invalid argument」类瞬时错误有限重试；超时不重试。
    """
    logger.info(
      "生成屏幕设计(批量): project=%s, device=%s, expect=%s",
      project_id,
      device_type,
      expected_count,
    )
    logger.debug("提示词长度: %s 字符", len(prompt))

    last_exc: Exception | None = None
    data: dict[str, Any] | None = None
    attempts = max(1, int(max_retries) + 1)

    for attempt in range(1, attempts + 1):
      try:
        data = self.call_tool(
          "generate_screen_from_text",
          {
            "projectId": project_id,
            "prompt": prompt,
            "deviceType": device_type,
            "modelId": "GEMINI_3_FLASH",
          },
          use_oauth=False,
        )
        last_exc = None
        break
      except StitchMcpError as exc:
        last_exc = exc
        if attempt >= attempts or not _is_retryable_generate_error(exc):
          # 瞬时失败也可能已落盘，先捞一次已有屏幕
          recovered = self._try_list_screen_ids(project_id, expected_count)
          if recovered:
            logger.warning(
              "generate_screen 报错但 list_screens 已拿到结果，按成功处理: %s",
              recovered,
            )
            return recovered
          raise
        wait_sec = min(8.0, 2.0 * attempt)
        logger.warning(
          "generate_screen 瞬时失败（%s/%s），%.0fs 后重试: %s",
          attempt,
          attempts,
          wait_sec,
          exc,
        )
        time.sleep(wait_sec)

    if data is None:
      if last_exc:
        raise last_exc
      raise StitchMcpError("generate_screens 未获得响应")

    logger.debug("generate_screen_from_text 响应 keys: %s", list(data.keys()))

    screen_ids = self._extract_all_screen_refs(data)
    if len(screen_ids) < expected_count:
      for sid in self._try_list_screen_ids(project_id, expected_count + 4):
        if sid not in screen_ids:
          screen_ids.append(sid)

    if not screen_ids:
      logger.error("generate_screens 未返回 screen id: %s", list(data.keys()))
      raise StitchMcpError(
        "generate_screens 未返回 screen id（响应含 keys: "
        f"{list(data.keys())}）"
      )

    if expected_count > 0:
      screen_ids = screen_ids[:expected_count]

    logger.info("屏幕设计生成成功: %s", ", ".join(screen_ids))
    return screen_ids

  def _try_list_screen_ids(self, project_id: str, limit: int = 8) -> list[str]:
    try:
      listed = self.list_screens(project_id)
      return listed[:limit] if limit > 0 else listed
    except StitchMcpError as exc:
      logger.warning("list_screens 补充失败: %s", exc)
      return []

  def get_screen(self, project_id: str, screen_id: str) -> dict[str, Any]:
    return self.call_tool(
      "get_screen",
      {
        "name": f"projects/{project_id}/screens/{screen_id}",
        "projectId": project_id,
        "screenId": screen_id,
      },
    )

  @staticmethod
  def extract_screenshot_url(screen_data: dict[str, Any]) -> str:
    """从 get_screen 响应中提取截图下载 URL。"""
    shot = screen_data.get("screenshot")
    if isinstance(shot, dict):
      url = shot.get("downloadUrl") or shot.get("url") or ""
      if isinstance(url, str) and url.strip():
        return url.strip()
    for key in ("screenshotUrl", "imageUrl", "downloadUrl"):
      val = screen_data.get(key)
      if isinstance(val, str) and val.strip().startswith("http"):
        return val.strip()
    return ""

  @staticmethod
  def extract_html_url(screen_data: dict[str, Any]) -> str:
    """从 get_screen 响应中提取 HTML 下载 URL。"""
    html = screen_data.get("htmlCode") or screen_data.get("html")
    if isinstance(html, dict):
      url = html.get("downloadUrl") or html.get("url") or ""
      if isinstance(url, str) and url.strip():
        return url.strip()
    for key in ("htmlUrl", "htmlDownloadUrl"):
      val = screen_data.get(key)
      if isinstance(val, str) and val.strip().startswith("http"):
        return val.strip()
    return ""

  def download_url_to_file(self, url: str, dest: Path) -> Path:
    """下载远程资源到本地文件；保留调用方指定的扩展名。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=120.0, follow_redirects=True) as client:
      resp = client.get(url)
      resp.raise_for_status()
      data = resp.content

    dest.write_bytes(data)
    logger.info("已下载 %s (%s bytes) -> %s", url[:80], len(data), dest)
    return dest

  def download_screen_assets(
    self,
    project_id: str,
    screen_id: str,
    image_dest: Path,
    html_dest: Path,
  ) -> tuple[Path, Path]:
    """下载单屏截图 PNG + HTML 到指定路径。"""
    data = self.get_screen(project_id, screen_id)
    img_url = self.extract_screenshot_url(data)
    html_url = self.extract_html_url(data)
    if not img_url:
      raise StitchMcpError(f"screen {screen_id} 未返回 screenshot.downloadUrl")
    if not html_url:
      raise StitchMcpError(f"screen {screen_id} 未返回 htmlCode.downloadUrl")

    image_path = self.download_url_to_file(img_url, image_dest)
    html_path = self.download_url_to_file(html_url, html_dest)
    return image_path, html_path

  def download_screen_image(
    self,
    project_id: str,
    screen_id: str,
    dest: Path,
  ) -> Path:
    """仅下载截图（兼容旧调用）。"""
    data = self.get_screen(project_id, screen_id)
    url = self.extract_screenshot_url(data)
    if not url:
      raise StitchMcpError(f"screen {screen_id} 未返回 screenshot.downloadUrl")
    return self.download_url_to_file(url, dest)
