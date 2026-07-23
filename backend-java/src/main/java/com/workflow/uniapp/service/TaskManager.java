package com.workflow.uniapp.service;

import jakarta.annotation.PreDestroy;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Component;

import java.util.Map;
import java.util.UUID;
import java.util.concurrent.*;

/**
 * 后台任务管理器 — 管理设计生成、代码生成等长时间运行任务。
 */
@Slf4j
@Component
public class TaskManager {

    private final ExecutorService executor = Executors.newFixedThreadPool(4);
    private final Map<String, Future<?>> tasks = new ConcurrentHashMap<>();

    /** 提交后台任务，返回 taskId */
    public String submit(String sessionId, String step, Runnable runnable) {
        String taskId = sessionId + "-" + step + "-" + UUID.randomUUID().toString().substring(0, 6);

        // 取消同 session+step 的旧任务
        tasks.keySet().stream()
                .filter(k -> k.startsWith(sessionId + "-" + step))
                .findFirst()
                .ifPresent(this::cancel);

        Future<?> future = executor.submit(() -> {
            try {
                log.info("Task {} started", taskId);
                runnable.run();
                log.info("Task {} completed", taskId);
            } catch (Exception e) {
                log.error("Task {} failed: {}", taskId, e.getMessage(), e);
            } finally {
                tasks.remove(taskId);
            }
        });

        tasks.put(taskId, future);
        return taskId;
    }

    public void cancel(String taskId) {
        Future<?> f = tasks.remove(taskId);
        if (f != null) {
            log.info("Cancelling task {}", taskId);
            f.cancel(true);
        }
    }

    @PreDestroy
    public void shutdown() {
        log.info("Shutting down TaskManager...");
        tasks.forEach((id, f) -> f.cancel(true));
        executor.shutdownNow();
    }
}
