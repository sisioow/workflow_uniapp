package com.workflow.uniapp.entity;

import jakarta.persistence.*;
import lombok.*;

import java.time.LocalDateTime;

/** 错误统计实体 — 反馈层，记录各类错误频率 */
@Entity
@Table(name = "error_stat")
@Getter @Setter @NoArgsConstructor @AllArgsConstructor @Builder
public class ErrorStat {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "error_key", nullable = false, length = 100, unique = true)
    private String errorKey;

    @Column(name = "count")
    @Builder.Default
    private Integer count = 0;

    @Column(name = "last_seen")
    private LocalDateTime lastSeen;

    @Column(name = "sample", length = 500)
    private String sample;
}
