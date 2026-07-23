package com.workflow.uniapp.dto;

import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.util.List;

/** 需求方案 DTO — LLM 输出的结构化 JSON 映射 */
@Data
@NoArgsConstructor
@AllArgsConstructor
@Builder
public class RequirementPlan {

    private String appName;
    private String summary;
    private List<PageDesign> pages;
    private List<String> sampleData;
    private String rawText;

    @Data
    @NoArgsConstructor
    @AllArgsConstructor
    @Builder
    public static class PageDesign {
        private String key;
        private String title;
        private List<String> features;
        private String description;
    }
}
