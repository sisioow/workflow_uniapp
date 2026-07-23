package com.workflow.uniapp.workflow.state;

import com.workflow.uniapp.enums.WorkflowStep;
import org.springframework.stereotype.Component;

/**
 * 工作流状态机 — 步骤转换规则。
 * 所有步骤跳转都通过此组件校验，防止非法状态转移。
 */
@Component
public class WorkflowStateMachine {

    public void assertCanTransition(WorkflowStep from, WorkflowStep to) {
        if (!from.canTransitionTo(to)) {
            throw new IllegalStateException(
                    String.format("状态转移非法: %s → %s", from, to));
        }
    }
}
