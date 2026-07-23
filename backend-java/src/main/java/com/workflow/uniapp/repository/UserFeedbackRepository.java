package com.workflow.uniapp.repository;

import com.workflow.uniapp.entity.UserFeedback;
import org.springframework.data.jpa.repository.JpaRepository;

interface UserFeedbackRepository extends JpaRepository<UserFeedback, Long> {
    UserFeedback findBySessionId(String sessionId);
}
