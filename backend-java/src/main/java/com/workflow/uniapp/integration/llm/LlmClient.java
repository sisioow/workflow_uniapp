package com.workflow.uniapp.integration.llm;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.workflow.uniapp.dto.RequirementPlan;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Component;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.util.List;
import java.util.Map;

@Slf4j
@Component
@RequiredArgsConstructor
public class LlmClient {

    private final LlmConfig config;
    private final ObjectMapper objectMapper;
    private final HttpClient httpClient = HttpClient.newHttpClient();

    private static final String SYSTEM_PROMPT = """
            你是一名高级移动端 App 产品经理。根据应用名称设计一款纯前端可实现的工具类应用。
            约束：
            - 必须是工具类应用，与应用名称紧密贴合
            - 不要设计个人中心页面
            - 严禁游戏、金融、大范围知识科普、论坛博客方向
            - 强调交互类功能
            - 只设计 4 个一级页面（page1~page4），每个页面 2~4 个核心功能点
            输出严格 JSON，不要 markdown，不要废话：
            {"summary": "一句话产品定位", "pages": [{"key": "page1", "title": "...", "features": ["功能A"], "description": "..."}], "sample_data": ["示例"]}
            """;

    public RequirementPlan analyzeRequirement(String appName) {
        String userMessage = "应用名称：\"" + appName + "\"\n请输出 4 页功能方案 JSON。";
        String raw = chat(SYSTEM_PROMPT, userMessage);
        try {
            // 清理 markdown 包裹
            String cleaned = raw.trim()
                    .replaceAll("^```(?:json)?\\s*", "")
                    .replaceAll("\\s*```$", "");
            return objectMapper.readValue(cleaned, RequirementPlan.class);
        } catch (Exception e) {
            log.warn("LLM JSON 解析失败，使用文本回退: {}", e.getMessage());
            return fallbackPlan(appName, raw);
        }
    }

    private String chat(String system, String user) {
        try {
            var body = objectMapper.writeValueAsString(Map.of(
                    "model", config.getModel(),
                    "messages", List.of(
                            Map.of("role", "system", "content", system),
                            Map.of("role", "user", "content", user)
                    ),
                    "temperature", 0.7
            ));

            var req = HttpRequest.newBuilder()
                    .uri(URI.create(config.getBaseUrl() + "/chat/completions"))
                    .header("Content-Type", "application/json")
                    .header("Authorization", "Bearer " + config.getApiKey())
                    .POST(HttpRequest.BodyPublishers.ofString(body))
                    .build();

            var resp = httpClient.send(req, HttpResponse.BodyHandlers.ofString());
            var node = objectMapper.readTree(resp.body());
            return node.get("choices").get(0).get("message").get("content").asText().strip();
        } catch (Exception e) {
            throw new LlmException("LLM 调用失败: " + e.getMessage(), e);
        }
    }

    private RequirementPlan fallbackPlan(String appName, String raw) {
        var plan = new RequirementPlan();
        plan.setAppName(appName);
        plan.setRawText(raw);
        plan.setPages(List.of(
                new RequirementPlan.PageDesign("page1", "页面1", List.of(), ""),
                new RequirementPlan.PageDesign("page2", "页面2", List.of(), ""),
                new RequirementPlan.PageDesign("page3", "页面3", List.of(), ""),
                new RequirementPlan.PageDesign("page4", "页面4", List.of(), "")
        ));
        return plan;
    }

    public static class LlmException extends RuntimeException {
        public LlmException(String msg, Throwable cause) { super(msg, cause); }
    }
}
