package com.workflow.uniapp.dto;

import lombok.Data;

@Data
public class PlanSelection {
    @Min(0)
    private int taskIndex;
    private String framework = "vue";
}
