package com.workflow.uniapp.controller;

import com.workflow.uniapp.entity.WorkflowSession;
import com.workflow.uniapp.service.TaskManager;
import com.workflow.uniapp.service.WorkflowService;
import jakarta.validation.Valid;
import jakarta.validation.constraints.*;
import lombok.Data;
import lombok.RequiredArgsConstructor;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;
import reactor.core.publisher.Flux;

import java.time.Duration;
import java.util.List;
import java.util.Map;

/**
 * 工作流 REST API — 对应 Python 版 main.py 的所有路由。
 */
@RestController
@RequestMapping("/api")
@RequiredArgsConstructor
public class WorkflowController {

    private final WorkflowService workflowService;
    private final TaskManager taskManager;

    // ── 会话管理 ──

    @GetMapping("/sessions")
    public List<Map<String, String>> listSessions() {
        // 返回摘要列表，供前端侧边栏
        return workflowService.listSessionSummaries();
    }

    @GetMapping("/session/{sessionId}")
    public WorkflowSession getSession(@PathVariable String sessionId) {
        return workflowService.getSession(sessionId);
    }

    @DeleteMapping("/session/{sessionId}")
    public ResponseEntity<Map<String, String>> deleteSession(@PathVariable String sessionId) {
        workflowService.deleteSession(sessionId);
        return ResponseEntity.ok(Map.of("ok", "true", "message", "会话 " + sessionId + " 已删除"));
    }

    // ── 步骤 1-2: 启动 → 需求分析 ──

    @PostMapping("/session/start")
    public WorkflowSession start(@Valid @RequestBody StartRequest body) {
        return workflowService.start(body.appName, body.projectDir, body.llmModel);
    }

    @PostMapping("/session/{sessionId}/analyze")
    public WorkflowSession retryAnalyze(@PathVariable String sessionId) {
        return workflowService.retryAnalyze(sessionId);
    }

    // ── 步骤 3: 需求审核 ──

    @PutMapping("/session/{sessionId}/requirement")
    public WorkflowSession updateRequirement(@PathVariable String sessionId, @RequestBody Map<String, Object> updates) {
        return workflowService.updateRequirement(sessionId, updates);
    }

    @PostMapping("/session/{sessionId}/confirm-requirement")
    public WorkflowSession confirmRequirement(@PathVariable String sessionId) {
        return workflowService.confirmRequirement(sessionId);
    }

    // ── 步骤 4: 选择风格 ──

    @PostMapping("/session/{sessionId}/styles")
    public WorkflowSession setStyles(@PathVariable String sessionId, @Valid @RequestBody StyleSelection body) {
        List<Map<String, String>> styles = buildStyleList(body);
        return workflowService.setStyles(sessionId, styles);
    }

    // ── 步骤 5: 设计 ──

    @PostMapping("/session/{sessionId}/design")
    public ResponseEntity<Map<String, String>> runDesign(@PathVariable String sessionId) {
        String taskId = taskManager.submit(sessionId, "design",
                () -> { workflowService.runDesign(sessionId); return null; });
        return ResponseEntity.ok(Map.of("ok", "true", "message", "设计任务已启动", "taskId", taskId));
    }

    @PostMapping("/session/{sessionId}/skip-design")
    public WorkflowSession skipDesign(@PathVariable String sessionId) {
        return workflowService.skipDesign(sessionId);
    }

    // ── 步骤 6-7: 选择方案 + 代码生成 ──

    @PostMapping("/session/{sessionId}/select-plan")
    public WorkflowSession selectPlan(@PathVariable String sessionId, @RequestBody PlanSelection body) {
        return workflowService.selectPlan(sessionId, body.taskIndex, body.framework);
    }

    @PostMapping("/session/{sessionId}/generate-code")
    public ResponseEntity<Map<String, String>> generateCode(@PathVariable String sessionId) {
        String taskId = taskManager.submit(sessionId, "codegen",
                () -> { workflowService.runCodegen(sessionId); return null; });
        return ResponseEntity.ok(Map.of("ok", "true", "message", "代码生成已启动", "taskId", taskId));
    }

    // ── 步骤 8: 评估与反馈 ──

    @PostMapping("/session/{sessionId}/evaluate")
    public WorkflowSession runEvaluate(@PathVariable String sessionId) {
        return workflowService.runEvaluate(sessionId);
    }

    @PostMapping("/session/{sessionId}/feedback")
    public WorkflowSession submitFeedback(@PathVariable String sessionId, @RequestBody FeedbackRequest body) {
        return workflowService.submitFeedback(sessionId, body.rating, body.score, body.comment);
    }

    @GetMapping("/feedback/stats")
    public Map<String, Object> feedbackStats() {
        return workflowService.getFeedbackStats();
    }

    // ── SSE 事件流 ──

    @GetMapping(value = "/session/{sessionId}/events", produces = MediaType.TEXT_EVENT_STREAM_VALUE)
    public Flux<Map<String, Object>> sessionEvents(@PathVariable String sessionId) {
        return Flux.interval(Duration.ofSeconds(2))
                .map(tick -> workflowService.getSession(sessionId))
                .map(session -> Map.of(
                        "type", "state",
                        "data", (Object) session
                ))
                .takeUntil(data -> {
                    var s = (WorkflowSession) data.get("data");
                    var step = s.getStep();
                    return step == com.workflow.uniapp.enums.WorkflowStep.DONE
                            || step == com.workflow.uniapp.enums.WorkflowStep.ERROR
                            || step == com.workflow.uniapp.enums.WorkflowStep.EVALUATE
                            || step == com.workflow.uniapp.enums.WorkflowStep.SELECT_PLAN
                            || step == com.workflow.uniapp.enums.WorkflowStep.REVIEW_REQUIREMENT;
                });
    }

    // ── 健康检查 ──

    @GetMapping("/health")
    public Map<String, Object> health() {
        return Map.of("status", "ok", "version", "2.0.0");
    }

    // ── 请求 DTO ──

    @Data
    static class StartRequest {
        @NotBlank @Size(min = 1, max = 60) String appName;
        @NotBlank @Size(min = 1, max = 40) @Pattern(regexp = "^[a-z0-9_-]+$") String projectDir;
        String llmModel;
    }

    @Data
    static class StyleSelection {
        @NotEmpty List<String> styleIds;
        String colors;
    }

    @Data
    static class PlanSelection {
        @Min(0) int taskIndex;
        String framework;
    }

    @Data
    static class FeedbackRequest {
        String rating;
        int score;
        String comment;
    }

    private List<Map<String, String>> buildStyleList(StyleSelection body) {
        // 与 Python 版 set_styles 逻辑一致，映射 style_id → name + color
        return body.styleIds.stream().map(id -> Map.of("id", id, "name", id, "color", "")).toList();
    }
}
