package com.workflow.uniapp.integration.codex;

import com.workflow.uniapp.config.PathsConfig;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Component;

import java.io.IOException;
import java.nio.file.*;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.TimeUnit;

/**
 * Codex CLI 封装 — 对应 Python 版 run_codegen / run_init_project。
 */
@Slf4j
@Component
@RequiredArgsConstructor
public class CodexClient {

    private final CodexConfig codexConfig;
    private final PathsConfig pathsConfig;

    /** 拷贝 uni-app 模板到输出目录 */
    public String initProject(String projectDir) {
        Path template = Path.of(pathsConfig.getUniappTemplateDir());
        Path dest = Path.of(pathsConfig.getUniappOutputDir(), projectDir);

        if (!Files.exists(template)) throw new CodexException("模板目录不存在: " + template);
        try {
            if (Files.exists(dest)) deleteRecursively(dest);
            copyRecursively(template, dest);
        } catch (IOException e) {
            throw new CodexException("项目初始化失败: " + e.getMessage(), e);
        }
        return dest.toString();
    }

    /** 执行 Codex 代码生成 */
    public String execute(String projectPath, String prompt) throws CodexTimeoutException, CodexExitException {
        List<String> cmd = new ArrayList<>(List.of(
                codexConfig.getBin(), "exec",
                "-C", projectPath,
                "--skip-git-repo-check",
                "--sandbox", codexConfig.getSandbox()
        ));
        if (codexConfig.getModel() != null && !codexConfig.getModel().isBlank()) {
            cmd.addAll(List.of("-m", codexConfig.getModel()));
        }
        cmd.add(prompt);

        log.info("启动 Codex: {}", String.join(" ", cmd.subList(0, Math.min(7, cmd.size()))));

        try {
            var proc = new ProcessBuilder(cmd)
                    .redirectErrorStream(true)
                    .directory(Path.of(projectPath).toFile())
                    .start();

            boolean finished = proc.waitFor(codexConfig.getTimeoutMinutes(), TimeUnit.MINUTES);
            if (!finished) {
                proc.destroyForcibly();
                throw new CodexTimeoutException("Codex 执行超时（>" + codexConfig.getTimeoutMinutes() + "min）");
            }

            String output = new String(proc.getInputStream().readAllBytes());
            if (proc.exitValue() != 0) {
                String err = output.length() > 500 ? output.substring(output.length() - 500) : output;
                throw new CodexExitException("Codex 退出码 " + proc.exitValue() + ": " + err);
            }
            return output;

        } catch (CodexTimeoutException | CodexExitException e) { throw e;
        } catch (IOException e) {
            throw new CodexException("未找到 Codex 可执行文件: " + codexConfig.getBin(), e);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new CodexException("Codex 执行被中断", e);
        }
    }

    private void deleteRecursively(Path path) throws IOException {
        try (var files = Files.walk(path)) {
            files.sorted(java.util.Comparator.reverseOrder()).forEach(p -> {
                try { Files.deleteIfExists(p); } catch (IOException ignored) {}
            });
        }
    }

    private void copyRecursively(Path src, Path dest) throws IOException {
        try (var files = Files.walk(src)) {
            files.forEach(source -> {
                try {
                    Path target = dest.resolve(src.relativize(source));
                    if (Files.isDirectory(source)) Files.createDirectories(target);
                    else Files.copy(source, target, StandardCopyOption.REPLACE_EXISTING);
                } catch (IOException e) { throw new RuntimeException(e); }
            });
        }
    }

    public static class CodexException extends RuntimeException {
        public CodexException(String msg) { super(msg); }
        public CodexException(String msg, Throwable cause) { super(msg, cause); }
    }

    public static class CodexTimeoutException extends CodexException {
        public CodexTimeoutException(String msg) { super(msg); }
    }

    public static class CodexExitException extends CodexException {
        public CodexExitException(String msg) { super(msg); }
    }
}
