package com.workflow.uniapp.enums;

/** 工作流步骤枚举 — 单一真相源，包含转换规则 */
public enum WorkflowStep {
    INPUT,
    ANALYZE,
    REVIEW_REQUIREMENT,
    SELECT_STYLES,
    DESIGN,
    SELECT_PLAN,
    INIT_PROJECT,
    CODEGEN,
    EVALUATE,
    DONE,
    ERROR;

    /** 允许从当前步骤跳转到哪些步骤 */
    public boolean canTransitionTo(WorkflowStep target) {
        return switch (this) {
            case INPUT              -> target == ANALYZE;
            case ANALYZE            -> target == REVIEW_REQUIREMENT || target == ERROR;
            case REVIEW_REQUIREMENT -> target == SELECT_STYLES;
            case SELECT_STYLES      -> target == DESIGN || target == INIT_PROJECT;
            case DESIGN             -> target == SELECT_PLAN || target == ERROR;
            case SELECT_PLAN        -> target == INIT_PROJECT;
            case INIT_PROJECT       -> target == CODEGEN || target == ERROR;
            case CODEGEN            -> target == EVALUATE || target == ERROR;
            case EVALUATE           -> target == DONE;
            case ERROR              -> target == ANALYZE || target == DESIGN || target == CODEGEN;
            default                 -> false;
        };
    }

    /** 映射到前端面板名 */
    public String toPanelName() {
        return switch (this) {
            case INPUT              -> "input";
            case ANALYZE            -> "analyze";
            case REVIEW_REQUIREMENT -> "review";
            case SELECT_STYLES      -> "styles";
            case DESIGN             -> "design";
            case SELECT_PLAN        -> "plan";
            case INIT_PROJECT, CODEGEN, DONE, ERROR -> "codegen";
            case EVALUATE           -> "evaluate";
        };
    }
}
