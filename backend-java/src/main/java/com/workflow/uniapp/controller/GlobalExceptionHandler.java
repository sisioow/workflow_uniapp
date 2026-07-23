package com.workflow.uniapp.controller;

import com.workflow.uniapp.enums.WorkflowStep;
import com.workflow.uniapp.service.WorkflowService;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.*;

import java.util.Map;
import java.util.HashMap;

/** 通用异常处理 — 统一错误响应格式 */
@RestControllerAdvice
@RequiredArgsConstructor
public class GlobalExceptionHandler {

    @ExceptionHandler(IllegalArgumentException.class)
    public Map<String, Object> handleIllegal(IllegalArgumentException e) {
        Map<String, Object> body = new HashMap<>();
        body.put("error", "Bad Request");
        body.put("detail", e.getMessage());
        return body;
    }

    @ExceptionHandler(IllegalStateException.class)
    public Map<String, Object> handleIllegalState(IllegalStateException e) {
        Map<String, Object> body = new HashMap<>();
        body.put("error", "State Error");
        body.put("detail", e.getMessage());
        return body;
    }

    @ExceptionHandler(Exception.class)
    public Map<String, Object> handleGeneral(Exception e) {
        Map<String, Object> body = new HashMap<>();
        body.put("error", "Internal Server Error");
        body.put("detail", e.getMessage());
        return body;
    }
}
