package com.workflow.uniapp.repository;

import com.workflow.uniapp.entity.WorkflowSession;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.stereotype.Repository;

import java.util.List;

@Repository
public interface WorkflowSessionRepository extends JpaRepository<WorkflowSession, String> {
    List<WorkflowSession> findAllByOrderByUpdatedAtDesc();
}
