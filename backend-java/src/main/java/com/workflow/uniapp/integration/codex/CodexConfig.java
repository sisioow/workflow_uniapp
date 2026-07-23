package com.workflow.uniapp.integration.codex;

import lombok.Data;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.stereotype.Component;

@Data
@Component
@ConfigurationProperties(prefix = "workflow.codex")
public class CodexConfig {
    private String bin = "codex";
    private String model = "";
    private String sandbox = "workspace-write";
    private int timeoutMinutes = 60;
}
