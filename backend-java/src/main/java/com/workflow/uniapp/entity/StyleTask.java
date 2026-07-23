package com.workflow.uniapp.entity;

import jakarta.persistence.*;
import lombok.*;

@Entity
@Table(name = "style_task")
@Data
@NoArgsConstructor
@AllArgsConstructor
@Builder
public class StyleTask {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "session_id", length = 12, nullable = false)
    private String sessionId;

    @Column(name = "style_id", length = 50, nullable = false)
    private String styleId;

    @Column(name = "style_name", length = 100, nullable = false)
    private String styleName;

    @Column(name = "color_hint", length = 50)
    private String colorHint;

    @Column(name = "project_id", length = 100)
    private String projectId;

    @Column(length = 20)
    @Builder.Default
    private String status = "pending";

    @Column(columnDefinition = "TEXT")
    private String error;

    @Builder.Default
    @Column(name = "skip_design")
    private boolean skipDesign = false;
}
