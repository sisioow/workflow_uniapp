package com.workflow.uniapp.repository;

import com.workflow.uniapp.entity.WorkflowLog;
import org.springframework.data.jpa.repository.JpaRepository;
import java.util.List;

interface WorkflowLogRepository extends JpaRepository<WorkflowLog, Long> {
    List<WorkflowLog> findBySessionIdOrderByCreatedAtAsc(String sessionId);
    void deleteBySessionId(String sessionId);
}
