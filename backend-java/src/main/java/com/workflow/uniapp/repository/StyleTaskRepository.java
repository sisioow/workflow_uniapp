package com.workflow.uniapp.repository;

import com.workflow.uniapp.entity.StyleTask;
import org.springframework.data.jpa.repository.JpaRepository;
import java.util.List;

interface StyleTaskRepository extends JpaRepository<StyleTask, Long> {
    List<StyleTask> findBySessionIdOrderByCreatedAtAsc(String sessionId);
    void deleteBySessionId(String sessionId);
}
