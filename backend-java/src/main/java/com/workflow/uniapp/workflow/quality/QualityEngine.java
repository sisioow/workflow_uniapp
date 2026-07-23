package com.workflow.uniapp.workflow.quality;

import com.workflow.uniapp.entity.*;
import com.workflow.uniapp.repository.*;
import lombok.*;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Component;

import java.nio.file.*;
import java.util.*;

/**
 * 质量评估引擎 — 对应 Python 版 quality.py。
 */
@Slf4j
@Component
@RequiredArgsConstructor
public class QualityEngine {

    private final QualityCheckRepository checkRepo;
    private final UserFeedbackRepository feedbackRepo;

    public QualityReport runChecks(String projectPath, String sessionId) {
        var checks = new ArrayList<QualityCheck>();
        if (projectPath == null || projectPath.isBlank()) {
            return new QualityReport(checks, 0);
        }

        Path base = Path.of(projectPath);
        List<String> pageKeys = List.of("page1", "page2", "page3", "page4");

        // 检查 4 个页面文件
        for (String key : pageKeys) {
            Path vueFile = base.resolve("pages").resolve(key).resolve(key + ".vue");
            if (Files.exists(vueFile)) {
                long size = 0;
                try { size = Files.size(vueFile); } catch (Exception ignored) {}
                boolean passed = size > 100; // >0.1KB
                checks.add(saveCheck(sessionId, key + ".vue 文件存在", passed,
                        passed ? String.format("%.1f KB", size / 1024.0) : "文件为空"));
            } else {
                checks.add(saveCheck(sessionId, key + ".vue 文件存在", false, "文件缺失"));
            }
        }

        // 检查 pages.json
        Path pagesJson = base.resolve("pages.json");
        if (Files.exists(pagesJson)) {
            try {
                String content = Files.readString(pagesJson);
                for (String key : pageKeys) {
                    String expected = "pages/" + key + "/" + key;
                    boolean registered = content.contains(expected);
                    checks.add(saveCheck(sessionId, key + " 路由已注册", registered,
                            registered ? "已注册" : "未注册"));
                }
            } catch (Exception e) {
                checks.add(saveCheck(sessionId, "pages.json 解析", false, "解析失败: " + e.getMessage()));
            }
        } else {
            checks.add(saveCheck(sessionId, "pages.json 存在", false, "文件缺失"));
        }

        int total = checks.size();
        int passed = (int) checks.stream().filter(QualityCheck::isPassed).count();
        int score = total > 0 ? Math.round(passed * 100f / total) : 0;

        return new QualityReport(checks, score);
    }

    public List<String> getImprovementHints() {
        // 从全局反馈数据统计改进建议
        var hints = new ArrayList<String>();
        // 可扩展：从 errorStat 表读取统计
        return hints;
    }

    private QualityCheck saveCheck(String sessionId, String label, boolean passed, String detail) {
        var check = QualityCheck.builder()
                .sessionId(sessionId).label(label).passed(passed).detail(detail).build();
        return checkRepo.save(check);
    }

    @Data
    @AllArgsConstructor
    public static class QualityReport {
        private List<QualityCheck> checks;
        private int score;
        public int getPassedCount() { return (int) checks.stream().filter(QualityCheck::isPassed).count(); }
        public int getTotalCount() { return checks.size(); }
    }
}
