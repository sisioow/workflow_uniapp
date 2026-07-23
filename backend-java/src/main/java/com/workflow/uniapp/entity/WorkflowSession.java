package com.workflow.uniapp.entity;

import com.workflow.uniapp.enums.WorkflowStep;
import jakarta.persistence.*;
import lombok.*;

import java.time.LocalDateTime;

@Entity
@Table(name = "workflow_session")
@Data
@NoArgsConstructor
@AllArgsConstructor
@Builder
public class WorkflowSession {

    @Id
    @Column(name = "session_id", length = 12)
    private String sessionId;

    @Column(name = "app_name", length = 60, nullable = false)
    private String appName;

    @Column(name = "project_dir", length = 40, nullable = false)
    private String projectDir;

    @Column(length = 20)
    @Builder.Default
    private String framework = "vue";

    @Column(name = "llm_model", length = 100)
    private String llmModel;

    @Enumerated(EnumType.STRING)
    @Column(length = 30, nullable = false)
    private WorkflowStep step;

    @Column(columnDefinition = "TEXT")
    private String error;

    @Column(name = "error_step", length = 30)
    private String errorStep;

    @Column(name = "project_path", length = 500)
    private String projectPath;

    @Column(name = "selected_task_index")
    @Builder.Default
    private int selectedTaskIndex = 0;

    @Column(name = "quality_score")
    @Builder.Default
    private int qualityScore = 0;

    /** 序列化后的需求方案 JSON */
    @Column(name = "requirement_json", columnDefinition = "MEDIUMTEXT")
    private String requirementJson;

    /** 序列化后的选中风格 JSON */
    @Column(name = "selected_styles_json", columnDefinition = "TEXT")
    private String selectedStylesJson;

    @Column(name = "created_at")
    private LocalDateTime createdAt;

    @Column(name = "updated_at")
    private LocalDateTime updatedAt;

    @PrePersist
    protected void onCreate() {
        createdAt = LocalDateTime.now();
        updatedAt = LocalDateTime.now();
    }

    @PreUpdate
    protected void onUpdate() {
        updatedAt = LocalDateTime.now();
    }
}
