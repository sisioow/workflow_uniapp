package com.workflow.uniapp.integration.stitch;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Component;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.Map;
import java.util.concurrent.atomic.AtomicInteger;

/**
 * Google Stitch MCP HTTP JSON-RPC 客户端。
 * 对应 Python 版 StitchMcpClient。
 */
@Slf4j
@Component
@RequiredArgsConstructor
public class StitchClient {

    private final StitchConfig config;
    private final ObjectMapper objectMapper;
    private final AtomicInteger requestId = new AtomicInteger(0);
    private String oauthToken;

    private final HttpClient httpClient = HttpClient.newBuilder()
            .connectTimeout(Duration.ofSeconds(30))
            .build();

    /** 初始化 + 获取 OAuth token */
    public void initialize() {
        oauthToken = resolveOAuthToken();
        var result = post("initialize", Map.of(
                "protocolVersion", "2024-11-05",
                "capabilities", Map.of(),
                "clientInfo", Map.of("name", "workflow-uniapp", "version", "2.0.0")
        ), false);
        log.info("Stitch MCP initialized. oauth={}", oauthToken != null);
    }

    public String createProject(String title) {
        var result = callTool("create_project", Map.of("title", title), false);
        String name = result.has("name") ? result.get("name").asText() : "";
        if (name.isEmpty()) throw new StitchException("create_project 未返回 project id");
        return name.contains("/") ? name.substring(name.lastIndexOf('/') + 1) : name;
    }

    public String generateScreen(String projectId, String prompt) {
        var result = callTool("generate_screen_from_text", Map.of(
                "projectId", projectId,
                "prompt", prompt,
                "deviceType", "MOBILE",
                "modelId", "GEMINI_3_FLASH"
        ), true);
        // 与 Python 版相同的 screen_id 提取逻辑
        String name = result.has("name") ? result.get("name").asText() :
                      result.has("screenId") ? result.get("screenId").asText() : "";
        if (name.isEmpty()) throw new StitchException("generate_screen 未返回 screen id: " + result);
        return name.contains("/screens/") ? name.substring(name.indexOf("/screens/") + 9) : name;
    }

    public JsonNode getScreen(String projectId, String screenId) {
        return callTool("get_screen", Map.of(
                "name", "projects/" + projectId + "/screens/" + screenId,
                "projectId", projectId,
                "screenId", screenId
        ), true);
    }

    // ── 内部方法 ──

    private JsonNode callTool(String name, Map<String, Object> arguments, boolean useOAuth) {
        var result = post("tools/call", Map.of("name", name, "arguments", arguments), useOAuth);
        return parseToolResult(result);
    }

    private JsonNode parseToolResult(JsonNode result) {
        if (result.has("structuredContent") && !result.get("structuredContent").isNull()) {
            return result.get("structuredContent");
        }
        if (result.has("content")) {
            for (var item : result.get("content")) {
                if ("text".equals(item.get("type").asText()) && item.has("text")) {
                    try {
                        return objectMapper.readTree(item.get("text").asText());
                    } catch (Exception e) {
                        return objectMapper.createObjectNode().put("text", item.get("text").asText());
                    }
                }
            }
        }
        return result;
    }

    private JsonNode post(String method, Map<String, Object> params, boolean useOAuth) {
        try {
            var payload = objectMapper.createObjectNode();
            payload.put("jsonrpc", "2.0");
            payload.put("id", requestId.incrementAndGet());
            payload.put("method", method);
            if (params != null) payload.set("params", objectMapper.valueToTree(params));

            var reqBuilder = HttpRequest.newBuilder()
                    .uri(URI.create(config.getMcpUrl()))
                    .header("Content-Type", "application/json")
                    .header("Accept", "application/json, text/event-stream")
                    .timeout(Duration.ofMinutes(10));

            if (useOAuth && oauthToken != null) {
                reqBuilder.header("Authorization", "Bearer " + oauthToken);
            } else {
                reqBuilder.header("X-Goog-Api-Key", config.getApiKey());
            }

            reqBuilder.POST(HttpRequest.BodyPublishers.ofString(objectMapper.writeValueAsString(payload)));
            var response = httpClient.send(reqBuilder.build(), HttpResponse.BodyHandlers.ofString());
            String body = response.body().strip();

            if (body.startsWith("data:")) {
                for (String line : body.split("\n")) {
                    if (line.startsWith("data:")) { body = line.substring(5).strip(); break; }
                }
            }

            var result = objectMapper.readTree(body);
            if (result.has("error")) {
                throw new StitchException("Stitch API 错误: " + result.get("error"));
            }
            return result.has("result") ? result.get("result") : result;

        } catch (StitchException e) { throw e;
        } catch (Exception e) {
            throw new StitchException("网络请求失败: " + e.getMessage(), e);
        }
    }

    /** 5 级降级获取 OAuth token */
    private String resolveOAuthToken() {
        // 1) 配置中的静态 token
        if (config.getOauthToken() != null && !config.getOauthToken().isBlank()) return config.getOauthToken();
        // 2) Google ADC
        try {
            var creds = com.google.auth.oauth2.GoogleCredentials.getApplicationDefault()
                    .createScoped("https://www.googleapis.com/auth/cloud-platform");
            creds.refreshIfExpired();
            if (creds.getAccessToken() != null) return creds.getAccessToken().getTokenValue();
        } catch (Exception e) { log.debug("ADC failed: {}", e.getMessage()); }
        // 3-4) gcloud CLI
        try {
            for (String[] cmd : new String[][]{
                    {"gcloud", "auth", "print-access-token"},
                    {"gcloud", "auth", "application-default", "print-access-token"}}) {
                var proc = new ProcessBuilder(cmd).redirectErrorStream(true).start();
                String out = new String(proc.getInputStream().readAllBytes()).strip();
                if (proc.waitFor() == 0 && !out.isBlank()) return out;
            }
        } catch (Exception e) { log.debug("gcloud failed: {}", e.getMessage()); }
        return null;
    }

    public static class StitchException extends RuntimeException {
        public StitchException(String msg) { super(msg); }
        public StitchException(String msg, Throwable cause) { super(msg, cause); }
    }
}
