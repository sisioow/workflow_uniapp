package com.workflow.uniapp.entity;

import jakarta.persistence.*;
import lombok.*;
import java.time.LocalDateTime;

/** 用户反馈实体 — 第 8 步评估反馈 */
@Entity
@Table(name = "user_feedback")
@Getter @Setter @NoArgsConstructor @AllArgsConstructor @Builder
public class UserFeedback {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "session_id", nullable = false, length = 64)
    private String sessionId;

    /** good | ok | bad */
    @Column(name = "rating", length = 10)
    private String rating;

    /** 1-5 星评分 */
    @Column(name = "score")
    private Integer score;

    @Column(name = "comment", columnDefinition = "TEXT")
    private String comment;

    @Column(name = "quality_score")
    private Integer qualityScore;

    @Column(name = "quality_report_json", columnDefinition = "TEXT")
    private String qualityReportJson;

    @Column(name = "created_at", nullable = false, updatable = false)
    private LocalDateTime createdAt;

    @PrePersist
    void onCreate() { this.createdAt = LocalDateTime.now(); }
}
