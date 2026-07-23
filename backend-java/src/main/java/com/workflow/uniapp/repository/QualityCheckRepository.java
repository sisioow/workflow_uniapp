package com.workflow.uniapp.repository;

import com.workflow.uniapp.entity.QualityCheck;
import org.springframework.data.jpa.repository.JpaRepository;
import java.util.List;

interface QualityCheckRepository extends JpaRepository<QualityCheck, Long> {
    List<QualityCheck> findBySessionId(String sessionId);
}
