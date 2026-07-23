package com.workflow.uniapp.service;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.workflow.uniapp.dto.*;
import com.workflow.uniapp.entity.*;
import com.workflow.uniapp.enums.WorkflowStep;
import com.workflow.uniapp.integration.codex.CodexClient;
import com.workflow.uniapp.integration.llm.LlmClient;
import com.workflow.uniapp.integration.stitch.StitchClient;
import com.workflow.uniapp.repository.*;
import com.workflow.uniapp.workflow.quality.QualityEngine;
import com.workflow.uniapp.workflow.state.WorkflowStateMachine;
import jakarta.transaction.Transactional;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;

import java.time.LocalDateTime;
import java.util.*;

/**
 * 工作流编排服务 — 核心业务逻辑。
 * 对应 Python 版 WorkflowOrchestrator + run_* 函数。
 */
@Slf4j
@Service
@RequiredArgsConstructor
public class WorkflowService {

    private final WorkflowSessionRepository sessionRepo;
    private final WorkflowLogRepository logRepo;
    private final StyleTaskRepository styleTaskRepo;
    private final QualityCheckRepository qualityCheckRepo;
    private final UserFeedbackRepository feedbackRepo;
    private final ErrorStatRepository errorStatRepo;

    private final LlmClient llmClient;
    private final StitchClient stitchClient;
    private final CodexClient codexClient;

    private final WorkflowStateMachine stateMachine;
    private final QualityEngine qualityEngine;

    private final ObjectMapper objectMapper;

    // ═══════════════════════════════════════════════
    // 步骤 1-2: 启动 → 需求分析
    // ═══════════════════════════════════════════════

    @Transactional
    public WorkflowSession start(String appName, String projectDir, String llmModel) {
        String sessionId = UUID.randomUUID().toString().replace("-", "").substring(0, 12);

        var session = WorkflowSession.builder()
                .sessionId(sessionId)
                .appName(appName.trim())
                .projectDir(projectDir.trim())
                .llmModel(llmModel != null ? llmModel.trim() : "")
                .step(WorkflowStep.ANALYZE)
                .build();
        session = sessionRepo.save(session);
        appendLog(sessionId, "开始需求分析：" + appName.trim());

        // LLM 需求分析
        RequirementPlan plan = llmClient.analyzeRequirement(appName.trim());
        try {
            session.setRequirementJson(objectMapper.writeValueAsString(plan));
        } catch (JsonProcessingException e) {
            log.error("序列化需求方案失败", e);
        }

        session.setStep(WorkflowStep.REVIEW_REQUIREMENT);
        session = sessionRepo.save(session);
        appendLog(sessionId, "需求分析完成，请查看并修改需求方案");
        return session;
    }

    @Transactional
    public WorkflowSession retryAnalyze(String sessionId) {
        var session = getOrThrow(sessionId);
        stateMachine.assertCanTransition(session.getStep(), WorkflowStep.ANALYZE);

        session.setStep(WorkflowStep.ANALYZE);
        session.setError(null);
        session.setErrorStep(null);
        sessionRepo.save(session);
        appendLog(sessionId, "🔄 重新开始需求分析…");

        RequirementPlan plan = llmClient.analyzeRequirement(session.getAppName());
        try {
            session.setRequirementJson(objectMapper.writeValueAsString(plan));
        } catch (JsonProcessingException e) {
            log.error("序列化需求方案失败", e);
        }

        session.setStep(WorkflowStep.REVIEW_REQUIREMENT);
        sessionRepo.save(session);
        appendLog(sessionId, "需求分析完成");
        return session;
    }

    // ═══════════════════════════════════════════════
    // 步骤 3: 需求审核
    // ═══════════════════════════════════════════════

    @Transactional
    public WorkflowSession updateRequirement(String sessionId, Map<String, Object> updates) {
        var session = getOrThrow(sessionId);
        if (session.getStep() != WorkflowStep.REVIEW_REQUIREMENT && session.getStep() != WorkflowStep.SELECT_STYLES) {
            throw new IllegalStateException("当前状态不允许修改需求方案");
        }

        if (updates.containsKey("appName") && updates.get("appName") != null) {
            session.setAppName(((String) updates.get("appName")).trim());
        }
        // 更新需求方案 JSON 中的字段
        if (session.getRequirementJson() != null) {
            try {
                @SuppressWarnings("unchecked")
                var map = objectMapper.readValue(session.getRequirementJson(), Map.class);
                if (updates.containsKey("summary")) map.put("summary", updates.get("summary"));
                if (updates.containsKey("pages")) map.put("pages", updates.get("pages"));
                session.setRequirementJson(objectMapper.writeValueAsString(map));
            } catch (JsonProcessingException e) {
                log.error("更新需求方案失败", e);
            }
        }

        sessionRepo.save(session);
        appendLog(sessionId, "需求方案已更新");
        return session;
    }

    @Transactional
    public WorkflowSession confirmRequirement(String sessionId) {
        var session = getOrThrow(sessionId);
        stateMachine.assertCanTransition(session.getStep(), WorkflowStep.SELECT_STYLES);

        session.setStep(WorkflowStep.SELECT_STYLES);
        sessionRepo.save(session);
        appendLog(sessionId, "需求方案已确认，请选择 UI 风格");
        return session;
    }

    // ═══════════════════════════════════════════════
    // 步骤 4: 选择风格
    // ═══════════════════════════════════════════════

    @Transactional
    public WorkflowSession setStyles(String sessionId, List<Map<String, String>> styles) {
        var session = getOrThrow(sessionId);
        try {
            session.setSelectedStylesJson(objectMapper.writeValueAsString(styles));
        } catch (JsonProcessingException e) {
            log.error("序列化风格选择失败", e);
        }
        session.setStep(WorkflowStep.DESIGN);
        sessionRepo.save(session);
        return session;
    }

    // ═══════════════════════════════════════════════
    // 步骤 5: 设计生成 or 跳过设计
    // ═══════════════════════════════════════════════

    @Transactional
    public WorkflowSession skipDesign(String sessionId) {
        var session = getOrThrow(sessionId);
        stateMachine.assertCanTransition(session.getStep(), WorkflowStep.INIT_PROJECT);

        // 创建虚拟 StyleTask
        List<Map<String, String>> selectedStyles = parseSelectedStyles(session);
        for (var style : selectedStyles) {
            var task = StyleTask.builder()
                    .sessionId(sessionId)
                    .styleId(style.get("id"))
                    .styleName(style.get("name"))
                    .colorHint(style.getOrDefault("color", ""))
                    .status("done")
                    .skipDesign(true)
                    .build();
            styleTaskRepo.save(task);
        }

        session.setStep(WorkflowStep.INIT_PROJECT);
        session.setSelectedTaskIndex(0);
        sessionRepo.save(session);
        appendLog(sessionId, "跳过 Stitch 设计，直接进入代码生成阶段");
        return session;
    }

    /** 设计生成在后台异步执行，此方法只启动任务 */
    public String startDesignAsync(String sessionId) {
        var session = getOrThrow(sessionId);
        stateMachine.assertCanTransition(session.getStep(), WorkflowStep.DESIGN);

        // 后台任务由 TaskManager 管理，这里返回确认
        appendLog(sessionId, "【开始设计生成】");
        return sessionId; // TaskManager.submit(sessionId, "design", () -> runDesign(sessionId))
    }

    /** 同步执行设计生成（由 TaskManager 调用） */
    @Transactional
    public void runDesign(String sessionId) {
        var session = getOrThrow(sessionId);
        appendLog(sessionId, "初始化 Stitch MCP...");

        try {
            stitchClient.initialize();
            appendLog(sessionId, "✅ Stitch MCP 初始化成功");
        } catch (Exception e) {
            markError(session, WorkflowStep.DESIGN, "stitch_init_failed", "Stitch 初始化失败: " + e.getMessage());
            return;
        }

        List<Map<String, String>> selectedStyles = parseSelectedStyles(session);
        int doneCount = 0;

        for (int idx = 0; idx < selectedStyles.size(); idx++) {
            var style = selectedStyles.get(idx);
            String styleId = style.get("id");
            String styleName = style.get("name");
            String colorHint = style.getOrDefault("color", "");

            appendLog(sessionId, String.format("\n【%d/%d】处理风格: %s", idx + 1, selectedStyles.size(), styleName));

            var task = StyleTask.builder()
                    .sessionId(sessionId)
                    .styleId(styleId)
                    .styleName(styleName)
                    .colorHint(colorHint)
                    .status("running")
                    .build();
            task = styleTaskRepo.save(task);

            try {
                String projectTitle = session.getAppName() + "+" + styleId;
                appendLog(sessionId, "  创建 Stitch 项目: " + projectTitle);

                String projectId = stitchClient.createProject(projectTitle);
                task.setProjectId(projectId);
                styleTaskRepo.save(task);
                appendLog(sessionId, "  ✅ 项目创建成功: " + projectId);

                // 生成 4 个页面
                for (String key : List.of("page1", "page2", "page3", "page4")) {
                    appendLog(sessionId, "    生成 " + key + "...");
                    String prompt = buildScreenPrompt(session, key, styleName, colorHint);
                    try {
                        String screenId = stitchClient.generateScreen(projectId, prompt);
                        appendLog(sessionId, "    ✅ " + key + " 生成成功: " + screenId);
                    } catch (Exception e) {
                        appendLog(sessionId, "    ❌ " + key + " 生成失败: " + e.getMessage());
                        throw e;
                    }
                }

                task.setStatus("done");
                styleTaskRepo.save(task);
                doneCount++;
                appendLog(sessionId, "  ✅ 风格「" + styleName + "」设计完成");

            } catch (Exception e) {
                task.setStatus("failed");
                task.setError(e.getMessage());
                styleTaskRepo.save(task);
                appendLog(sessionId, "  ❌ 风格「" + styleName + "」失败: " + e.getMessage());
            }
        }

        int failedCount = selectedStyles.size() - doneCount;
        appendLog(sessionId, "\n【设计阶段总结】");
        appendLog(sessionId, "成功: " + doneCount + "/" + selectedStyles.size());
        appendLog(sessionId, "失败: " + failedCount + "/" + selectedStyles.size());

        if (doneCount == 0) {
            markError(session, WorkflowStep.DESIGN, "all_styles_failed", "所有风格设计均失败");
        } else {
            session = getOrThrow(sessionId); // 重新加载
            session.setStep(WorkflowStep.SELECT_PLAN);
            sessionRepo.save(session);
            appendLog(sessionId, "✅ 设计阶段完成，请选择最终方案");
        }
    }

    // ═══════════════════════════════════════════════
    // 步骤 6-7: 选择方案 → 项目初始化 → 代码生成
    // ═══════════════════════════════════════════════

    @Transactional
    public WorkflowSession selectPlan(String sessionId, int taskIndex, String framework) {
        var session = getOrThrow(sessionId);
        var tasks = styleTaskRepo.findBySessionId(sessionId);

        if (taskIndex < 0 || taskIndex >= tasks.size()) throw new IllegalArgumentException("无效的方案序号");
        if (!"done".equals(tasks.get(taskIndex).getStatus())) throw new IllegalArgumentException("所选方案未完成设计");

        session.setSelectedTaskIndex(taskIndex);
        session.setFramework(framework != null ? framework : "vue");
        session.setStep(WorkflowStep.INIT_PROJECT);
        sessionRepo.save(session);
        return session;
    }

    /** 代码生成在后台异步执行 */
    public String startCodegenAsync(String sessionId) {
        return sessionId; // TaskManager.submit(sessionId, "codegen", () -> runCodegen(sessionId))
    }

    /** 同步执行项目初始化 + 代码生成（由 TaskManager 调用） */
    @Transactional
    public void runCodegen(String sessionId) {
        var session = getOrThrow(sessionId);
        var task = styleTaskRepo.findBySessionId(sessionId).stream()
                .skip(session.getSelectedTaskIndex()).findFirst().orElseThrow();

        // 项目初始化
        String projectPath = codexClient.initProject(session.getProjectDir());
        session.setProjectPath(projectPath);
        sessionRepo.save(session);
        appendLog(sessionId, "项目已初始化：" + projectPath);

        // 构建 prompt 并执行 Codex
        String prompt = buildCodexPrompt(session, task);
        appendLog(sessionId, "启动 Codex 代码生成 …");

        try {
            String output = codexClient.execute(projectPath, prompt);
            for (String line : output.lines().toList()) {
                appendLog(sessionId, "  codex: " + line);
            }
        } catch (CodexClient.CodexTimeoutException e) {
            markError(session, WorkflowStep.CODEGEN, "codex_timeout", "Codex 执行超时（>1h）");
            return;
        } catch (CodexClient.CodexExitException e) {
            markError(session, WorkflowStep.CODEGEN, "codex_nonzero_exit", e.getMessage());
            return;
        } catch (Exception e) {
            markError(session, WorkflowStep.CODEGEN, "codex_not_found", e.getMessage());
            return;
        }

        // 自动质量评估
        appendLog(sessionId, "代码生成完成，请评估结果");
        runEvaluate(sessionId);
    }

    // ═══════════════════════════════════════════════
    // 步骤 8: 评估与反馈
    // ═══════════════════════════════════════════════

    @Transactional
    public WorkflowSession runEvaluate(String sessionId) {
        var session = getOrThrow(sessionId);
        appendLog(sessionId, "\n【第 8 步：质量评估】");

        var report = qualityEngine.runChecks(session.getProjectPath(), sessionId);
        session.setQualityScore(report.getScore());
        session.setStep(WorkflowStep.EVALUATE);
        sessionRepo.save(session);

        for (var check : report.getChecks()) {
            String icon = check.isPassed() ? "✅" : "❌";
            appendLog(sessionId, "  " + icon + " " + check.getLabel() + ": " + check.getDetail());
        }
        appendLog(sessionId, String.format("\n质量评分：%d/100（%d/%d 通过）",
                report.getScore(), report.getPassedCount(), report.getTotalCount()));

        // 改进建议
        var hints = qualityEngine.getImprovementHints();
        if (!hints.isEmpty()) {
            appendLog(sessionId, "\n📋 改进建议：");
            hints.forEach(h -> appendLog(sessionId, "  " + h));
        }

        return session;
    }

    @Transactional
    public WorkflowSession submitFeedback(String sessionId, String rating, int score, String comment) {
        var session = getOrThrow(sessionId);

        var fb = UserFeedback.builder()
                .sessionId(sessionId)
                .rating(rating)
                .score(score)
                .comment(comment)
                .build();
        feedbackRepo.save(fb);

        String emoji = switch (rating) {
            case "good" -> "👍";
            case "bad" -> "👎";
            default -> "👌";
        };
        appendLog(sessionId, "\n用户反馈：" + emoji + " " + rating);
        if (comment != null && !comment.isBlank()) {
            appendLog(sessionId, "反馈内容：" + comment);
        }

        session.setStep(WorkflowStep.DONE);
        sessionRepo.save(session);
        appendLog(sessionId, "✅ 工作流完成");
        return session;
    }

    // ═══════════════════════════════════════════════
    // 辅助方法
    // ═══════════════════════════════════════════════

    private void appendLog(String sessionId, String message) {
        logRepo.save(WorkflowLog.builder().sessionId(sessionId).message(message).build());
    }

    private void markError(WorkflowSession session, WorkflowStep errorStep, String errorType, String detail) {
        session.setStep(WorkflowStep.ERROR);
        session.setErrorStep(errorStep.name().toLowerCase());
        session.setError(detail);
        sessionRepo.save(session);
        appendLog(session.getSessionId(), "❌ " + detail);
        errorStatRepo.increment(errorStep.name().toLowerCase() + ":" + errorType, detail);
    }

    private WorkflowSession getOrThrow(String sessionId) {
        return sessionRepo.findById(sessionId)
                .orElseThrow(() -> new IllegalArgumentException("会话不存在: " + sessionId));
    }

    @SuppressWarnings("unchecked")
    private List<Map<String, String>> parseSelectedStyles(WorkflowSession session) {
        if (session.getSelectedStylesJson() == null) return List.of();
        try {
            return objectMapper.readValue(session.getSelectedStylesJson(), List.class);
        } catch (JsonProcessingException e) {
            log.error("解析选中风格失败", e);
            return List.of();
        }
    }

    private String buildScreenPrompt(WorkflowSession session, String pageKey, String styleName, String colorHint) {
        // 与 Python 版 build_screen_prompt 一致
        RequirementPlan plan = parseRequirement(session);
        var page = plan.getPages().stream().filter(p -> p.getKey().equals(pageKey)).findFirst()
                .orElse(plan.getPages().get(0));

        String features = String.join("、", page.getFeatures() != null ? page.getFeatures() : List.of("核心功能"));
        String colorLine = (colorHint != null && !colorHint.isBlank()) ? "；主色调：" + colorHint : "";

        return String.join("\n",
                "【产品方案】",
                "产品定位：" + plan.getSummary(),
                "页面：" + page.getTitle() + "（" + pageKey + "）",
                "功能点：" + features,
                "交互说明：" + (page.getDescription() != null ? page.getDescription() : "布局优美、强调可操作"),
                "",
                "【UI 风格要求】",
                "风格主题：" + styleName + colorLine,
                "设备类型：移动端（375px 宽度）",
                "语言：中文",
                "",
                "【设计约束】",
                "- 纯前端可实现",
                "- 无个人中心、登录、注册等通用页面",
                "- 强调交互可操作性",
                "- 保持 " + styleName + " 风格高度统一"
        );
    }

    private String buildCodexPrompt(WorkflowSession session, StyleTask task) {
        RequirementPlan plan = parseRequirement(session);
        boolean hasDesigns = task.getProjectId() != null && !task.getProjectId().isBlank();
        boolean isSkip = task.isSkipDesign();

        var lines = new ArrayList<String>();
        lines.add("【UI 开发任务 - 4 页面】");
        lines.add("应用名称：" + session.getAppName());
        lines.add("当前风格：" + task.getStyleName());
        lines.add("配色：" + (task.getColorHint() != null ? task.getColorHint() : "默认"));

        if (hasDesigns && !isSkip) {
            lines.add("");
            lines.add("参考设计稿（4 个一级页面）：");
            for (int i = 0; i < 4; i++) {
                String key = "page" + (i + 1);
                var page = i < plan.getPages().size() ? plan.getPages().get(i) : null;
                String title = page != null ? page.getTitle() : key;
                lines.add("- " + key + " " + title + " -> stitch screen_id: (见 design 步骤)");
            }
            lines.addAll(List.of("", "任务要求：",
                    "1. 通过 stitch mcp 的 get_screen 获取设计稿，下载至 static/stitch",
                    "2. 参考设计稿在 /pages 下实现 page1~page4 完整 UI 与交互",
                    "3. 同步调整基础页面 UI，保持风格统一",
                    "4. 自测并修复问题"));
        } else {
            lines.addAll(List.of("", "页面清单（自行设计并实现 4 个页面）："));
            for (int i = 0; i < 4; i++) {
                String key = "page" + (i + 1);
                var page = i < plan.getPages().size() ? plan.getPages().get(i) : null;
                if (page != null) {
                    String feats = String.join("、", page.getFeatures() != null ? page.getFeatures() : List.of());
                    lines.add("- " + key + " " + page.getTitle() + " — 功能：" + feats + " / 交互：" + page.getDescription());
                }
            }
            lines.addAll(List.of("",
                    "UI 风格要求：" + task.getStyleName() + (task.getColorHint() != null ? "；主色调：" + task.getColorHint() : ""),
                    "设备类型：移动端（375px 宽度）", "语言：中文",
                    "", "任务要求：",
                    "1. 根据页面清单和风格要求，自行设计并实现 4 个页面",
                    "2. 保持 " + task.getStyleName() + " 风格高度统一",
                    "3. 同步调整基础页面 UI", "4. 自测修复"));
        }
        return String.join("\n", lines);
    }

    private RequirementPlan parseRequirement(WorkflowSession session) {
        if (session.getRequirementJson() == null) return new RequirementPlan();
        try {
            return objectMapper.readValue(session.getRequirementJson(), RequirementPlan.class);
        } catch (JsonProcessingException e) {
            log.error("解析需求方案失败", e);
            return new RequirementPlan();
        }
    }
}
