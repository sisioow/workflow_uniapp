package com.workflow.uniapp.entity;

import jakarta.persistence.*;
import lombok.*;

@Entity
@Table(name = "quality_check")
@Data
@NoArgsConstructor
@AllArgsConstructor
@Builder
public class QualityCheck {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "session_id", length = 12, nullable = false)
    private String sessionId;

    @Column(length = 200, nullable = false)
    private String label;

    @Column(nullable = false)
    private boolean passed;

    @Column(length = 500)
    private String detail;
}
